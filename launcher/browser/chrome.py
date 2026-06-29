"""
Google Chrome 适配器

支持平台: Windows / macOS / Linux
"""

from __future__ import annotations

import sys
import shutil
from pathlib import Path

from .base import BrowserAdapter


class ChromeAdapter(BrowserAdapter):
    """Google Chrome 浏览器适配器"""

    @staticmethod
    def display_name() -> str:
        return "Google Chrome"

    def executable_paths(self) -> list[Path]:
        if sys.platform == "win32":
            return [
                Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
                Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
                Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe",
            ]
        elif sys.platform == "darwin":
            return [
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            ]
        else:
            found = shutil.which("google-chrome") or shutil.which("chromium-browser")
            return [Path(found)] if found else []

    def kill_command(self, port: int) -> list[str]:
        if sys.platform == "win32":
            return ["taskkill", "/F", "/IM", "chrome.exe"]
        elif sys.platform == "darwin":
            return ["pkill", "-f", "Google Chrome.*--remote-debugging-port"]
        else:
            return ["pkill", "-f", f"chrome.*--remote-debugging-port={port}"]
