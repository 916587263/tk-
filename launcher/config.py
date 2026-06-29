"""
启动器配置 — 数据类 + YAML 加载

所有可调参数集中于此。修改配置无需改代码:
  - 默认值内置 (零配置文件即可运行)
  - config/launcher.yaml 可覆盖任何字段
  - 环境变量优先级最高 (LAUNCHER_* 前缀)

用法:
  from launcher.config import LauncherConfig, load_config

  cfg = load_config()                    # 默认 + YAML + 环境变量
  cfg = load_config(config_path=None)    # 纯默认
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ═══════════════════════════════════════════════════════════════
# 配置数据类
# ═══════════════════════════════════════════════════════════════

@dataclass
class BrowserConfig:
    """浏览器适配器配置"""

    # 首选浏览器: "edge" | "chrome" | "auto"
    preferred: str = "edge"

    # CDP 调试端口
    debug_port: int = 9222
    debug_host: str = "127.0.0.1"

    # 超时 (秒)
    startup_timeout: float = 15.0
    launch_timeout: float = 30.0
    poll_interval: float = 0.5

    # 浏览器管理策略
    reuse_existing: bool = True          # 端口已有 CDP 时是否复用
    kill_stale: bool = True              # 端口被非 CDP 占用时是否终止旧进程
    create_isolated_profile: bool = True # 是否使用独立 user-data-dir

    # 独立 Profile 目录名 (相对于项目根目录)
    profile_dir_name: str = "launcher_profile"

    # Edge / Chrome 启动时附加参数
    extra_args: list[str] = field(default_factory=lambda: [
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-default-apps",
        "--disable-component-update",
    ])


@dataclass
class AppConfig:
    """Flask 应用配置"""

    # Flask 绑定
    host: str = "127.0.0.1"
    port: int = 5000

    # 启动超时 (等待 Flask stdout 输出 "Running on")
    ready_timeout: float = 10.0

    # 启动后是否自动打开浏览器
    open_browser: bool = True

    # 退出后是否保留 CDP 浏览器 (False=自动关闭)
    keep_browser_on_exit: bool = False


@dataclass
class LoggingConfig:
    """日志配置"""

    # 控制台日志级别
    console_level: str = "INFO"   # DEBUG | INFO | WARNING | ERROR

    # 文件日志级别
    file_level: str = "DEBUG"

    # 日志目录 (相对于项目根目录)
    log_dir: str = "logs"

    # 日志文件名
    log_filename: str = "launcher.log"

    # 日志格式
    format: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    date_format: str = "%Y-%m-%d %H:%M:%S"

    # 是否启用彩色控制台输出
    color: bool = True

    # 文件日志最大字节数 (轮转)
    max_bytes: int = 10 * 1024 * 1024  # 10 MB

    # 保留的日志文件备份数
    backup_count: int = 5


@dataclass
class HealthCheckConfig:
    """健康检查配置"""

    # 最低 Python 版本
    min_python: tuple[int, int] = (3, 9)

    # 必需依赖: {import_name: pip_name}
    required_packages: dict[str, str] = field(default_factory=lambda: {
        "playwright": "playwright",
        "flask": "flask",
        "openai": "openai",
        "yaml": "pyyaml",
        "dotenv": "python-dotenv",
        "httpx": "httpx",
    })

    # 可选依赖: {import_name: pip_name}
    optional_packages: dict[str, str] = field(default_factory=lambda: {})


@dataclass
class LauncherConfig:
    """启动器总配置 — 聚合所有子配置"""

    browser: BrowserConfig = field(default_factory=BrowserConfig)
    app: AppConfig = field(default_factory=AppConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    health: HealthCheckConfig = field(default_factory=HealthCheckConfig)


# ═══════════════════════════════════════════════════════════════
# 配置加载
# ═══════════════════════════════════════════════════════════════

def _find_project_root() -> Path:
    """查找项目根目录 (包含 app.py 或 pyproject.toml)"""
    # 从当前文件向上查找
    current = Path(__file__).resolve().parent.parent
    for marker in ("app.py", "pyproject.toml"):
        if (current / marker).exists():
            return current
    return current


def _env_override(cfg: LauncherConfig) -> LauncherConfig:
    """环境变量覆盖 (LAUNCHER_* 前缀)

    支持的变量:
      LAUNCHER_DEBUG_PORT=9223
      LAUNCHER_PREFERRED_BROWSER=chrome
      LAUNCHER_LOG_LEVEL=DEBUG
      LAUNCHER_APP_PORT=5001
      LAUNCHER_NO_OPEN_BROWSER=1
    """
    env = os.environ

    if env.get("LAUNCHER_DEBUG_PORT"):
        try:
            cfg.browser.debug_port = int(env["LAUNCHER_DEBUG_PORT"])
        except ValueError:
            pass

    if env.get("LAUNCHER_PREFERRED_BROWSER"):
        cfg.browser.preferred = env["LAUNCHER_PREFERRED_BROWSER"].lower()

    if env.get("LAUNCHER_LOG_LEVEL"):
        level = env["LAUNCHER_LOG_LEVEL"].upper()
        if level in ("DEBUG", "INFO", "WARNING", "ERROR"):
            cfg.logging.console_level = level

    if env.get("LAUNCHER_APP_PORT"):
        try:
            cfg.app.port = int(env["LAUNCHER_APP_PORT"])
        except ValueError:
            pass

    if env.get("LAUNCHER_NO_OPEN_BROWSER") == "1":
        cfg.app.open_browser = False

    if env.get("LAUNCHER_KEEP_BROWSER") == "1":
        cfg.app.keep_browser_on_exit = True

    return cfg


def load_config(
    config_path: Optional[Path] = None,
    project_root: Optional[Path] = None,
) -> LauncherConfig:
    """加载启动器配置

    优先级 (低→高):
      1. 代码默认值 (LauncherConfig())
      2. config/launcher.yaml
      3. 环境变量 LAUNCHER_*

    Args:
        config_path: YAML 配置文件路径 (None=自动查找)
        project_root: 项目根目录 (None=自动检测)
    """
    cfg = LauncherConfig()

    root = project_root or _find_project_root()

    # ── YAML 文件覆盖 ──
    if config_path is None:
        config_path = root / "config" / "launcher.yaml"

    if config_path.exists():
        try:
            import yaml
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            # browser 节
            if "browser" in data:
                b = data["browser"]
                if "preferred" in b:
                    cfg.browser.preferred = b["preferred"]
                if "debug_port" in b:
                    cfg.browser.debug_port = b["debug_port"]
                if "startup_timeout" in b:
                    cfg.browser.startup_timeout = b["startup_timeout"]
                if "reuse_existing" in b:
                    cfg.browser.reuse_existing = b["reuse_existing"]
                if "kill_stale" in b:
                    cfg.browser.kill_stale = b["kill_stale"]
                if "extra_args" in b:
                    cfg.browser.extra_args = b["extra_args"]

            # app 节
            if "app" in data:
                a = data["app"]
                if "host" in a:
                    cfg.app.host = a["host"]
                if "port" in a:
                    cfg.app.port = a["port"]
                if "open_browser" in a:
                    cfg.app.open_browser = a["open_browser"]
                if "keep_browser_on_exit" in a:
                    cfg.app.keep_browser_on_exit = a["keep_browser_on_exit"]

            # logging 节
            if "logging" in data:
                l = data["logging"]
                if "console_level" in l:
                    cfg.logging.console_level = l["console_level"]
                if "file_level" in l:
                    cfg.logging.file_level = l["file_level"]

        except Exception:
            # YAML 解析失败不阻止启动，使用默认值
            pass

    # ── 环境变量覆盖 (最高优先级) ──
    cfg = _env_override(cfg)

    return cfg
