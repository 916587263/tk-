"""
应用启动器 — Flask 子进程管理

职责:
  - 在子进程中启动 Flask app.py
  - 转发 stdout 到 logger
  - 等待 Flask 就绪信号
  - 优雅关闭

设计: 无模块级全局状态, 所有状态封装在 AppRunner 实例中。
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

from .config import AppConfig


class AppRunner:
    """Flask 应用进程管理器

    用法:
      runner = AppRunner(project_root, config, logger)
      runner.start()
      runner.open_browser()
      runner.wait()          # 阻塞直到 Ctrl+C 或 Flask 退出
    """

    def __init__(
        self,
        project_root: Path,
        config: AppConfig,
        logger: logging.Logger,
    ):
        self._root = project_root
        self._config = config
        self._log = logger
        self._process: Optional[subprocess.Popen] = None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def url(self) -> str:
        return f"http://{self._config.host}:{self._config.port}"

    def start(self) -> subprocess.Popen:
        """在子进程中启动 Flask app.py

        Returns:
            Popen 对象

        Raises:
            RuntimeError: Python 解释器不可用
            OSError: 子进程创建失败
        """
        app_py = self._root / "app.py"
        if not app_py.exists():
            raise FileNotFoundError(f"找不到入口文件: {app_py}")

        # 构建环境变量 (继承 + 覆盖)
        env = os.environ.copy()
        env.setdefault("FLASK_ENV", "development")
        env.setdefault("PYTHONUNBUFFERED", "1")

        self._log.info("启动 Flask: %s", app_py)

        try:
            self._process = subprocess.Popen(
                [sys.executable, str(app_py)],
                cwd=str(self._root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except FileNotFoundError:
            raise RuntimeError(f"找不到 Python 解释器: {sys.executable}")
        except OSError as e:
            raise RuntimeError(f"无法启动 app.py: {e}")

        # 等待 Flask 就绪
        self._wait_ready()

        return self._process

    def _wait_ready(self):
        """轮询 Flask stdout 直到输出 "Running on" """
        deadline = time.time() + self._config.ready_timeout
        while time.time() < deadline and self._process and self._process.poll() is None:
            line = self._process.stdout.readline()
            if line:
                stripped = line.rstrip()
                self._log.debug("[Flask] %s", stripped)
                if "Running on" in stripped:
                    self._log.info("Flask 就绪: %s", self.url)
                    return
            else:
                time.sleep(0.1)

        if self._process and self._process.poll() is None:
            # 超时但进程仍在运行 — 可能 Flask 启动较慢
            self._log.warning(
                "Flask 就绪信号未检测到 (超时 %ss), 进程仍在运行, 请手动访问 %s",
                self._config.ready_timeout, self.url
            )

    def open_browser(self):
        """在系统默认浏览器中打开 Web UI"""
        if self._config.open_browser:
            self._log.info("打开浏览器: %s", self.url)
            webbrowser.open(self.url)

    def wait(self):
        """阻塞等待 Flask 进程退出或用户 Ctrl+C

        同时启动后台线程转发 Flask stdout 到 logger。
        """
        if self._process is None:
            return

        def _forward():
            try:
                for line in self._process.stdout:
                    self._log.debug("[Flask] %s", line.rstrip())
            except (ValueError, OSError):
                pass

        forwarder = threading.Thread(target=_forward, daemon=True)
        forwarder.start()

        try:
            while self._process.poll() is None:
                time.sleep(0.5)
        except KeyboardInterrupt:
            self._log.info("收到 Ctrl+C, 正在关闭...")

    def shutdown(self):
        """终止 Flask 子进程"""
        if self._process is None or self._process.poll() is not None:
            return

        self._log.info("关闭 Flask (PID=%s)...", self._process.pid)
        try:
            if sys.platform == "win32":
                self._process.terminate()
            else:
                self._process.send_signal(signal.SIGTERM)
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._log.warning("Flask 未响应 SIGTERM, 强制终止")
                self._process.kill()
        except (OSError, ProcessLookupError):
            pass
