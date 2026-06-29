"""
Microsoft Edge 适配器

支持平台: Windows / macOS / Linux
"""

from __future__ import annotations

import sys
import shutil
from pathlib import Path

from .base import BrowserAdapter


class EdgeAdapter(BrowserAdapter):
    """Microsoft Edge 浏览器适配器"""

    @staticmethod
    def display_name() -> str:
        return "Microsoft Edge"

    def executable_paths(self) -> list[Path]:
        if sys.platform == "win32":
            return [
                Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
                Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
                Path.home() / "AppData/Local/Microsoft/Edge/Application/msedge.exe",
            ]
        elif sys.platform == "darwin":
            return [
                Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            ]
        else:
            # Linux: 通过 which 查找
            found = shutil.which("microsoft-edge")
            return [Path(found)] if found else []

    def kill_command(self, port: int) -> list[str]:
        if sys.platform == "win32":
            return ["taskkill", "/F", "/IM", "msedge.exe"]
        elif sys.platform == "darwin":
            return ["pkill", "-f", "Microsoft Edge.*--remote-debugging-port"]
        else:
            return ["pkill", "-f", f"edge.*--remote-debugging-port={port}"]
