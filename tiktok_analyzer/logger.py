"""
TikTok 竞争对手分析系统 - 日志模块
"""
import logging
import os
import sys
from pathlib import Path
from datetime import datetime

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# 会话级单一日志文件（所有模块共享）
_session_log_file: str | None = None


def _get_session_log_file() -> Path:
    global _session_log_file
    if _session_log_file is None:
        _session_log_file = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    return LOG_DIR / _session_log_file


def setup_logger(name: str = "tiktok_analyzer") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    # 从环境变量读取日志级别（默认 INFO）
    _log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    _level_map = {"DEBUG": logging.DEBUG, "INFO": logging.INFO,
                  "WARNING": logging.WARNING, "ERROR": logging.ERROR}
    console_level = _level_map.get(_log_level, logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 控制台输出（errors='replace' 避免 GBK 编码崩溃）
    import io
    utf8_stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ch = logging.StreamHandler(utf8_stdout)
    ch.setLevel(console_level)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # 文件输出（同一会话共享一个日志文件）
    fh = logging.FileHandler(_get_session_log_file(), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger
