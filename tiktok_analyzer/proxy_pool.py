"""TikTok 竞争对手分析系统 - 代理池模块"""
import random
import time
import socket
import urllib.parse
from typing import Optional
from dataclasses import dataclass
from pathlib import Path
import json
import threading

from .logger import setup_logger

logger = setup_logger("proxy_pool")

@dataclass
class Proxy:
    server: str          # e.g. "http://127.0.0.1:7890"
    username: Optional[str] = None
    password: Optional[str] = None
    fail_count: int = 0
    max_fails: int = 3
    cooldown_until: float = 0.0

class ProxyPool:
    """代理池，支持从文件加载代理列表并轮换"""

    def __init__(self, proxy_file: Optional[str] = None, check_reachable: bool = True):
        self._proxies: list[Proxy] = []
        self._lock = threading.Lock()
        self._index = 0
        self._check_reachable = check_reachable

        if proxy_file:
            self.load_from_file(proxy_file)

    @staticmethod
    def check_proxy_reachable(server: str, timeout: float = 3.0) -> bool:
        """检测代理服务器是否可达（TCP 连接测试）"""
        try:
            parsed = urllib.parse.urlparse(server)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 8080
            sock = socket.create_connection((host, port), timeout=timeout)
            sock.close()
            return True
        except (OSError, ValueError) as e:
            logger.debug("代理 %s 不可达: %s", server, e)
            return False

    def load_from_file(self, filepath: str):
        """从 JSON 文件加载代理列表（兼容 UTF-8 BOM）"""
        path = Path(filepath)
        if not path.exists():
            logger.warning("代理文件不存在: %s", filepath)
            return

        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)

        with self._lock:
            for item in data:
                if isinstance(item, str):
                    self._proxies.append(Proxy(server=item))
                elif isinstance(item, dict):
                    self._proxies.append(Proxy(
                        server=item.get("server", ""),
                        username=item.get("username"),
                        password=item.get("password"),
                    ))
            logger.info("加载了 %d 个代理", len(self._proxies))

    def add_proxy(self, server: str, username: str = None, password: str = None):
        with self._lock:
            self._proxies.append(Proxy(server=server, username=username, password=password))

    def get_proxy(self) -> Optional[dict]:
        """轮询获取一个可用代理，返回 Playwright proxy 配置字典"""
        with self._lock:
            if not self._proxies:
                logger.debug("代理池为空")
                return None

            now = time.time()
            for _ in range(len(self._proxies)):
                self._index = (self._index + 1) % len(self._proxies)
                proxy = self._proxies[self._index]

                if proxy.cooldown_until > now:
                    continue
                if proxy.fail_count >= proxy.max_fails:
                    continue

                # 可选：使用前检测代理是否可达
                if self._check_reachable and not self.check_proxy_reachable(proxy.server):
                    logger.warning("代理 %s 不可达，跳过", proxy.server)
                    proxy.cooldown_until = time.time() + 60
                    continue

                config: dict = {"server": proxy.server}
                if proxy.username:
                    config["username"] = proxy.username
                if proxy.password:
                    config["password"] = proxy.password
                return config

        logger.warning("没有可用代理")
        return None

    def report_failure(self, server: str):
        with self._lock:
            for p in self._proxies:
                if p.server == server:
                    p.fail_count += 1
                    if p.fail_count >= p.max_fails:
                        logger.warning("代理 %s 已禁用（失败 %d 次）", server, p.fail_count)
                    else:
                        p.cooldown_until = time.time() + 60
                    return

    def report_success(self, server: str):
        with self._lock:
            for p in self._proxies:
                if p.server == server:
                    p.fail_count = 0
                    return

    @property
    def count(self) -> int:
        return len(self._proxies)

    @property
    def available_count(self) -> int:
        now = time.time()
        with self._lock:
            return sum(1 for p in self._proxies
                       if p.fail_count < p.max_fails and p.cooldown_until <= now)

    def any_reachable(self) -> bool:
        """是否有至少一个代理可达"""
        with self._lock:
            if not self._proxies:
                return False
            for p in self._proxies:
                if p.fail_count < p.max_fails and p.cooldown_until <= time.time():
                    if not self._check_reachable or self.check_proxy_reachable(p.server):
                        return True
            return False
