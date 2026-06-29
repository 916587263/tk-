"""
浏览器生命周期管理器

职责:
  1. CDP 端口可用性检测 (复用已有浏览器)
  2. 自动清理冲突进程
  3. 启动新的 CDP 浏览器实例
  4. DevTools 就绪等待
  5. 退出时清理子进程

设计:
  - 依赖注入: BrowserAdapter + BrowserConfig 通过构造函数传入
  - 无全局状态: _process 是实例变量
  - platform-specific 逻辑委托给 BrowserAdapter
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

from .base import BrowserAdapter
from ..config import BrowserConfig


class BrowserManager:
    """CDP 浏览器生命周期管理器

    用法:
      adapter = EdgeAdapter()
      config = BrowserConfig()
      mgr = BrowserManager(adapter, config, project_root, logger)
      ok, msg = mgr.ensure_ready()
      # ... 使用 CDP 浏览器 ...
      mgr.shutdown()  # 或自动 atexit
    """

    def __init__(
        self,
        adapter: BrowserAdapter,
        config: BrowserConfig,
        project_root: Path,
        logger: logging.Logger,
    ):
        self._adapter = adapter
        self._config = config
        self._project_root = project_root
        self._log = logger

        self._process: Optional[subprocess.Popen] = None
        self._launched_by_us: bool = False
        self._shutdown_registered: bool = False

    # ── 公开 API ──

    def ensure_ready(self) -> tuple[bool, str]:
        """确保 CDP 浏览器就绪 (复用或启动)

        Returns:
            (success, message)
        """
        port = self._config.debug_port
        host = self._config.debug_host

        # ── Case 1: CDP 已就绪 → 复用 ──
        if self._config.reuse_existing and self._is_cdp_ready(host, port):
            self._log.info("检测到已有 CDP 浏览器 (端口 %s), 直接复用", port)
            return True, f"复用已有浏览器 (localhost:{port})"

        # ── Case 2/3: 需要启动新实例 ──
        if self._is_port_in_use(host, port):
            self._log.warning("端口 %s 被占用但 CDP 不可用, 尝试清理...", port)
            if self._config.kill_stale:
                self._kill_existing(port)

        return self._launch(port, host)

    def shutdown(self):
        """关闭由本管理器启动的浏览器进程"""
        if not self._launched_by_us or self._process is None:
            return

        self._log.info("正在关闭 CDP 浏览器...")
        try:
            if sys.platform == "win32":
                self._process.terminate()
            else:
                self._process.send_signal(signal.SIGTERM)
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._log.warning("浏览器未响应 SIGTERM, 强制终止")
                self._process.kill()
        except (OSError, ProcessLookupError):
            pass
        self._log.info("CDP 浏览器已关闭")

    # ── 内部方法 ──

    def _launch(self, port: int, host: str) -> tuple[bool, str]:
        """启动新的 CDP 浏览器实例"""
        # 查找可执行文件
        exe_path = None
        for p in self._adapter.executable_paths():
            if p.exists():
                exe_path = p
                break

        if exe_path is None:
            return False, f"找不到 {self._adapter.display_name()} 可执行文件"

        self._log.info("启动 %s: %s", self._adapter.display_name(), exe_path)

        # Profile 目录
        profile_dir = self._project_root / self._config.profile_dir_name
        profile_dir.mkdir(parents=True, exist_ok=True)

        # 构建命令行
        cmd = [str(exe_path)]
        cmd.extend(self._adapter.launch_args(
            port=port,
            profile_dir=profile_dir,
            extra=self._config.extra_args,
        ))
        cmd.append("about:blank")  # 打开空白页, 加速启动

        # 启动进程
        try:
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x08000000
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags if sys.platform == "win32" else 0,
            )
        except (FileNotFoundError, OSError) as e:
            return False, f"启动浏览器失败: {e}"

        self._launched_by_us = True
        self._register_shutdown()
        self._log.info("浏览器进程 PID=%s, 等待 DevTools 就绪...", self._process.pid)

        # 等待 DevTools
        if self._wait_for_cdp(host, port):
            return True, f"浏览器已启动 (localhost:{port})"
        else:
            return False, f"DevTools 启动超时 ({self._config.startup_timeout}s)"

    def _wait_for_cdp(self, host: str, port: int) -> bool:
        """轮询等待 DevTools HTTP 接口就绪"""
        deadline = time.time() + self._config.startup_timeout
        while time.time() < deadline:
            if self._is_cdp_ready(host, port):
                return True
            time.sleep(self._config.poll_interval)
        return False

    def _is_cdp_ready(self, host: str, port: int) -> bool:
        """HTTP 握手验证 CDP 可用"""
        try:
            url = f"http://{host}:{port}/json/version"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode())
                    return "Browser" in data
        except Exception:
            pass
        return False

    @staticmethod
    def _is_port_in_use(host: str, port: int) -> bool:
        """TCP 连接检测端口"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.3)
        try:
            sock.connect((host, port))
            sock.close()
            return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False

    def _kill_existing(self, port: int):
        """终止占用 CDP 端口的旧浏览器进程"""
        cmd = self._adapter.kill_command(port)
        self._log.info("执行清理命令: %s", " ".join(cmd))
        try:
            subprocess.run(cmd, capture_output=True, timeout=10)
            time.sleep(1.0)
        except Exception as e:
            self._log.warning("清理命令执行异常: %s", e)

    def _register_shutdown(self):
        """注册退出清理回调"""
        if self._shutdown_registered:
            return

        atexit.register(self.shutdown)

        def _handler(signum, frame):
            self.shutdown()
            sys.exit(0)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handler)
            except (ValueError, OSError):
                pass

        self._shutdown_registered = True


def create_manager(
    preferred: str,
    config: BrowserConfig,
    project_root: Path,
    logger: logging.Logger,
) -> Optional[BrowserManager]:
    """工厂函数: 根据名称创建对应的 BrowserManager

    扩展新浏览器只需在此添加一个 elif 分支。

    Args:
        preferred: "edge" | "chrome" | "auto"
        config: 浏览器配置
        project_root: 项目根目录
        logger: logger 实例

    Returns:
        BrowserManager 或 None (无法识别的浏览器名称)
    """
    from .edge import EdgeAdapter
    from .chrome import ChromeAdapter

    adapters: dict[str, BrowserAdapter] = {
        "edge": EdgeAdapter(),
        "chrome": ChromeAdapter(),
    }

    if preferred in adapters:
        return BrowserManager(adapters[preferred], config, project_root, logger)

    # auto: 依次尝试所有适配器
    for name, adapter in adapters.items():
        for p in adapter.executable_paths():
            if p.exists():
                logger.info("自动选择浏览器: %s (%s)", adapter.display_name(), p)
                return BrowserManager(adapter, config, project_root, logger)

    logger.error("未找到任何支持的浏览器 (%s)", ", ".join(adapters.keys()))
    return None
