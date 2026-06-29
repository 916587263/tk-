"""
浏览器适配器 — 抽象基类

定义所有浏览器适配器必须实现的接口。
新增浏览器支持只需:
  1. 继承 BrowserAdapter
  2. 实现 executable_paths() 和 display_name()
  3. 注册到 BrowserManager.create()

符合 SOLID 的 OCP (对扩展开放, 对修改关闭)。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class BrowserAdapter(ABC):
    """浏览器适配器抽象基类

    每个具体实现代表一种浏览器 (Edge / Chrome / Brave / ...)。
    所有适配器是无状态的 — 只提供元数据和路径解析,
    生命周期管理由 BrowserManager 负责。
    """

    @staticmethod
    @abstractmethod
    def display_name() -> str:
        """人类可读的浏览器名称, 如 'Microsoft Edge'"""
        ...

    @abstractmethod
    def executable_paths(self) -> list[Path]:
        """返回可执行文件的候选路径列表 (按优先级排列)

        BrowserManager 会使用第一个存在的路径。
        """
        ...

    @abstractmethod
    def kill_command(self, port: int) -> list[str]:
        """返回终止占用指定端口的浏览器进程的命令

        例如 Windows: ["taskkill", "/F", "/IM", "msedge.exe"]
        例如 macOS:   ["pkill", "-f", "Google Chrome.*--remote-debugging-port"]
        """
        ...

    def launch_args(self, port: int, profile_dir: Path, extra: list[str] | None = None) -> list[str]:
        """构建浏览器启动命令行参数

        子类可覆盖以添加浏览器特有参数。
        """
        args = [
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
        ]
        if extra:
            args.extend(extra)
        return args
