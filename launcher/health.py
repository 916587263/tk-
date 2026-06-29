"""
健康检查模块 — 启动前验证所有前提条件

每个检查项返回 HealthResult，包含:
  - name:     检查项名称
  - passed:   是否通过
  - message:  人类可读的结果描述
  - fix_hint: 失败时的修复建议 (可选)
  - severity: "fatal" (阻断启动) | "warning" (不阻断)

设计: 纯函数, 无副作用, 方便 pytest 测试。
"""

from __future__ import annotations

import os
import sys
import shutil
import socket
import urllib.request
import urllib.error
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable


# ═══════════════════════════════════════════════════════════════
# 结果数据结构
# ═══════════════════════════════════════════════════════════════

@dataclass
class HealthResult:
    """单项健康检查结果"""
    name: str
    passed: bool
    message: str = ""
    fix_hint: str = ""
    severity: str = "fatal"  # "fatal" | "warning"

    def status_icon(self) -> str:
        return "✓" if self.passed else "✗"


@dataclass
class HealthReport:
    """聚合健康检查报告"""
    results: list[HealthResult] = field(default_factory=list)

    @property
    def all_pass(self) -> bool:
        return all(r.passed for r in self.results if r.severity == "fatal")

    @property
    def fatal_count(self) -> int:
        return sum(1 for r in self.results if not r.passed and r.severity == "fatal")

    @property
    def warning_count(self) -> int:
        return sum(1 for r in self.results if not r.passed and r.severity == "warning")


# ═══════════════════════════════════════════════════════════════
# 检查函数
# ═══════════════════════════════════════════════════════════════

def check_python(min_version: tuple[int, int]) -> HealthResult:
    """检查 Python 版本"""
    current = sys.version_info[:2]
    passed = current >= min_version
    return HealthResult(
        name="Python 版本",
        passed=passed,
        message=f"Python {current[0]}.{current[1]} (需要 >= {min_version[0]}.{min_version[1]})",
        fix_hint=f"安装 Python {min_version[0]}.{min_version[1]}+: https://www.python.org/downloads/",
    )


def check_packages(
    required: dict[str, str],
    optional: Optional[dict[str, str]] = None,
) -> HealthResult:
    """检查 Python 包是否可导入

    Args:
        required: {import_name: pip_name}
        optional: {import_name: pip_name} — 缺失不标记为失败
    """
    missing = []
    opt_missing = []

    for module_name, pip_name in required.items():
        try:
            __import__(module_name)
        except ImportError:
            missing.append(pip_name)

    if optional:
        for module_name, pip_name in optional.items():
            try:
                __import__(module_name)
            except ImportError:
                opt_missing.append(pip_name)

    passed = len(missing) == 0

    parts = []
    if passed:
        parts.append("所有必需依赖已安装")
    if missing:
        parts.append(f"缺失: {' '.join(missing)}")
    if opt_missing:
        parts.append(f"可选: {' '.join(opt_missing)} (不影响使用)")

    return HealthResult(
        name="Python 依赖",
        passed=passed,
        message=" | ".join(parts),
        fix_hint=f"pip install -r requirements.txt" if missing else "",
        severity="fatal" if missing else "warning",
    )


def check_playwright_browsers() -> HealthResult:
    """检查 Playwright Chromium 是否已安装"""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "--dry-run", "chromium"],
            capture_output=True, text=True, timeout=30,
        )
        # playwright install --dry-run: 返回 0=已安装, 非0=未安装
        passed = result.returncode == 0
        return HealthResult(
            name="Playwright Chromium",
            passed=passed,
            message="Chromium 已安装" if passed else "Chromium 未安装",
            fix_hint="playwright install chromium" if not passed else "",
            severity="fatal",
        )
    except Exception:
        # playwright 本身未安装时也返回 fatal
        return HealthResult(
            name="Playwright Chromium",
            passed=False,
            message="无法检查 (playwright 可能未安装)",
            fix_hint="pip install playwright && playwright install chromium",
            severity="fatal",
        )


def check_browser_installed(browser_paths: list[Path], browser_name: str = "Edge") -> HealthResult:
    """检查浏览器可执行文件是否存在"""
    for p in browser_paths:
        if p.exists():
            return HealthResult(
                name=f"浏览器 ({browser_name})",
                passed=True,
                message=str(p),
            )

    return HealthResult(
        name=f"浏览器 ({browser_name})",
        passed=False,
        message=f"未找到 {browser_name}",
        fix_hint=f"安装 {browser_name}: https://www.microsoft.com/edge" if browser_name == "Edge" else "",
        severity="fatal",
    )


def check_port_available(host: str, port: int) -> HealthResult:
    """检查端口是否可用 (未被非 CDP 进程占用)"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.3)
    try:
        sock.connect((host, port))
        sock.close()
        # 端口被占用 — 但可能是 CDP，需进一步检查
        return HealthResult(
            name=f"端口 {port}",
            passed=True,
            message=f"端口 {port} 已占用 (可能已是 CDP 浏览器)",
            severity="warning",
        )
    except (socket.timeout, ConnectionRefusedError, OSError):
        return HealthResult(
            name=f"端口 {port}",
            passed=True,
            message=f"端口 {port} 空闲",
        )


def check_cdp_ready(host: str, port: int, timeout: float = 2.0) -> HealthResult:
    """检查 CDP DevTools 是否就绪 (HTTP 握手)"""
    try:
        url = f"http://{host}:{port}/json/version"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode())
                browser_name = data.get("Browser", "Unknown")
                return HealthResult(
                    name="CDP DevTools",
                    passed=True,
                    message=f"就绪 ({browser_name})",
                )
    except Exception:
        pass

    return HealthResult(
        name="CDP DevTools",
        passed=False,
        message=f"不可连接 (http://{host}:{port})",
        fix_hint="启动 CDP 浏览器: launcher 将自动处理",
        severity="warning",  # warning 非 fatal — launcher 会自动启动
    )


def check_env_file(project_root: Path) -> HealthResult:
    """检查 .env 文件"""
    env_path = project_root / ".env"
    if env_path.exists():
        # 检查是否还是默认模板 (未配置)
        content = env_path.read_text(encoding="utf-8", errors="ignore")
        if "sk-your-deepseek-key-here" in content and "sk-your-openai-key-here" in content:
            return HealthResult(
                name=".env 配置",
                passed=True,
                message="存在但使用默认模板 (LLM 功能需配置 API Key)",
                fix_hint=f"编辑 {env_path} 填入 DEEPSEEK_API_KEY 或 OPENAI_API_KEY",
                severity="warning",
            )
        return HealthResult(
            name=".env 配置",
            passed=True,
            message="已配置",
        )

    return HealthResult(
        name=".env 配置",
        passed=True,  # 不阻断 — 规则引擎模式不需要
        message="未找到 .env (LLM 增强需 API Key)",
        fix_hint="cp .env.example .env 然后编辑填入 API Key",
        severity="warning",
    )


# ═══════════════════════════════════════════════════════════════
# 聚合运行器
# ═══════════════════════════════════════════════════════════════

def run_health_checks(
    project_root: Path,
    browser_paths: list[Path],
    browser_name: str,
    required_packages: dict[str, str],
    optional_packages: dict[str, str],
    min_python: tuple[int, int],
    cdp_host: str = "127.0.0.1",
    cdp_port: int = 9222,
) -> HealthReport:
    """运行所有健康检查, 返回聚合报告

    用法:
        report = run_health_checks(root, paths, "Edge", req, opt, (3,9))
        if not report.all_pass:
            for r in report.results:
                print(f"[{r.status_icon()}] {r.name}: {r.message}")
    """
    checks: list[Callable[[], HealthResult]] = [
        lambda: check_python(min_python),
        lambda: check_packages(required_packages, optional_packages),
        lambda: check_playwright_browsers(),
        lambda: check_browser_installed(browser_paths, browser_name),
        lambda: check_port_available(cdp_host, cdp_port),
        lambda: check_cdp_ready(cdp_host, cdp_port),
        lambda: check_env_file(project_root),
    ]

    report = HealthReport()
    for check in checks:
        try:
            result = check()
        except Exception as e:
            result = HealthResult(
                name="内部错误",
                passed=False,
                message=str(e),
                severity="warning",
            )
        report.results.append(result)

    return report


# ═══════════════════════════════════════════════════════════════
# 浏览器路径解析 (平台自适应)
# ═══════════════════════════════════════════════════════════════

def resolve_browser_paths(preferred: str = "edge") -> tuple[list[Path], str]:
    """解析浏览器可执行文件搜索路径列表

    Args:
        preferred: "edge" | "chrome" | "auto"

    Returns:
        (paths, display_name)
    """
    if sys.platform == "win32":
        return _resolve_windows(preferred)
    elif sys.platform == "darwin":
        return _resolve_macos(preferred)
    else:
        return _resolve_linux(preferred)


def _resolve_windows(preferred: str) -> tuple[list[Path], str]:
    """Windows 浏览器路径"""
    edge_paths = [
        Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
        Path.home() / "AppData/Local/Microsoft/Edge/Application/msedge.exe",
    ]
    chrome_paths = [
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe",
    ]

    if preferred == "edge":
        return edge_paths, "Edge"
    elif preferred == "chrome":
        return chrome_paths, "Chrome"
    else:  # auto: Edge 优先
        return edge_paths + chrome_paths, "Edge/Chrome"


def _resolve_macos(preferred: str) -> tuple[list[Path], str]:
    """macOS 浏览器路径"""
    edge_paths = [Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")]
    chrome_paths = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]

    if preferred == "edge":
        return edge_paths, "Edge"
    elif preferred == "chrome":
        return chrome_paths, "Chrome"
    else:
        return edge_paths + chrome_paths, "Edge/Chrome"


def _resolve_linux(preferred: str) -> tuple[list[Path], str]:
    """Linux 浏览器路径 (通过 which 查找)"""
    edge = shutil.which("microsoft-edge")
    chrome = shutil.which("google-chrome") or shutil.which("chromium-browser")

    paths = []
    if preferred in ("edge", "auto") and edge:
        paths.append(Path(edge))
    if preferred in ("chrome", "auto") and chrome:
        paths.append(Path(chrome))

    name = "Edge" if edge else "Chrome" if chrome else "Browser"
    return paths, name


def find_browser_executable(paths: list[Path]) -> Optional[Path]:
    """从路径列表中返回第一个存在的可执行文件"""
    for p in paths:
        if p.exists():
            return p
    return None
