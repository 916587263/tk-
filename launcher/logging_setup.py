"""
日志系统 — 彩色控制台 + 轮转文件

设计:
  - 使用标准库 logging (零第三方依赖)
  - 控制台: 彩色输出 (ANSI), INFO 及以上
  - 文件:   RotatingFileHandler, DEBUG 及以上, UTF-8
  - get_logger(name) 获取已配置的 logger
  - 单例初始化, 重复调用安全

用法:
  from launcher.logging_setup import init_logging, get_logger

  init_logging(project_root, config)
  log = get_logger("launcher")
  log.info("Starting...")
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from .config import LoggingConfig


# ── ANSI 颜色 (Windows 10+ 原生支持) ──
COLORS = {
    "DEBUG":    "\033[36m",   # Cyan
    "INFO":     "\033[92m",   # Green
    "WARNING":  "\033[93m",   # Yellow
    "ERROR":    "\033[91m",   # Red
    "CRITICAL": "\033[1;91m", # Bold Red
}
RESET = "\033[0m"
LEVEL_ICONS = {
    "DEBUG":    "·",
    "INFO":     "✓",
    "WARNING":  "⚠",
    "ERROR":    "✗",
    "CRITICAL": "☠",
}


class _ColoredFormatter(logging.Formatter):
    """彩色控制台格式化器"""

    def __init__(self, fmt: str, datefmt: str, color: bool = True):
        super().__init__(fmt, datefmt)
        self._color = color and sys.stdout.isatty()

    def format(self, record: logging.LogRecord) -> str:
        level = record.levelname
        if self._color and level in COLORS:
            icon = LEVEL_ICONS.get(level, "")
            record.levelname = f"{COLORS[level]}{icon} {level}{RESET}"
        else:
            icon = LEVEL_ICONS.get(level, "")
            record.levelname = f"{icon} {level}"
        return super().format(record)


class _PlainFormatter(logging.Formatter):
    """文件纯文本格式化器 (无颜色)"""

    def format(self, record: logging.LogRecord) -> str:
        icon = LEVEL_ICONS.get(record.levelname, "")
        record.levelname = f"{icon} {record.levelname}"
        return super().format(record)


# ── 全局状态 (模块级单例) ──
_initialized: bool = False
_root_logger: Optional[logging.Logger] = None


def init_logging(
    project_root: Path,
    config: LoggingConfig,
    force: bool = False,
) -> logging.Logger:
    """初始化日志系统 (幂等: 重复调用不创建重复 handler)

    Args:
        project_root: 项目根目录 (日志文件写入 project_root/logs/)
        config:      日志配置
        force:       True=强制重新初始化

    Returns:
        配置好的 root logger
    """
    global _initialized, _root_logger

    if _initialized and not force:
        return _root_logger  # type: ignore[return-value]

    root = logging.getLogger("launcher")
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    # ── 控制台 handler ──
    console_level = getattr(logging, config.console_level.upper(), logging.INFO)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(_ColoredFormatter(
        fmt=config.format,
        datefmt=config.date_format,
        color=config.color,
    ))
    root.addHandler(console_handler)

    # ── 文件 handler ──
    log_dir = project_root / config.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / config.log_filename

    file_level = getattr(logging, config.file_level.upper(), logging.DEBUG)
    file_handler = RotatingFileHandler(
        filename=str(log_file),
        maxBytes=config.max_bytes,
        backupCount=config.backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(_PlainFormatter(
        fmt=config.format,
        datefmt=config.date_format,
    ))
    root.addHandler(file_handler)

    _initialized = True
    _root_logger = root
    return root


def get_logger(name: str = "launcher") -> logging.Logger:
    """获取子 logger (继承 root handler 配置)

    用法:
      log = get_logger(__name__)
      log.info("Hello")
    """
    if _root_logger is None:
        # 未初始化时使用默认配置 (仅控制台)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%H:%M:%S",
        )
    return logging.getLogger(f"launcher.{name}") if name != "launcher" else logging.getLogger("launcher")
