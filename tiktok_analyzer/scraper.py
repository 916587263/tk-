"""
TikTok 竞争对手分析系统 - Playwright 爬虫核心
使用 persistent_context + Edge 浏览器
"""
import json
import re
import time
import random
import asyncio
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from playwright.async_api import async_playwright, BrowserContext, Page

from .logger import setup_logger
from .captcha import detect_captcha, wait_for_human_intervention
from .proxy_pool import ProxyPool
from .checkpoint import CheckpointManager
from .network_collector import NetworkCollector

logger = setup_logger("scraper")

COOKIE_DIR = Path(__file__).parent.parent / "cookies"
COOKIE_DIR.mkdir(exist_ok=True)
DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# TikTok 页面 URL 模板
TIKTOK_URL = "https://www.tiktok.com"
TIKTOK_SEARCH_URL = "https://www.tiktok.com/search?q={query}"
TIKTOK_USER_URL = "https://www.tiktok.com/@{username}"
TIKTOK_VIDEO_URL = "https://www.tiktok.com/@{username}/video/{video_id}"

# 统一 User-Agent（所有 context 共用，保持指纹一致性）
COMMON_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# 反检测浏览器参数
ANTI_DETECTION_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-sandbox",
    "--disable-infobars",
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-sync",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-field-trial-config",
    "--disable-hang-monitor",
    "--disable-ipc-flooding-protection",
    "--force-color-profile=srgb",
    "--metrics-recording-only",
    "--password-store=basic",
    "--use-mock-keychain",
]

# ==== ======== ====
STEALTH_JS = r"""
(function() {
    // ── 核心：隐藏自动化标志 ──
    Object.defineProperty(navigator, "webdriver", { get: () => false });
    try { delete navigator.__proto__.webdriver; } catch(e) {}

    // ── 伪装 plugins（TikTok 严格检查 plugins.length）──
    // 使用纯对象模拟（不依赖原生 MimeType/Plugin 构造函数）
    const fakeMimeTypes = [
        { type: "application/pdf", suffixes: "pdf", description: "Portable Document Format" },
        { type: "application/x-google-chrome-pdf", suffixes: "pdf", description: "Portable Document Format" },
        { type: "application/x-nacl", suffixes: "", description: "Native Client Executable" },
    ];
    const fakePlugins = [
        { name: "Chrome PDF Plugin", filename: "internal-pdf-viewer", description: "Portable Document Format", length: 1 },
        { name: "Chrome PDF Viewer", filename: "mhjfbmdgcfjbbpaeojofohoefgiehjai", description: "", length: 1 },
        { name: "Native Client", filename: "internal-nacl-plugin", description: "", length: 1 },
    ];

    try {
        Object.defineProperty(navigator, "plugins", {
            get: () => {
                const arr = fakePlugins.map((p, i) => {
                    const plugin = Object.create(Object.prototype, {
                        name: { value: p.name, enumerable: true },
                        filename: { value: p.filename, enumerable: true },
                        description: { value: p.description, enumerable: true },
                        length: { value: p.length, enumerable: true },
                        0: { value: fakeMimeTypes[i] || fakeMimeTypes[0], enumerable: true },
                    });
                    return plugin;
                });
                arr.item = (i) => arr[i] || null;
                arr.namedItem = (n) => arr.find(p => p.name === n || p.filename === n) || null;
                arr.refresh = () => {};
                return arr;
            },
            configurable: true, enumerable: true,
        });
        Object.defineProperty(navigator, "mimeTypes", {
            get: () => {
                const arr = fakeMimeTypes.map(m => {
                    const mt = Object.create(Object.prototype, {
                        type: { value: m.type, enumerable: true },
                        suffixes: { value: m.suffixes, enumerable: true },
                        description: { value: m.description, enumerable: true },
                    });
                    return mt;
                });
                arr.item = (i) => arr[i] || null;
                arr.namedItem = (n) => arr.find(m => m.type === n) || null;
                arr.refresh = () => {};
                return arr;
            },
            configurable: true, enumerable: true,
        });
    } catch(e) {}

    // ── 浏览器指纹一致性 ──
    Object.defineProperty(navigator, "languages", { get: () => ["zh-CN","zh","en-US","en"] });
    Object.defineProperty(navigator, "language", { get: () => "zh-CN" });
    Object.defineProperty(navigator, "platform", { get: () => "Win32" });
    Object.defineProperty(navigator, "vendor", { get: () => "Google Inc." });
    Object.defineProperty(navigator, "productSub", { get: () => "20030107" });
    Object.defineProperty(navigator, "hardwareConcurrency", { get: () => 8 });
    Object.defineProperty(navigator, "maxTouchPoints", { get: () => 0 });
    Object.defineProperty(navigator, "deviceMemory", { get: () => 8 });

    // ── chrome 对象 ──
    window.chrome = {
        runtime: { onConnect: { addListener: function(){} }, onMessage: { addListener: function(){} } },
        loadTimes: function() { return {}; },
        csi: function() { return {}; },
        app: { isInstalled: false, InstallState: { DISABLED: "disabled", INSTALLED: "installed", NOT_INSTALLED: "not_installed" }, RunningState: { CANNOT_RUN: "cannot_run", READY_TO_RUN: "ready_to_run", RUNNING: "running" } },
    };

    // ── 权限伪装 ──
    const origQuery = window.navigator.permissions.query.bind(window.navigator.permissions);
    window.navigator.permissions.query = (params) => {
        if (params.name === "notifications") {
            return Promise.resolve({ state: "prompt", onchange: null });
        }
        return origQuery(params);
    };

    // ── 屏幕尺寸一致性 ──
    Object.defineProperty(window, "outerWidth", { get: () => window.innerWidth, configurable: true });
    Object.defineProperty(window, "outerHeight", { get: () => window.innerHeight + 85, configurable: true });
    try {
        Object.defineProperty(window.screen, "availWidth", { get: () => window.screen.width, configurable: true });
        Object.defineProperty(window.screen, "availHeight", { get: () => window.screen.height - 40, configurable: true });
    } catch(e) {}
    Object.defineProperty(window.screen, "colorDepth", { get: () => 24 });
    Object.defineProperty(window.screen, "pixelDepth", { get: () => 24 });

    // ── WebGL 指纹伪装 ──
    try {
        const canvas = document.createElement("canvas");
        const gl = canvas.getContext("webgl") || canvas.getContext("experimental-webgl");
        if (gl) {
            const origGetParam = gl.getParameter.bind(gl);
            gl.getParameter = function(param) {
                if (param === 37445) return "Google Inc. (Intel)";
                if (param === 37446) return "ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)";
                return origGetParam(param);
            };
        }
    } catch(e) {}

    // ── Canvas 指纹加噪 ──
    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type) {
        const ctx = this.getContext("2d");
        if (ctx && this.width > 16 && this.height > 16) {
            try {
                const imgData = ctx.getImageData(0, 0, 1, 1);
                imgData.data[3] = imgData.data[3] ^ 1;
                ctx.putImageData(imgData, 0, 0);
            } catch(e) {}
        }
        return origToDataURL.apply(this, arguments);
    };

    // ── 清理痕迹 ──
    delete window.callPhantom;
    delete window._phantom;
    delete window.__nightmare;
    try { delete window.navigator.userAgentData; } catch(e) {}

    // ── 覆盖 connection ──
    try {
        Object.defineProperty(navigator, "connection", {
            get: () => ({ effectiveType: "4g", rtt: 50, downlink: 10, saveData: false, onchange: null }),
            configurable: true,
        });
    } catch(e) {}

    // ── 覆盖 MediaDevices ──
    try {
        const enumerateDevices = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
        navigator.mediaDevices.enumerateDevices = () =>
            enumerateDevices().then(devices =>
                devices.length > 0 ? devices : [
                    { deviceId: "default", groupId: "default", kind: "audioinput", label: "" },
                    { deviceId: "default", groupId: "default", kind: "audiooutput", label: "" },
                    { deviceId: "default", groupId: "default", kind: "videoinput", label: "" },
                ]
            );
    } catch(e) {}

    // ── 覆盖 Battery API ──
    try {
        navigator.getBattery = () => Promise.resolve({
            charging: true, chargingTime: 0, dischargingTime: Infinity,
            level: 1, onchargingchange: null, onchargingtimechange: null,
            ondischargingtimechange: null, onlevelchange: null,
        });
    } catch(e) {}
})();
"""



class TikTokScraper:
    """TikTok 数据抓取器"""

    def __init__(
        self,
        browser_channel: str = "msedge",
        headless: bool = False,
        proxy_pool: Optional[ProxyPool] = None,
        checkpoint: Optional[CheckpointManager] = None,
        progress_callback=None,
    ):
        self.browser_channel = browser_channel
        self.headless = headless
        self.proxy_pool = proxy_pool
        self.checkpoint = checkpoint
        self.progress_callback = progress_callback  # async callback(task_id, msg, data)

        self._playwright = None
        self._browser = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        # 请求间隔（秒），带随机抖动防止被检测
        self._rate_limit_min = 0.5   # 最小间隔（CDP 模式使用真实浏览器，不需太保守）
        self._rate_limit_max = 2.0   # 最大间隔
        self._backoff_level = 0      # 当前退避级别（0=正常，每遇限流+1）
        self._max_backoff = 6        # 最大退避级别
        self._current_proxy = None  # 当前使用的代理
        self._cancelled = False     # 用户取消标志

    @staticmethod
    def _is_network_error(err_msg: str) -> bool:
        """Check if error is proxy/network related"""
        network_errors = [
            "ERR_PROXY_CONNECTION_FAILED", "ERR_FAILED",
            "ERR_CONNECTION_REFUSED", "ERR_TIMED_OUT",
            "ERR_TUNNEL_CONNECTION_FAILED", "ERR_CONNECTION_CLOSED",
            "ERR_CONNECTION_RESET", "ERR_NAME_NOT_RESOLVED",
            "ERR_NETWORK_CHANGED", "ERR_INTERNET_DISCONNECTED",
        ]
        return any(e in err_msg for e in network_errors)

    async def _emit_progress(self, msg: str, data: dict = None):
        if self.progress_callback:
            await self.progress_callback(msg, data or {})

    def cancel(self):
        """设置取消标志，run_analysis() 将在下一个检查点停止"""
        self._cancelled = True
        logger.info("用户请求取消，将在下一个检查点停止...")

    def _random_delay(self) -> float:
        """Return jittered delay with backoff"""
        base = random.uniform(self._rate_limit_min, self._rate_limit_max)
        backoff = base * (2 ** self._backoff_level)
        actual = min(backoff, 300.0)
        return actual

    async def _sleep_with_jitter(self):
        """Sleep with random jitter and backoff"""
        delay = self._random_delay()
        logger.debug("Sleeping %.1fs (backoff level %d)", delay, self._backoff_level)
        await asyncio.sleep(delay)

    async def _is_rate_limited(self) -> bool:
        """Detect TikTok rate-limit / too-many-requests page"""
        try:
            page_text = (await self._page.content()).lower()
            title = (await self._page.title()).lower()
            rate_limit_keywords = [
                "too many requests", "try again later",
                "you are visiting too frequently", "something went wrong",
            ]
            for kw in rate_limit_keywords:
                if kw in title or kw in page_text[:3000]:
                    logger.warning("Rate-limited: %s", kw)
                    return True
            if len(page_text) < 200 and "tiktok" not in title:
                logger.warning("Page too short, likely rate-limited")
                return True
            return False
        except Exception:
            return False

    async def _handle_rate_limit(self) -> bool:
        """Handle rate limiting with exponential backoff. Returns True=continue, False=abort."""
        if self._backoff_level < self._max_backoff:
            self._backoff_level += 1
            delay = self._random_delay()
            logger.warning(
                "Rate-limited! Backoff %d/%d, waiting %.1fs...",
                self._backoff_level, self._max_backoff, delay
            )
            await self._emit_progress(
                f"Rate-limited, waiting {delay:.0f}s... (backoff {self._backoff_level}/{self._max_backoff})"
            )
            await asyncio.sleep(delay)
            if self.proxy_pool and self.proxy_pool.count > 1:
                logger.info("Switching proxy to bypass rate limit...")
                await self._emit_progress("Switching proxy...")
                try:
                    await self._context.close()
                except Exception:
                    pass
                proxy_config = self.proxy_pool.get_proxy()
                if proxy_config:
                    self._context = await self._browser.new_context(
                        proxy=proxy_config,
                        viewport={"width": 1920, "height": 1080},
                        user_agent=COMMON_UA,
                    )
                    self._page = await self._context.new_page()
                    await self._page.add_init_script(STEALTH_JS)
                    logger.info("Switched to new proxy")
            return True
        else:
            logger.error("Max backoff reached, aborting")
            await self._emit_progress("Rate limit too frequent, please try later", {"error": "rate_limit_exhausted"})
            return False

    def _get_proxy_config(self) -> Optional[dict]:
        """Get proxy config from pool (no pre-check, try and fail)"""
        if self.proxy_pool and self.proxy_pool.count > 0:
            config = self.proxy_pool.get_proxy()
            if config:
                self._current_proxy = config.get("server", "")
                logger.info("Proxy: %s", self._current_proxy)
                return config
        return None

    # ───────────────────── 浏览器生命周期 ─────────────────────

    async def connect_over_cdp(self, cdp_port: int = 9222):
        """通过 CDP 连接到已运行的浏览器（推荐方案，绕过反检测）

        使用方法：
        1. 完全关闭 Edge/Chrome
        2. 在终端运行:
           msedge --remote-debugging-port=9222
           或: chrome --remote-debugging-port=9222
        3. 正常使用该浏览器登录 TikTok
        4. 启动分析任务，选择 "CDP 连接模式"

        优势：使用真实浏览器环境（含登录态、历史、扩展），TikTok 几乎无法检测。
        """
        await self._emit_progress(f"通过 CDP 连接到浏览器 (端口 {cdp_port})...")

        self._cdp_mode = True
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.connect_over_cdp(
            f"http://127.0.0.1:{cdp_port}"
        )
        # 复用浏览器默认 context，只创建一页 — 不 new_context()（会在用户桌面弹出新窗口）
        # 原则：整个抓取过程复用同一个 page，不调 new_page()，用户眼里只有一个标签页
        contexts = self._browser.contexts
        self._context = contexts[0] if contexts else await self._browser.new_context()
        self._page = await self._context.new_page()

        # CDP 模式下同样注入 stealth（防护层）
        await self._page.add_init_script(STEALTH_JS)
        await self._emit_progress("CDP 浏览器已连接（单页复用，不干扰用户）")

    async def start_browser(self, user_data_dir: Optional[str] = None):
        """启动浏览器（persistent context 保持登录状态）"""
        await self._emit_progress("正在启动浏览器...")

        self._playwright = await async_playwright().start()

        proxy_config = self._get_proxy_config()
        if proxy_config is None:
            if self.proxy_pool and self.proxy_pool.count > 0:
                await self._emit_progress(
                    f"代理池 {self.proxy_pool.count} 个代理全部不可用，以系统直连启动。"
                    f"如果你使用 VPN 全局模式（TUN），这是正常的——VPN 不通过 HTTP 代理。"
                )
            else:
                await self._emit_progress(
                    "未配置 HTTP 代理，以系统直连启动。"
                    "如果你使用 VPN（Clash/V2Ray 等）全局/TUN 模式，流量已自动走 VPN，无需额外配置。"
                )

        launch_options = {
            "headless": self.headless,
            "channel": self.browser_channel,
            "args": ANTI_DETECTION_ARGS,
        }

        if user_data_dir:
            udd = Path(user_data_dir)
            udd.mkdir(parents=True, exist_ok=True)

            context_options = {
                "user_data_dir": str(udd),
                "viewport": {"width": 1920, "height": 1080},
                "locale": "zh-CN",
                "timezone_id": "Asia/Shanghai",
                "user_agent": COMMON_UA,
                "bypass_csp": True,
            }

            if proxy_config:
                context_options["proxy"] = proxy_config

            self._context = await self._playwright.chromium.launch_persistent_context(
                **launch_options,
                **context_options,
            )
            logger.info("Persistent context 已创建: %s (代理: %s)", udd, self._current_proxy or "系统直连/VPN")
        else:
            self._browser = await self._playwright.chromium.launch(**launch_options)
            context_options = {
                "viewport": {"width": 1920, "height": 1080},
                "locale": "zh-CN",
                "timezone_id": "Asia/Shanghai",
                "user_agent": COMMON_UA,
                "bypass_csp": True,
            }
            if proxy_config:
                context_options["proxy"] = proxy_config
            self._context = await self._browser.new_context(**context_options)
            logger.info("普通 context 已创建 (代理: %s)", self._current_proxy or "系统直连/VPN")

        self._page = await self._context.new_page()

        # 设置反检测脚本
        await self._page.add_init_script(STEALTH_JS)

        # 额外：注入 navigator 增强（必须在 context 创建后立即执行）
        await self._page.evaluate("""
            (() => {
                // 确保 navigator 属性在页面 JS 运行前被覆盖
                Object.defineProperty(navigator, 'webdriver', {get: () => false});
                delete Object.getPrototypeOf(navigator).webdriver;
            })();
        """)

        await self._emit_progress("浏览器已启动")

        # 连接测试
        await self._test_connectivity()

    async def _test_connectivity(self):
        """Test if TikTok is reachable through current proxy/direct connection"""
        logger.info("Testing connectivity...")
        mode = self._current_proxy or "direct"

        # 1. 先测 Google — 验证基础网络 / 代理是否通
        try:
            await self._page.goto(
                "https://www.google.com",
                timeout=30000
            )
            logger.info("Google OK")
        except Exception as e:
            logger.warning("Google FAIL: %s", str(e)[:120])

        # 2. 再测 TikTok
        try:
            await self._page.goto(
                TIKTOK_URL,
                wait_until="networkidle",
                timeout=60000
            )
            current_url = self._page.url
            logger.info("TikTok OK: %s", current_url)
            await self._emit_progress(f"TikTok reachable ({mode}): {current_url}")
        except Exception as e:
            err_msg = str(e)
            logger.error("TikTok FAIL: %s", err_msg)
            await self._emit_progress(
                f"TikTok unreachable ({mode}): {err_msg[:120]}. "
                "Check proxy/VPN or remove proxies.json for direct mode."
            )

    async def close(self):
        """关闭浏览器（CDP 模式下仅断开连接，不关闭用户浏览器）"""
        if self._context and not self._browser:
            # persistent context 模式：关闭 context（没有 browser 对象）
            await self._context.close()
        elif getattr(self, '_cdp_mode', False):
            # CDP 模式：关闭抓取页面，保留用户浏览器和 context
            logger.info("CDP 模式：断开连接（用户浏览器保持运行）")
        elif self._browser and self._context:
            # 普通 browser 模式：context 由 browser 管理，关闭 browser 即可
            pass
        if self._browser and not getattr(self, '_cdp_mode', False):
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._playwright:
            await self._playwright.stop()
        logger.info("浏览器已断开")

    # ───────────────────── 登录与验证码 ─────────────────────

    async def ensure_logged_in(self):
        """确保已登录 TikTok（Cookie 持久化）

        处理三种情况：
        1. 已有有效 Cookie → 直接恢复
        2. Cookie 过期 + TikTok 反爬 → 提供 CDP 替代方案
        3. 需要人工登录 → 等待用户在 Playwright 窗口中操作
        """
        cookie_file = COOKIE_DIR / "tiktok_cookies.json"

        # ── 尝试加载已有 Cookie ──
        if cookie_file.exists():
            try:
                with open(cookie_file, "r", encoding="utf-8") as f:
                    cookies = json.load(f)
                await self._context.add_cookies(cookies)
                logger.info("已加载 %d 个 Cookie", len(cookies))

                # 验证登录状态
                try:
                    await self._page.goto(TIKTOK_URL, wait_until="domcontentloaded", timeout=30000)
                except Exception as e:
                    err_msg = str(e)
                    if self._is_network_error(err_msg):
                        logger.error("Cookie验证时网络错误: %s", err_msg)
                    else:
                        raise
                await asyncio.sleep(2)
                if await self._check_logged_in():
                    logger.info("Cookie 有效，已登录")
                    await self._emit_progress("Cookie 有效，已登录状态")
                    return True
                else:
                    logger.info("Cookie 已过期，需要重新登录")
            except Exception as e:
                logger.warning("加载 Cookie 失败: %s", e)

        # ── 需要登录：先检查 TikTok 是否可访问 ──
        await self._emit_progress("需要登录 TikTok，正在打开登录页面...")
        try:
            await self._page.goto(f"{TIKTOK_URL}/login", wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            err_msg = str(e)
            if self._is_network_error(err_msg):
                logger.error("无法连接 TikTok (当前网络: %s): %s", self._current_proxy or "系统直连/VPN", err_msg)
                hint = (
                    f"\n{'='*60}\n"
                    f"❌ 无法连接到 TikTok\n"
                    f"当前网络模式: {self._current_proxy or '系统直连/VPN'}\n"
                    f"错误: {err_msg[:200]}\n"
                    f"\n请检查：\n"
                    f"  1. VPN/代理是否已开启（Clash/V2Ray 等是否在运行）\n"
                    f"  2. 如果用 HTTP 代理，检查 proxies.json 端口是否正确\n"
                    f"  3. 如果用 VPN TUN 模式，删除 proxies.json（VPN 不通过 HTTP 代理）\n"
                    f"{'='*60}"
                )
                raise RuntimeError(hint) from e
            raise
        await asyncio.sleep(3)

        # ── 检测 TikTok 反爬拦截（访问繁忙 / too many requests）──
        page_text = await self._page.content()
        title = (await self._page.title()).lower()

        is_blocked = any(kw in page_text.lower() for kw in [
            "maximum number of attempts", "try again later",
            "too many requests", "访问太频繁", "访问繁忙",
            "something went wrong", "访问过于频繁",
        ]) or any(kw in title for kw in [
            "too many requests", "access denied",
        ])

        if is_blocked:
            logger.warning("TikTok 检测到自动化浏览器，显示反爬页面")
            await self._emit_progress(
                "\n".join([
                    "⚠️ TikTok 检测到自动化浏览器，拒绝访问。",
                    "",
                    "🔧 这通常不是代理/VPN 的问题，而是 TikTok 识别了 Playwright 浏览器。",
                    "",
                    "📌 推荐解决方案（按优先级）：",
                    "  【方案A - CDP 连接模式】推荐！",
                    "    1. 完全关闭 Edge/Chrome 浏览器",
                    "    2. 终端运行: msedge --remote-debugging-port=9222",
                    "    3. 在弹出的浏览器中正常登录 TikTok",
                    "    4. 在工具中选择 'CDP 连接模式' 重新开始",
                    "",
                    "  【方案B - Cookie 导入】",
                    "    1. 用普通浏览器登录 TikTok",
                    "    2. 用 EditThisCookie 等扩展导出 cookies.json",
                    "    3. 放入项目的 cookies/ 目录覆盖 tiktok_cookies.json",
                    "    4. 重新启动分析",
                    "",
                    "  【方案C - 手动登录】",
                    "    在弹出窗口中尝试完成登录（成功率较低，取决于 TikTok 风控）",
                ]),
                {"error": "tiktok_antibot_block"}
            )

            # 仍然打开登录页让用户尝试（万一能成功）
            logger.info("等待人工操作（登录/验证码）...")
            logged_in = await wait_for_human_intervention(self._page, timeout_hours=2.0)
            if logged_in:
                cookies = await self._context.cookies()
                with open(cookie_file, "w", encoding="utf-8") as f:
                    json.dump(cookies, f, ensure_ascii=False, indent=2)
                await self._emit_progress("登录成功，Cookie 已保存")
                return True
            return False

        # ── 未被拦截，正常登录流程 ──
        logger.info("等待人工登录 TikTok...")
        await self._emit_progress(
            "请在浏览器窗口中登录 TikTok。如果你有可用的 TikTok Cookie，也可放入 "
            f"{cookie_file} 后重启任务跳过此步骤。"
        )
        logged_in = await wait_for_human_intervention(self._page, timeout_hours=2.0)

        if logged_in:
            # 保存 Cookie
            cookies = await self._context.cookies()
            with open(cookie_file, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
            logger.info("Cookie 已保存 (%d 个)", len(cookies))
            await self._emit_progress("登录成功，Cookie 已保存")
            return True

        await self._emit_progress("登录失败或超时", {"error": "login_timeout"})
        return False

    async def _check_logged_in(self) -> bool:
        """检查是否已登录"""
        try:
            logged_out_selectors = [
                '[data-e2e="top-login-button"]',
                'button:has-text("Log in")',
                'a[href*="login"]',
            ]
            for sel in logged_out_selectors:
                if await self._page.locator(sel).count() > 0:
                    return False
            return True
        except Exception:
            return False

    async def handle_captcha_if_needed(self) -> bool:
        """检测验证码并暂停等待"""
        if await detect_captcha(self._page):
            return await wait_for_human_intervention(self._page)
        return True

    # ───────────────────── 搜索账号 ─────────────────────

    async def search_accounts(
        self, keyword: str, region: str = "", max_results: int = 20
    ) -> list[dict]:
        """根据关键词搜索 TikTok 账号 — 通过 XHR 拦截提取, 不依赖 DOM"""
        await self._emit_progress(f"正在搜索: {keyword} (地区: {region or '不限'})")

        collector = NetworkCollector(self._page)

        # 先搜索视频 (触发 general/full API)
        search_query = f"{keyword} {region}".strip() if region else keyword
        search_videos = await collector.collect_search_videos(search_query, max_results=50)

        # 从视频作者中提取账号列表
        accounts = self._build_accounts_from_videos(search_videos)

        # 再点击 User 标签获取详细账号信息 (补充 follower/bio 等)
        try:
            api_accounts = await collector.collect_search_accounts(search_query, max_results=max_results)
            # 合并: API 账号数据更全 (follower/bio), 用 API 数据覆盖
            api_map = {a["username"]: a for a in api_accounts if a.get("username")}
            for acc in accounts:
                uname = acc.get("username", "")
                if uname in api_map:
                    api_data = api_map[uname]
                    # API 数据优先 (含 follower_count, bio)
                    for key in ("nickname", "bio", "follower_count", "like_count",
                                "sec_uid", "uid", "verified"):
                        if api_data.get(key):
                            acc[key] = api_data[key]
            # 添加 API 中有但视频作者中没有的账号
            existing = {a["username"] for a in accounts}
            for a in api_accounts:
                if a["username"] not in existing:
                    accounts.append(a)
        except Exception as e:
            logger.warning("搜索账号补充失败: %s", e)

        # 限流检测
        if await self._is_rate_limited():
            await self._handle_rate_limit()

        logger.info("搜索到 %d 个账号 (来自 %d 条视频)", len(accounts), len(search_videos))
        self._backoff_level = 0

        await self._emit_progress(
            f"搜索完成: 找到 {len(accounts)} 个账号, {len(search_videos)} 条视频",
            {"accounts_found": len(accounts), "videos_found": len(search_videos)}
        )
        return accounts[:max_results]

    @staticmethod
    def _build_accounts_from_videos(videos: list[dict]) -> list[dict]:
        """从视频列表中提取唯一作者, 构建初始账号列表"""
        seen = set()
        accounts = []
        for v in videos:
            uname = v.get("author_unique_id", "")
            if uname and uname not in seen:
                seen.add(uname)
                accounts.append({
                    "username": uname,
                    "nickname": v.get("author_nickname", ""),
                    "avatar": "",
                    "verified": False,
                    "follower_count": 0,
                    "following_count": 0,
                    "video_count": 0,
                    "like_count": 0,
                    "bio": "",
                    "sec_uid": v.get("author_sec_uid", ""),
                    "uid": "",
                    "url": f"https://www.tiktok.com/@{uname}",
                })
        return accounts

    async def _search_videos_api(self, keyword: str, region: str = "", max_results: int = 50) -> list[dict]:
        """通过 API 拦截搜索视频 — 独立方法, 供 run_analysis 预筛选使用"""
        collector = NetworkCollector(self._page)
        search_query = f"{keyword} {region}".strip() if region else keyword
        return await collector.collect_search_videos(search_query, max_results=max_results)

    async def _extract_search_accounts(self) -> list[dict]:
        """从搜索结果页提取账号列表"""
        accounts = []
        try:
            # 方法1: 尝试从 SIGI_STATE 提取（旧版 TikTok）
            sigi = await self._page.evaluate("() => window.SIGI_STATE || null")
            if sigi:
                if isinstance(sigi, str):
                    sigi = json.loads(sigi)
                items = (
                    sigi.get("SearchPage", {})
                    .get("search", {})
                    .get("user", {})
                    .get("list", [])
                )
                if not items:
                    items = (
                        sigi.get("ItemList", {})
                        .get("user", {})
                        .get("list", [])
                    )
                for item in items:
                    user_info = item.get("userInfo", item)
                    accounts.append({
                        "username": user_info.get("uniqueId", user_info.get("id", "")),
                        "nickname": user_info.get("nickname", ""),
                        "avatar": user_info.get("avatarMedium", user_info.get("avatarThumb", "")),
                        "verified": user_info.get("verified", False),
                        "follower_count": user_info.get("followerCount", 0),
                        "following_count": user_info.get("followingCount", 0),
                        "video_count": user_info.get("videoCount", 0),
                        "like_count": user_info.get("heartCount", user_info.get("heart", 0)),
                        "bio": user_info.get("signature", ""),
                        "sec_uid": user_info.get("secUid", ""),
                        "uid": user_info.get("id", ""),
                    })
                if accounts:
                    return accounts

            # 方法2: 新版 TikTok DOM 提取 — DivSearchUserItemContainer
            user_cards = self._page.locator('div[class*="DivSearchUserItemContainer"]')
            count = await user_cards.count()
            for i in range(count):
                try:
                    card = user_cards.nth(i)
                    text = (await card.inner_text()) or ""

                    # 从父级 A 链接提取 username
                    username = ""
                    try:
                        parent_a = card.locator('xpath=ancestor::a[contains(@href, "/@")]')
                        if await parent_a.count() > 0:
                            href = await parent_a.first.get_attribute("href") or ""
                            username = href.split("/@")[-1].split("?")[0].split("/")[0]
                    except Exception:
                        pass

                    # 提取昵称（第一行非空文本，排除 "现场演出" 等标签）
                    lines = [l.strip() for l in text.split("\n") if l.strip()
                             and l.strip() not in ("关注", "现场演出", "Live")]
                    nickname = lines[0] if lines else ""

                    # 提取粉丝数和点赞数（支持 K/M/B/万/米）
                    follower_match = re.search(r"([\d.,]+[KMkmbB]?万?米?)\s*粉丝", text)
                    like_match = re.search(r"([\d.,]+[KMkmbB]?万?米?)\s*赞", text)
                    follower_count = _parse_count(follower_match.group(1)) if follower_match else 0
                    like_count = _parse_count(like_match.group(1)) if like_match else 0

                    if username:
                        accounts.append({
                            "username": username,
                            "nickname": nickname,
                            "avatar": "",
                            "verified": False,
                            "follower_count": follower_count,
                            "following_count": 0,
                            "video_count": 0,
                            "like_count": like_count,
                            "bio": "",
                            "sec_uid": "",
                            "uid": "",
                        })
                except Exception:
                    continue

            # 方法3: 更通用的 DOM 提取 — 查找 DivPanelContainer 下的 @ 链接
            if not accounts:
                panel = self._page.locator('div[class*="DivPanelContainer"]').first
                if await panel.count() > 0:
                    links = panel.locator('a[href*="/@"]')
                    link_count = await links.count()
                    seen = set()
                    for i in range(link_count):
                        try:
                            href = await links.nth(i).get_attribute("href") or ""
                            username = href.split("/@")[-1].split("?")[0].split("/")[0]
                            if username and username not in seen:
                                seen.add(username)
                                accounts.append({
                                    "username": username,
                                    "nickname": "",
                                    "avatar": "",
                                    "verified": False,
                                    "follower_count": 0,
                                    "following_count": 0,
                                    "video_count": 0,
                                    "like_count": 0,
                                    "bio": "",
                                    "sec_uid": "",
                                    "uid": "",
                                })
                        except Exception:
                            continue
        except Exception as e:
            logger.error("提取搜索账号失败: %s", e)

        return accounts

    # ───────────────────── 提取账号详情 ─────────────────────

    async def extract_account_info(self, username: str) -> Optional[dict]:
        """提取单个账号的详细信息，返回 dict 或 None（限流导致的跳过）"""
        username = username.strip().lstrip("@")
        ck_key = f"account_info:{username}"

        # 断点续爬
        if self.checkpoint and self.checkpoint.is_completed("account_info", ck_key):
            logger.info("跳过已抓取账号: %s", username)
            return self.checkpoint.get_scraped_data("account_info", ck_key)

        await self._emit_progress(f"正在提取账号信息: @{username}")

        url = TIKTOK_USER_URL.format(username=username)
        await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(1.5)

        if await self._is_rate_limited():
            if not await self._handle_rate_limit():
                logger.warning("限流耗尽，跳过账号: %s", username)
                return {"username": username, "url": url, "skipped": True, "reason": "rate_limit"}
            await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(1.5)

        if not await self.handle_captcha_if_needed():
            return None

        info = {}

        try:
            sigi = await self._page.evaluate("() => window.SIGI_STATE || null")
            if sigi:
                if isinstance(sigi, str):
                    sigi = json.loads(sigi)

                user_data = (
                    sigi.get("UserModule", {})
                    .get("users", {})
                    .get(username, {})
                )
                if not user_data and "UserPage" in sigi:
                    user_data = sigi.get("UserPage", {}).get("userInfo", {})

                if user_data:
                    stats = user_data.get("stats", user_data)
                    info = {
                        "username": user_data.get("uniqueId", username),
                        "nickname": user_data.get("nickname", ""),
                        "avatar": user_data.get("avatarMedium", user_data.get("avatarLarger", "")),
                        "verified": user_data.get("verified", False),
                        "follower_count": stats.get("followerCount", stats.get("follower", 0)),
                        "following_count": stats.get("followingCount", stats.get("following", 0)),
                        "video_count": stats.get("videoCount", stats.get("video", 0)),
                        "like_count": stats.get("heartCount", stats.get("heart", 0)),
                        "bio": user_data.get("signature", ""),
                        "region": user_data.get("region", ""),
                        "language": user_data.get("language", ""),
                        "location": user_data.get("location", ""),
                        "sec_uid": user_data.get("secUid", ""),
                        "uid": user_data.get("id", ""),
                        "url": url,
                    }

            # 备用：从 NetworkCollector 提取
            if not info:
                try:
                    collector = NetworkCollector(self._page)
                    detail = await collector.collect_account_detail(username)
                    if detail:
                        info = detail
                except Exception:
                    pass

        except Exception as e:
            logger.error("提取账号 %s 信息失败: %s", username, e)
            info = {"username": username, "url": url, "error": str(e)}

        # 保存断点
        if info and self.checkpoint:
            self.checkpoint.mark_scraped("account_info", ck_key, info)

        logger.info("账号 @%s: 粉丝=%s, 点赞=%s", username, info.get("follower_count", "?"), info.get("like_count", "?"))
        return info

    # ───────────────────── 提取视频列表 ─────────────────────

    async def extract_videos(
        self, username: str, max_videos: int = 30,
        enrich_top: int = 0  # P2: 深度补充前 N 条视频的互动数据（0=跳过）
    ) -> list[dict]:
        """提取账号最近 N 条视频信息，返回列表（限流时可能返回空列表）

        Args:
            username: TikTok 用户名
            max_videos: 最多提取视频数
            enrich_top: 对前 N 条视频调用 extract_video_detail 获取精确互动数据（0=跳过）
        """
        username = username.strip().lstrip("@")

        await self._emit_progress(f"正在提取视频: @{username} (目标 {max_videos} 条)")

        # 不复用 new_page() — CDP 模式下每次 new_page() 都会在用户浏览器创建新标签页
        # 直接导航现有页面，单页复用 — CDP 模式下不调 new_page()
        url = TIKTOK_USER_URL.format(username=username)
        await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)

        if await self._is_rate_limited():
            if not await self._handle_rate_limit():
                logger.warning("限流耗尽，跳过视频提取: %s", username)
                return []
            await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(1.5)

        if not await self.handle_captcha_if_needed():
            return []

        # P2 增强: 优先从 SIGI_STATE 提取（精度最高，含互动数据）
        sigi_videos = await self._extract_videos_from_sigi(username)
        if sigi_videos:
            logger.info("@%s: SIGI 提取了 %d 条视频", username, len(sigi_videos))
            await self._emit_progress(
                f"视频提取完成: @{username} ({len(sigi_videos[:max_videos])} 条, SIGI 精确数据)"
            )
            videos = sigi_videos[:max_videos]
        else:
            # SIGI 未命中, 用 NetworkCollector 拦截 API
            try:
                collector = NetworkCollector(self._page)
                api_videos = await collector.collect_account_videos(username, max_videos)
                if api_videos:
                    logger.info("@%s: API 拦截提取了 %d 条视频", username, len(api_videos))
                    videos = api_videos[:max_videos]
                else:
                    videos = []
            except Exception as e:
                logger.warning("@%s: API 视频提取失败: %s", username, e)
                videos = []

        # P2 增强: 按点赞数排序，仅对 Top N 进入详情页补充互动数据
        if enrich_top > 0 and videos:
            sorted_by_likes = sorted(
                videos,
                key=lambda x: x.get("digg_count", 0) or 0,
                reverse=True
            )
            enrich_count = min(enrich_top, len(sorted_by_likes))
            await self._emit_progress(
                f"深度补充互动数据: @{username} 点赞 Top {enrich_count} 条视频..."
            )
            for i in range(enrich_count):
                v = sorted_by_likes[i]
                vid = v.get("id", "")
                if not vid:
                    continue
                detail = await self.extract_video_detail(username, vid)
                if detail:
                    for key in ("play_count", "digg_count", "comment_count",
                                 "share_count", "create_time", "duration",
                                 "desc", "tags", "music"):
                        if key in detail and detail[key]:
                            v[key] = detail[key]
                    logger.debug(
                        "  点赞 #%d (digg=%s): plays=%s, comments=%s",
                        i+1, v.get("digg_count"), v.get("play_count"), v.get("comment_count")
                    )
                await self._sleep_with_jitter()

        logger.info("@%s: 提取了 %d 条视频", username, len(videos))
        return videos

    async def _extract_videos_from_sigi(self, username: str) -> list[dict]:
        """从 SIGI_STATE 提取视频列表（精度最高）"""
        videos = []
        try:
            sigi = await self._page.evaluate("() => window.SIGI_STATE || null")
            if not sigi:
                return videos
            if isinstance(sigi, str):
                sigi = json.loads(sigi)

            # TikTok 用户页 SIGI_STATE 结构: UserPage.items 或 ItemList.user-post
            items = None
            if "UserPage" in sigi:
                items = sigi["UserPage"].get("items", [])
            if not items and "ItemList" in sigi:
                items = sigi["ItemList"].get("user-post", {}).get("list", [])

            if not items:
                return videos

            for item in items:
                try:
                    vid = item.get("id", "")
                    if not vid:
                        continue

                    # 视频信息可能在 item 顶层或 video 子对象
                    video_info = item.get("video", item)
                    stats = item.get("stats", item)

                    videos.append({
                        "id": vid,
                        "desc": (item.get("desc") or "").strip(),
                        "create_time": item.get("createTime", 0),
                        "duration": video_info.get("duration", 0),
                        "play_count": stats.get("playCount", 0),
                        "digg_count": stats.get("diggCount", 0),
                        "comment_count": stats.get("commentCount", 0),
                        "share_count": stats.get("shareCount", 0),
                        "url": f"{TIKTOK_URL}/@{username}/video/{vid}",
                        "tags": [],
                        "music": item.get("music", {}).get("title", "") if isinstance(item.get("music"), dict) else "",
                    })
                except Exception:
                    continue

            if videos:
                logger.debug("SIGI: 提取了 %d 条视频 (UserPage items)", len(videos))
        except Exception as e:
            logger.debug("SIGI 视频提取失败: %s", e)

        return videos

    async def extract_video_detail(self, username: str, video_id: str) -> Optional[dict]:
        """访问单个视频详情页，提取精确的互动数据 + 标签 + 音乐

        用于补充列表页缺失的 engagement 指标（digg/comment/share）。
        返回增量 dict，调用方应 merge 到已有 video dict。
        """
        ck_key = f"video_detail:{video_id}"
        if self.checkpoint and self.checkpoint.is_completed("video_detail", ck_key):
            cached = self.checkpoint.get_scraped_data("video_detail", ck_key)
            if cached:
                return cached

        url = TIKTOK_VIDEO_URL.format(username=username, video_id=video_id)
        try:
            await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)

            if await self._is_rate_limited():
                logger.debug("视频详情页限流: %s", video_id)
                return None

            detail = {}

            # 方法1: SIGI_STATE 提取（最准确）
            sigi = await self._page.evaluate("() => window.SIGI_STATE || null")
            if sigi:
                if isinstance(sigi, str):
                    sigi = json.loads(sigi)

                # ItemModule: 视频详情页主数据
                item_data = None
                if "ItemModule" in sigi and video_id in sigi["ItemModule"]:
                    item_data = sigi["ItemModule"][video_id]
                elif "ItemList" in sigi:
                    item_data = sigi["ItemList"].get(video_id, None)

                if item_data:
                    stats = item_data.get("stats", item_data)
                    detail.update({
                        "play_count": stats.get("playCount", 0),
                        "digg_count": stats.get("diggCount", 0),
                        "comment_count": stats.get("commentCount", 0),
                        "share_count": stats.get("shareCount", 0),
                        "create_time": item_data.get("createTime", 0),
                        "duration": (item_data.get("video", {}) or {}).get("duration", 0)
                                      if isinstance(item_data.get("video"), dict) else 0,
                        "desc": item_data.get("desc", ""),
                        "music": (item_data.get("music", {}) or {}).get("title", "")
                                 if isinstance(item_data.get("music"), dict) else "",
                    })
                    # 标签
                    text_extra = item_data.get("textExtra", [])
                    if text_extra:
                        detail["tags"] = [
                            t.get("hashtagName", "").lstrip("#")
                            for t in text_extra
                            if t.get("hashtagName")
                        ]

            # SIGI 未命中: 不尝试 DOM (受骨架屏影响), 返回空

            # 保存 checkpoint
            if detail and self.checkpoint:
                self.checkpoint.mark_scraped("video_detail", ck_key, detail)

            return detail

        except Exception as e:
            logger.error("提取视频详情失败 %s: %s", video_id, e)
            return None

    async def _extract_video_detail_from_dom(self) -> dict:
        """从视频详情页 DOM 提取互动数据（SIGI 失败时的 fallback）"""
        import re
        detail = {}
        try:
            # 从页面 meta 标签提取
            # <meta data-react-helmet="true" name="description" content="...">
            meta_desc = self._page.locator('meta[name="description"]')
            if await meta_desc.count() > 0:
                content = (await meta_desc.first.get_attribute("content")) or ""
                # 格式: "Likes (123), Comments (45), Shares (67)"
                likes_m = re.search(r"(\d[\d,.]*[KkMm]?)\s*Likes?", content)
                comments_m = re.search(r"(\d[\d,.]*[KkMm]?)\s*Comments?", content)
                if likes_m:
                    detail["digg_count"] = _parse_count(likes_m.group(1))
                if comments_m:
                    detail["comment_count"] = _parse_count(comments_m.group(1))

            # 提取 strong 标签中的互动数
            strong_els = self._page.locator("strong")
            strong_count = await strong_els.count()
            numbers = []
            for i in range(min(strong_count, 10)):
                try:
                    text = (await strong_els.nth(i).inner_text()).strip()
                    if re.match(r"[\d.,]+[KkMmBb]?万?米?", text):
                        numbers.append(_parse_count(text))
                except Exception:
                    pass
            # TikTok 视频页: [点赞, 评论, 收藏, 分享, ...]
            if len(numbers) >= 4:
                if "digg_count" not in detail:
                    detail["digg_count"] = numbers[0]
                if "comment_count" not in detail:
                    detail["comment_count"] = numbers[1]
                if "share_count" not in detail:
                    detail["share_count"] = numbers[3]  # 跳过收藏(2)
            elif len(numbers) >= 2:
                if "digg_count" not in detail:
                    detail["digg_count"] = numbers[0]
                if "comment_count" not in detail:
                    detail["comment_count"] = numbers[1]

        except Exception as e:
            logger.debug("DOM 视频详情提取失败: %s", e)

        return detail

    # ───────────────────── 提取评论 ─────────────────────

    async def _extract_comments_via_network(
        self, video_id: str, max_comments: int = 200
    ) -> list[dict]:
        """P0 修复: 通过浏览器 fetch 直接调用 TikTok comment/list API

        在 CDP 模式下 TikTok 通过 JS 动态渲染评论，DOM 提取始终为 0。
        此方法利用浏览器原生 fetch（自动携带 Cookie），直接分页调用
        TikTok 评论 API (/api/comment/list/)，完全绕过 DOM。

        反检测设计:
        - 请求间隔: random.uniform(0.8, 1.5s)，模拟人类阅读停顿
        - 429 退避: 指数退避 + 自动恢复，最多 max_backoff 级
        - 同源 fetch: Sec-Fetch-* 头部与正常页面 XHR 一致
        - Cookie 继承: credentials: 'include'，sessionid 自动携带

        工作流程:
        1. 在页面上下文中执行 fetch() 调用 comment/list API
        2. 使用 cursor 分页，每次请求 50 条
        3. 429 时退避重试，403/401 时告警并退出
        4. 去重后返回

        优势:
        - 不依赖 DOM 渲染/滚动/按钮点击
        - 浏览器 Cookie 自动携带（反爬友好）
        - cursor 分页比滚动触发更可靠、更可控
        """
        comments: list[dict] = []
        seen_cids: set[str] = set()
        cursor = 0
        max_pages = max(max_comments // 50, 1) + 5
        consecutive_empty = 0
        api_total = 0  # API 返回的总评论数（首页获取）

        for page in range(max_pages):
            if len(comments) >= max_comments:
                break
            if consecutive_empty >= 3:
                break

            # 在浏览器中直接 fetch TikTok 评论 API
            result = await self._page.evaluate(
                """
                async ([aweme_id, cursor_val, count]) => {
                    const url = `https://www.tiktok.com/api/comment/list/?aid=1988&aweme_id=${aweme_id}&cursor=${cursor_val}&count=${count}`;
                    try {
                        const resp = await fetch(url, { credentials: 'include' });
                        if (!resp.ok) {
                            return {
                                error: 'HTTP ' + resp.status,
                                http_status: resp.status,
                                comments: []
                            };
                        }
                        const data = await resp.json();
                        return {
                            comments: data.comments || [],
                            has_more: data.has_more || false,
                            cursor: data.cursor || 0,
                            total: data.total || 0,
                        };
                    } catch (err) {
                        return { error: err.message, comments: [] };
                    }
                }
                """,
                [video_id, cursor, 50],
            )

            # 处理 API 错误
            if not result:
                logger.warning("comment/list API 第 %d 页无响应", page + 1)
                break

            http_status = result.get("http_status", 0)

            if http_status == 429:
                # 触发退避
                logger.warning("comment/list API 429 限流，触发退避...")
                self._backoff_level = min(self._backoff_level + 1, self._max_backoff)
                delay = self._random_delay()
                logger.info("退避等待 %.1fs (级别 %d)", delay, self._backoff_level)
                await asyncio.sleep(delay)
                if self._backoff_level >= self._max_backoff:
                    break
                continue  # 重试当前页

            if http_status in (403, 401):
                logger.warning(
                    "comment/list API 返回 %d — 可能需要 msToken/webId 签名升级",
                    http_status
                )
                break

            if result.get("error") and http_status != 429:
                logger.warning(
                    "comment/list API 第 %d 页失败: %s",
                    page + 1, result.get("error")
                )
                break

            batch = result.get("comments", [])
            if not batch:
                consecutive_empty += 1
                if not result.get("has_more"):
                    break
                cursor = result.get("cursor", cursor + 50)
                continue

            # 成功拿到评论，重置退避级别
            if self._backoff_level > 0:
                self._backoff_level = max(0, self._backoff_level - 1)

            # 记录 API 总评论数（用于日志对比）
            if api_total == 0:
                api_total = result.get("total", 0)

            consecutive_empty = 0
            for c in batch:
                cid = c.get("cid", "") or c.get("comment_id", "") or c.get("id", "")
                if not cid or cid in seen_cids:
                    continue
                seen_cids.add(cid)

                user = c.get("user", {}) or {}
                comments.append({
                    "video_id": video_id,
                    "text": c.get("text", ""),
                    "username": (
                        user.get("unique_id", "")
                        or user.get("uniqueId", "")
                        or user.get("uid", "")
                    ),
                    "likes": c.get("digg_count", 0) or c.get("like_count", 0),
                    "time": c.get("create_time", "") or c.get("createTime", ""),
                })

            # 下一页
            cursor = result.get("cursor", cursor + 50)
            if not result.get("has_more"):
                break

            # API 请求间隔（带随机抖动，模拟人类阅读评论的自然停顿）
            await asyncio.sleep(random.uniform(0.8, 1.5))

        logger.info(
            "API直调: 视频 %s 提取了 %d/%d 条评论 (%d 页)",
            video_id, len(comments), api_total, page + 1
        )
        return comments[:max_comments]

    async def _extract_comments_via_response_intercept(
        self, video_id: str, max_comments: int = 200
    ) -> list[dict]:
        """P0 修复: 通过 page.on("response") 被动拦截 TikTok 自身的 XHR 响应

        在 CDP 模式下，TikTok 页面会自动请求 comment/list API 加载评论。
        此方法被动监听浏览器网络响应并提取评论 JSON，完全不需要自己构造
        API 请求，因此绕过了签名/CORS/CSP/msToken 等所有问题。

        相比 fetch-based 方式的优势:
        - 浏览器自身发起的请求，自动携带所有签名/headers/Cookie
        - 无 CORS/CSP 策略限制
        - 不需要主动构造 API URL 或 cursor 参数

        工作流程:
        1. 由调用方在 page.goto() 之前注册 page.on("response") 监听器
        2. 本方法等待页面加载 + 滚动以触发评论 API 请求
        3. 从调用方传入的 captured_responses 列表中提取评论数据
        """
        comments: list[dict] = []
        seen_cids: set[str] = set()
        captured: list[dict] = []  # 由 extract_comments 注入

        # 等待 TikTok 页面自行加载评论（页面加载后 3-8 秒内通常发出 API 请求）
        await asyncio.sleep(3)

        # 滚动触发更多评论加载（TikTok 按需加载评论）
        for _ in range(5):
            try:
                await self._page.evaluate("window.scrollBy(0, 600)")
                await asyncio.sleep(1.0 + random.random() * 0.5)
            except Exception:
                break

        # 额外等待，确保异步请求完成
        await asyncio.sleep(1)

        # 使用外部传入的 captured_responses
        captured = getattr(self, "_captured_comment_responses", [])
        if not captured:
            logger.info("响应拦截: 视频 %s 未捕获到任何 comment/list 响应", video_id)
            return []

        # 处理所有拦截到的响应
        for data in captured:
            batch = data.get("comments", [])
            for c in batch:
                cid = c.get("cid", "") or c.get("comment_id", "") or c.get("id", "")
                if not cid or cid in seen_cids:
                    continue
                seen_cids.add(cid)
                user = c.get("user", {}) or {}
                comments.append({
                    "video_id": video_id,
                    "text": c.get("text", ""),
                    "username": (
                        user.get("unique_id", "")
                        or user.get("uniqueId", "")
                        or user.get("uid", "")
                    ),
                    "likes": c.get("digg_count", 0) or c.get("like_count", 0),
                    "time": c.get("create_time", "") or c.get("createTime", ""),
                })

        logger.info(
            "响应拦截: 视频 %s 捕获 %d 个 API 响应, 提取 %d 条评论",
            video_id, len(captured), len(comments)
        )
        return comments[:max_comments]

    async def _sample_comments(self, video_id: str, count: int = 8,
                              strategy: str = "first_n") -> list[dict]:
        """P1: 轻量评论采样 — 不导航页面，在当前页用 fetch() 直调 API

        采样策略:
          - first_n:          默认，前 N 条 (TikTok hot 排序)
          - top_and_latest:   Top N/2 + 更深分页 N/2 (模拟 hot+recent 混合)
          - random_from_N:    抓取 N 条，从中随机取 count 条
          - pool_random:      先抓 pool_size 条，再随机取 count 条

        对比 extract_comments():
          - _sample_comments: ~1.5s，1-2 次 API 调用，不导航页面
          - extract_comments:  ~10s，page.goto + 等待加载 + 分页抓取
        """
        try:
            if strategy == "first_n":
                return await self._sample_first_n(video_id, count)
            elif strategy == "top_and_latest":
                return await self._sample_top_and_latest(video_id, count)
            elif strategy == "random_from_N":
                return await self._sample_random_from_pool(video_id, count, pool_size=count)
            elif strategy == "pool_random":
                return await self._sample_random_from_pool(video_id, count, pool_size=max(count * 3, 30))
            else:
                return await self._sample_first_n(video_id, count)
        except Exception:
            logger.exception(
                "_sample_comments vid=%s: 采样异常 (page状态/网络/CDP连接?)", video_id
            )
            return []

    async def _sample_first_n(self, video_id: str, count: int) -> list[dict]:
        """策略 A: 前 N 条评论 (TikTok 默认 hot 排序)

        三级 fallback:
          1. page.evaluate() + fetch() — 浏览器内 fetch, 最自然
          2. page.evaluate() 同步 XHR  — 无异步依赖
          3. page.request.get()       — Playwright 原生, 绕过 JS 上下文
        """
        # ── 方法 1: page.evaluate() 异步 fetch ──
        try:
            result = await self._page.evaluate("""
                async (videoId, count) => {
                    try {
                        const url = `https://www.tiktok.com/api/comment/list/?aid=1988&aweme_id=${videoId}&count=${count}&cursor=0`;
                        const res = await fetch(url);
                        if (!res.ok) {
                            return {error: 'HTTP ' + res.status, url: url};
                        }
                        const data = await res.json();
                        const comments = (data.comments || []).map(c => ({
                            text: c.text || '',
                            likes: c.digg_count || 0,
                            username: c.user?.unique_id || '',
                            time: c.create_time || 0,
                        }));
                        return {ok: true, count: comments.length, comments: comments};
                    } catch(e) {
                        return {error: e.message || String(e)};
                    }
                }
            """, video_id, count)

            if isinstance(result, dict) and "error" in result:
                logger.warning(
                    "_sample_first_n vid=%s [fetch]: %s", video_id, result["error"]
                )
            elif isinstance(result, dict) and result.get("ok"):
                if result["count"] == 0:
                    logger.info(
                        "_sample_first_n vid=%s [fetch]: API OK but 0 comments",
                        video_id
                    )
                else:
                    logger.info(
                        "_sample_first_n vid=%s [fetch]: 成功抓取 %d 条评论",
                        video_id, result["count"]
                    )
                return result.get("comments", [])
            else:
                logger.warning(
                    "_sample_first_n vid=%s [fetch]: 意外返回值 type=%s, 尝试 fallback",
                    video_id, type(result).__name__
                )
        except Exception as e:
            logger.warning(
                "_sample_first_n vid=%s [fetch]: page.evaluate 异常 — %s: %s",
                video_id, type(e).__name__, str(e)[:200]
            )

        # ── 方法 2: page.request.get() — Playwright 原生 HTTP, 共享 cookie jar ──
        try:
            url = f"https://www.tiktok.com/api/comment/list/?aid=1988&aweme_id={video_id}&count={count}&cursor=0"
            resp = await self._page.request.get(url, headers={
                "Referer": "https://www.tiktok.com/",
                "Accept": "application/json, text/plain, */*",
            })
            if resp.ok:
                data = await resp.json()
                comments_raw = data.get("comments", [])
                comments = []
                for c in comments_raw:
                    user = c.get("user", {}) or {}
                    comments.append({
                        "video_id": video_id,
                        "text": c.get("text", ""),
                        "username": user.get("unique_id", user.get("uniqueId", "")),
                        "likes": c.get("digg_count", 0) or c.get("like_count", 0),
                        "time": c.get("create_time", "") or c.get("createTime", ""),
                    })
                logger.info(
                    "_sample_first_n vid=%s [request]: 成功抓取 %d 条评论 (API total=%s)",
                    video_id, len(comments), data.get("total", "?")
                )
                return comments[:count]
            else:
                logger.warning(
                    "_sample_first_n vid=%s [request]: HTTP %s",
                    video_id, resp.status
                )
        except Exception as e:
            logger.warning(
                "_sample_first_n vid=%s [request]: 异常 — %s: %s",
                video_id, type(e).__name__, str(e)[:200]
            )

        logger.error(
            "_sample_first_n vid=%s: 所有方法均失败, 返回 0 条评论", video_id
        )
        return []

    async def _sample_top_and_latest(self, video_id: str, count: int) -> list[dict]:
        """策略 B: Top N/2 (cursor=0) + 更深分页 N/2 (cursor=N)

        通过混合 hot 评论和较新/较低热度评论来增加样本多样性，
        提高捕获偶然商业评论的概率。
        """
        half = max(1, count // 2)
        try:
            result = await self._page.evaluate("""
                async (videoId, count, half) => {
                    try {
                        const base = 'https://www.tiktok.com/api/comment/list/'
                                   + '?aid=1988&aweme_id=' + videoId;
                        const [topRes, laterRes] = await Promise.all([
                            fetch(base + '&count=' + half + '&cursor=0'),
                            fetch(base + '&count=' + (count - half) + '&cursor=' + count)
                        ]);
                        const topData = await topRes.json();
                        const laterData = await laterRes.json();
                        const top = (topData.comments || []).map(c => ({
                            text: c.text || '', likes: c.digg_count || 0,
                            username: c.user?.unique_id || '', time: c.create_time || 0,
                        }));
                        const later = (laterData.comments || []).map(c => ({
                            text: c.text || '', likes: c.digg_count || 0,
                            username: c.user?.unique_id || '', time: c.create_time || 0,
                        }));
                        return {ok: true, comments: [...top, ...later]};
                    } catch(e) { return {error: e.message || String(e)}; }
                }
            """, video_id, count, half)
            if isinstance(result, dict) and result.get("ok"):
                comments = result.get("comments", [])
                logger.info(
                    "_sample_top_and_latest vid=%s [fetch]: %d 条评论",
                    video_id, len(comments)
                )
                return comments
            elif isinstance(result, dict) and "error" in result:
                logger.warning(
                    "_sample_top_and_latest vid=%s [fetch]: %s", video_id, result["error"]
                )
            else:
                logger.warning(
                    "_sample_top_and_latest vid=%s [fetch]: 意外返回值 type=%s",
                    video_id, type(result).__name__
                )
        except Exception as e:
            logger.warning(
                "_sample_top_and_latest vid=%s [fetch]: page.evaluate 异常 — %s: %s",
                video_id, type(e).__name__, str(e)[:200]
            )

        # fallback: page.request (降级为 first_n, 无法模拟 top+latest 混合)
        logger.info("_sample_top_and_latest vid=%s: 降级为 page.request first_n", video_id)
        return await self._sample_first_n(video_id, count)

    async def _sample_random_from_pool(
        self, video_id: str, count: int, pool_size: int = 30
    ) -> list[dict]:
        """策略 C/D: 从 pool_size 条评论中随机抽取 count 条

        先抓取 pool_size 条 (cursor=0)，再在 Python 侧随机采样。
        这避免了仅看 Top N 导致的"只看热门评论"偏差，
        可能捕获到被热评压制的商业询盘。
        """
        import random as _random

        def _extract_comments_from_result(result) -> list[dict]:
            """从 fetch 结果中提取评论列表"""
            if isinstance(result, dict) and result.get("ok"):
                return result.get("comments", [])
            return []

        # ── 方法 1: page.evaluate() + fetch ──
        pool = []
        try:
            result = await self._page.evaluate("""
                async (videoId, poolSize) => {
                    try {
                        const url = `https://www.tiktok.com/api/comment/list/?aid=1988&aweme_id=${videoId}&count=${poolSize}&cursor=0`;
                        const res = await fetch(url);
                        if (!res.ok) {
                            return {error: 'HTTP ' + res.status};
                        }
                        const data = await res.json();
                        const comments = (data.comments || []).map(c => ({
                            text: c.text || '',
                            likes: c.digg_count || 0,
                            username: c.user?.unique_id || '',
                            time: c.create_time || 0,
                        }));
                        return {ok: true, count: comments.length, comments: comments};
                    } catch(e) {
                        return {error: e.message || String(e)};
                    }
                }
            """, video_id, pool_size)

            if isinstance(result, dict) and "error" in result:
                logger.warning(
                    "_sample_random_from_pool vid=%s [fetch]: %s", video_id, result["error"]
                )
            elif isinstance(result, dict) and result.get("ok"):
                pool = result.get("comments", [])
                logger.info(
                    "_sample_random_from_pool vid=%s [fetch]: 抓取 %d 条 (池=%d)",
                    video_id, len(pool), pool_size
                )
            else:
                logger.warning(
                    "_sample_random_from_pool vid=%s [fetch]: 意外返回值 type=%s",
                    video_id, type(result).__name__
                )
        except Exception as e:
            logger.warning(
                "_sample_random_from_pool vid=%s [fetch]: page.evaluate 异常 — %s: %s",
                video_id, type(e).__name__, str(e)[:200]
            )

        # ── 方法 2: page.request.get() fallback ──
        if not pool:
            try:
                url = f"https://www.tiktok.com/api/comment/list/?aid=1988&aweme_id={video_id}&count={pool_size}&cursor=0"
                resp = await self._page.request.get(url, headers={
                    "Referer": "https://www.tiktok.com/",
                    "Accept": "application/json, text/plain, */*",
                })
                if resp.ok:
                    data = await resp.json()
                    comments_raw = data.get("comments", [])
                    for c in comments_raw:
                        user = c.get("user", {}) or {}
                        pool.append({
                            "video_id": video_id,
                            "text": c.get("text", ""),
                            "username": user.get("unique_id", user.get("uniqueId", "")),
                            "likes": c.get("digg_count", 0) or c.get("like_count", 0),
                            "time": c.get("create_time", "") or c.get("createTime", ""),
                        })
                    logger.info(
                        "_sample_random_from_pool vid=%s [request]: 抓取 %d 条",
                        video_id, len(pool)
                    )
                else:
                    logger.warning(
                        "_sample_random_from_pool vid=%s [request]: HTTP %s",
                        video_id, resp.status
                    )
            except Exception as e:
                logger.warning(
                    "_sample_random_from_pool vid=%s [request]: 异常 — %s: %s",
                    video_id, type(e).__name__, str(e)[:200]
                )

        if not pool:
            logger.error(
                "_sample_random_from_pool vid=%s: 所有方法均失败", video_id
            )
            return []

        if len(pool) <= count:
            return pool
        return _random.sample(pool, count)

    async def extract_comments(self, username: str, video_id: str, max_comments: int = 200) -> list[dict]:
        """提取单条视频的评论，返回列表（限流时可能返回空列表）

        提取策略（按优先级，逐级 fallback）:
        0. 响应拦截: page.on("response") 监听浏览器自身的 XHR
           — CDP 模式首选，被动拦截，无签名/跨域问题
        1. API 直调: 浏览器内 fetch() 直接调用 comment/list，cursor 分页
           — fallback，主动分页获取更多评论
        2. JS 全局状态: 从 __UNIVERSAL_DATA_FOR_REHYDRATION__ / SIGI_STATE 提取
        3. DOM 提取: 传统 DOM 解析（非 CDP 模式最后手段）
        """
        ck_key = f"comments:{video_id}"

        if self.checkpoint and self.checkpoint.is_completed("comments", ck_key):
            logger.info("跳过已抓取评论: %s", video_id)
            cached = self.checkpoint.get_scraped_data("comments", ck_key)
            return cached.get("comments", []) if cached else []

        await self._emit_progress(f"正在提取评论: {video_id}")

        url = TIKTOK_VIDEO_URL.format(username=username, video_id=video_id)

        # ── 策略0: 响应拦截（CDP 模式首选，导航前注册监听器）──
        self._captured_comment_responses: list[dict] = []

        async def _on_comment_response(response):
            try:
                if "/api/comment/list/" in response.url and response.ok:
                    data = await response.json()
                    self._captured_comment_responses.append(data)
            except Exception:
                pass

        self._page.on("response", _on_comment_response)

        try:
            await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)

            if await self._is_rate_limited():
                if not await self._handle_rate_limit():
                    logger.warning("限流耗尽，跳过评论提取: %s", video_id)
                    return []
                await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)

            if not await self.handle_captcha_if_needed():
                return []

            # 使用响应拦截提取评论
            intercept_comments = await self._extract_comments_via_response_intercept(video_id, max_comments)
            if intercept_comments:
                logger.info("响应拦截: 提取了 %d 条评论", len(intercept_comments))
                if self.checkpoint:
                    self.checkpoint.mark_scraped("comments", ck_key, {"comments": intercept_comments})
                return intercept_comments[:max_comments]
        finally:
            # 清理监听器
            try:
                self._page.remove_listener("response", _on_comment_response)
            except Exception:
                pass

        # ── 策略1: 直接调用 comment/list API（fallback: 主动分页获取更多评论）──
        network_comments = await self._extract_comments_via_network(video_id, max_comments)
        if network_comments:
            logger.info("API直接调用: 提取了 %d 条评论", len(network_comments))
            if self.checkpoint:
                self.checkpoint.mark_scraped("comments", ck_key, {"comments": network_comments})
            return network_comments[:max_comments]

        # ── 策略2: JS 全局状态提取（fallback）──
        js_comments = await self._extract_comments_from_js()
        if js_comments:
            logger.info("从 JS 全局状态提取了 %d 条评论", len(js_comments))
            if self.checkpoint:
                self.checkpoint.mark_scraped("comments", ck_key, {"comments": js_comments})
            return js_comments[:max_comments]

        # 所有策略均未获取到评论
        logger.info("视频 %s: 未提取到评论 (所有策略均无数据)", video_id)
        if self.checkpoint:
            self.checkpoint.mark_scraped("comments", ck_key, {"comments": []})
        return []

    async def _extract_comments_from_dom(self, video_id: str, max_comments: int = 200) -> list[dict]:
        """DOM 提取评论（传统方式，非 CDP 模式 fallback）"""
        # 等待评论加载
        comment_loaded = False
        for wait_sel in [
            '[data-e2e="comment-item"]',
            '[class*="DivCommentItem"]',
            '[class*="CommentItem"]',
            'div[class*="comment"]',
        ]:
            try:
                await self._page.wait_for_selector(wait_sel, timeout=8000)
                count = await self._page.locator(wait_sel).count()
                if count > 0:
                    logger.info("评论已加载: %s (找到 %d 个元素)", wait_sel, count)
                    comment_loaded = True
                    break
            except Exception:
                continue

        if not comment_loaded:
            try:
                await self._page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            await asyncio.sleep(2)

        comments = []
        scroll_count = 0
        max_scrolls = (max_comments // 8) + 15
        seen_texts = set()

        COMMENT_ITEM_SEL = (
            'div[data-e2e="comment-item"], '
            'div[class*="CommentItem"], div[class*="DivCommentItem"], '
            'div[class*="comment-item"], div[class*="comment-container"], '
            'div[class*="CommentList"] > div, '
            'div[class*="comment-list"] > div'
        )
        COMMENT_TEXT_SEL = (
            '[data-e2e="comment-text"], '
            'p[class*="comment"], span[class*="text"], '
            '[class*="CommentText"], [class*="comment-content"], '
            'span[class*="ContentText"]'
        )
        COMMENT_USER_SEL = (
            '[data-e2e="comment-username"], '
            'a[class*="username"], span[class*="user"], '
            '[class*="CommentUserName"], [class*="author"], '
            'a[class*="UserName"]'
        )
        COMMENT_LIKES_SEL = (
            '[data-e2e="comment-like-count"], '
            'span[class*="like"], [class*="CommentLike"], '
            'span[class*="LikeCount"]'
        )
        COMMENT_TIME_SEL = (
            'span[class*="time"], span[class*="date"], '
            '[class*="CommentTime"], time'
        )

        while len(comments) < max_comments and scroll_count < max_scrolls:
            try:
                comment_els = self._page.locator(COMMENT_ITEM_SEL)
                count = await comment_els.count()

                for i in range(count):
                    if len(comments) >= max_comments:
                        break
                    try:
                        el = comment_els.nth(i)

                        text_el = el.locator(COMMENT_TEXT_SEL)
                        text = ""
                        if await text_el.count() > 0:
                            text = (await text_el.first.inner_text()).strip()

                        if not text:
                            text = (await el.inner_text()).strip()
                            if "\n" in text:
                                lines = text.split("\n")
                                text = "\n".join(lines[1:3]) if len(lines) > 1 else text

                        if not text or text in seen_texts:
                            continue

                        seen_texts.add(text)

                        user_el = el.locator(COMMENT_USER_SEL)
                        comment_user = ""
                        if await user_el.count() > 0:
                            comment_user = (await user_el.first.inner_text()).strip()

                        likes_el = el.locator(COMMENT_LIKES_SEL)
                        likes = 0
                        if await likes_el.count() > 0:
                            likes = _parse_count((await likes_el.first.inner_text()).strip())

                        time_el = el.locator(COMMENT_TIME_SEL)
                        comment_time = ""
                        if await time_el.count() > 0:
                            comment_time = (await time_el.first.inner_text()).strip()

                        comments.append({
                            "video_id": video_id,
                            "text": text,
                            "username": comment_user,
                            "likes": likes,
                            "time": comment_time,
                        })
                    except Exception:
                        continue

            except Exception as e:
                logger.error("DOM 提取评论失败: %s", e)

            if len(comments) >= max_comments:
                break

            await self._page.evaluate("""
                (() => {
                    const container = document.querySelector(
                        '[class*="CommentListContainer"], ' +
                        '[class*="comment-list-container"], ' +
                        '[class*="DivCommentListContainer"], ' +
                        'div[class*="DivCommentList"], ' +
                        '[class*="comment"]'
                    );
                    if (container && container.scrollHeight > container.clientHeight) {
                        container.scrollBy(0, 800);
                    } else {
                        window.scrollBy(0, 800);
                    }
                })();
            """)
            await self._sleep_with_jitter()
            scroll_count += 1

            if not await self.handle_captcha_if_needed():
                break

            if await self._is_rate_limited():
                if not await self._handle_rate_limit():
                    break

        return comments

    async def _extract_comments_from_js(self) -> list[dict]:
        """P0修复: 从 TikTok JS 全局状态提取评论（绕过DOM等待问题）"""
        try:
            result = await self._page.evaluate("""
                (() => {
                    // 方法1: __UNIVERSAL_DATA_FOR_REHYDRATION__ (新版TikTok)
                    const udr = window.__UNIVERSAL_DATA_FOR_REHYDRATION__;
                    if (udr) {
                        const data = udr.__DEFAULT_SCOPE__ || udr;
                        // 遍历查找评论数据
                        function findComments(obj, depth) {
                            if (!obj || typeof obj !== 'object' || depth > 8) return null;
                            if (Array.isArray(obj) && obj.length > 0 && obj[0] && obj[0].text) {
                                return obj;
                            }
                            for (const key of Object.keys(obj)) {
                                if (key.includes('comment') || key === 'list' || key === 'items') {
                                    const found = findComments(obj[key], depth + 1);
                                    if (found) return found;
                                }
                            }
                            return null;
                        }
                        const comments = findComments(data, 0);
                        if (comments && comments.length > 0) {
                            return comments.slice(0, 500).map(c => ({
                                text: c.text || c.content || '',
                                username: c.uniqueId || c.user?.uniqueId || c.author?.uniqueId || '',
                                likes: c.likes || c.likeCount || c.diggCount || 0,
                                time: c.createTime ? new Date(c.createTime * 1000).toISOString() : '',
                                reply_count: c.replyCount || c.replyTotal || c.subCommentCount || 0
                            }));
                        }
                    }

                    // 方法2: SIGI_STATE (旧版TikTok)
                    const sigi = window.SIGI_STATE;
                    if (sigi) {
                        const data = typeof sigi === 'string' ? JSON.parse(sigi) : sigi;
                        // ItemModule 可能包含评论
                        const items = data?.ItemModule || {};
                        for (const vid of Object.keys(items)) {
                            const item = items[vid];
                            if (item?.commentList && Array.isArray(item.commentList)) {
                                return item.commentList.slice(0, 500).map(c => ({
                                    text: c.text || c.content || '',
                                    username: c.uniqueId || c.user?.uniqueId || '',
                                    likes: c.likes || c.likeCount || c.diggCount || 0,
                                    time: c.createTime ? new Date(c.createTime * 1000).toISOString() : '',
                                    reply_count: c.replyCount || c.subCommentCount || 0
                                }));
                            }
                        }
                    }

                    return null;
                })();
            """)

            if result and isinstance(result, list) and len(result) > 0:
                # 补充 video_id
                for c in result:
                    c["video_id"] = c.get("video_id", "")  # JS提取的可能没有
                return result
        except Exception as e:
            logger.debug("JS 评论提取失败: %s", e)

        return []

    # ───────────────────── P0 视频预筛选 ─────────────────────

    @staticmethod
    def _load_prefilter_config() -> dict:
        """从 config.yaml 加载 video_prefilter 配置（不含 yaml 依赖时回退默认值）"""
        try:
            import yaml
            cfg_path = Path(__file__).parent.parent / "config.yaml"
            if cfg_path.exists():
                with open(cfg_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                    return data.get("video_prefilter", {})
        except Exception:
            pass
        return {}

    @staticmethod
    @staticmethod
    def _compute_intent_score(video: dict, comments: list[dict] = None) -> int:
        """计算视频的购买意图评分 (0-100)。

        基于视频描述 + 采样评论文本中的采购关键词命中。
        关键词权重: buy/price/supplier/MOQ/order/link 各 +15，上限 100。
        """
        INTENT_KEYWORDS = {
            "buy": 15, "purchase": 15, "order": 15, "price": 15,
            "supplier": 15, "moq": 15, "manufacturer": 10, "factory": 10,
            "wholesale": 10, "link": 10, "sample": 10, "shipping": 10,
            "inquiry": 10, "quotation": 10, "export": 10, "custom": 5,
        }
        text_parts = [(video.get("desc") or "").lower()]
        if comments:
            for c in comments:
                text_parts.append((c.get("text") or "").lower())
        combined = " ".join(text_parts)

        score = 0
        for kw, weight in INTENT_KEYWORDS.items():
            if kw in combined:
                score += weight
        return min(score, 100)

    @staticmethod
    def _apply_video_prefilter(videos: list[dict], config: dict = None) -> tuple[list[dict], list[dict]]:
        """P0 视频硬过滤（优先级最高）：标记质量 + 过滤低互动视频。

        过滤规则 (任一不达标即标记为 low_quality):
          1. likes < 10 → low_quality
          2. comments == 0 → low_quality
          3. likes < 10 AND comments < 3 → low_quality (无 engagement)

        所有视频都会被标记 high_quality / low_quality。
        low_quality 视频不进入后续评论抓取和分析流程。

        Returns:
            (high_quality_videos, low_quality_videos)
        """
        if config is None:
            config = TikTokScraper._load_prefilter_config()
        prefilter_cfg = config.get("video_prefilter", config) if isinstance(config, dict) else {}

        if "video_prefilter" in config:
            prefilter_cfg = config["video_prefilter"]

        if not prefilter_cfg.get("enabled", True):
            # 即使禁用预筛选，仍标记质量
            for v in videos:
                v["quality"] = "high_quality"
            return videos, []

        # P0 硬过滤阈值 (不受 config 覆盖，最高优先级)
        MIN_LIKES = prefilter_cfg.get("min_digg", 10)
        MIN_COMMENTS = prefilter_cfg.get("min_comments", 1)
        MIN_PLAYS = prefilter_cfg.get("min_plays", 100)

        high_quality, low_quality = [], []
        for v in videos:
            digg = v.get("digg_count", 0) or 0
            comments = v.get("comment_count", 0) or 0
            plays = v.get("play_count", 0) or 0

            # P0 硬过滤判断
            is_low = False
            if digg < MIN_LIKES:
                is_low = True
            elif comments < MIN_COMMENTS:
                is_low = True
            elif digg < MIN_LIKES and comments < 3:  # 无 engagement
                is_low = True
            elif MIN_PLAYS > 0 and plays < MIN_PLAYS:
                is_low = True

            v["quality"] = "low_quality" if is_low else "high_quality"
            if is_low:
                low_quality.append(v)
            else:
                high_quality.append(v)

        if low_quality:
            logger.info(
                "P0 硬过滤: %d high_quality / %d low_quality (条件: likes>=%d, comments>=%d, plays>=%d)",
                len(high_quality), len(videos), MIN_LIKES, MIN_COMMENTS, MIN_PLAYS
            )
        return high_quality, low_quality

    # ───────────────────── 完整工作流 ─────────────────────

    async def run_analysis(
        self,
        keywords: list[str],
        region: str = "",
        accounts_per_keyword: int = 10,
        videos_per_account: int = 30,
        comments_per_video: int = 200,
        comments_video_count: int = 3,
        enrich_top: int = 0,
        # 新管道参数
        sample_comment_count: int = 8,        # 阶段 2 采样评论数
        comment_sampling_strategy: str = "first_n",  # first_n | top_and_latest | pool_random
        deep_comment_count: int = 40,         # 阶段 3 深度抓取评论数
        validation_mode: bool = False,        # 验证模式: 保留采样评论 + 返回漏斗统计
    ) -> dict:
        """执行完整分析流程 (新管道)

        阶段 0: 搜索 + 跨关键词去重
        阶段 1: QuickScore 无评论快速评分 + 激进淘汰 (~70-80%)
        阶段 2: 轻量评论采样 (8条) + QuickIntentScanner → 筛选有意图视频
        阶段 3: 深度评论抓取 (30-50条) — 仅对 ~10% 视频
        """
        from .unified_scorer import QuickScorer, QuickScorerConfig
        from .intent_detector import QuickIntentScanner

        # ── 加载 QuickScorer 配置 ──
        qs_cfg = QuickScorerConfig()
        try:
            import yaml
            from pathlib import Path
            cfg_path = Path(__file__).parent.parent / "config.yaml"
            if cfg_path.exists():
                with open(cfg_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                    qs_data = data.get("quick_scorer", {})
                    if qs_data:
                        qs_cfg = QuickScorerConfig(**{
                            k: v for k, v in qs_data.items()
                            if k in QuickScorerConfig.__dataclass_fields__
                        })
        except Exception:
            pass

        quick_scorer = QuickScorer(qs_cfg)
        quick_scanner = QuickIntentScanner(min_hits=1)

        # ── 关键词上限: 最多 10 个，超出截断 ──
        MAX_KEYWORDS = 10
        if len(keywords) > MAX_KEYWORDS:
            logger.warning("关键词过多 (%d)，截断为前 %d 个", len(keywords), MAX_KEYWORDS)
            keywords = keywords[:MAX_KEYWORDS]

        await self._emit_progress(
            f"开始分析: {len(keywords)} 个关键词, 目标 {len(keywords) * accounts_per_keyword} 个账号"
        )

        # ═══════════════════════════════════════════════════════════
        # 阶段 0: 搜索 + 跨关键词去重
        # ═══════════════════════════════════════════════════════════
        await self._emit_progress("阶段 0: 搜索 + 去重...")

        all_accounts = []
        seen_video_ids: dict[str, dict] = {}  # vid → video (存 play_count 最高的)
        seen_accounts: dict[str, dict] = {}   # username → account
        total_search_vids = 0

        for kw in keywords:
            await self._emit_progress(f"搜索关键词: {kw}")

            # 搜索账号
            accounts = await self.search_accounts(kw, region, accounts_per_keyword)
            for a in accounts:
                uname = a.get("username", "")
                if not uname:
                    continue
                if uname not in seen_accounts:
                    seen_accounts[uname] = a
                else:
                    # 保留 follower 更多的版本
                    existing = seen_accounts[uname]
                    if a.get("follower_count", 0) > existing.get("follower_count", 0):
                        seen_accounts[uname] = a

            # 搜索视频 (带跨关键词去重)
            try:
                search_vids = await self._search_videos_api(kw, region, max_results=50)
                total_search_vids += len(search_vids)
                for v in search_vids:
                    vid = v.get("id", "")
                    if not vid:
                        continue
                    v["account_username"] = v.get("author_unique_id", "")
                    if vid not in seen_video_ids:
                        v["_source_keywords"] = [kw]
                        seen_video_ids[vid] = v
                    else:
                        # 合并来源关键词
                        existing_kw = seen_video_ids[vid].get("_source_keywords", [])
                        if kw not in existing_kw:
                            existing_kw.append(kw)
                        seen_video_ids[vid]["_source_keywords"] = existing_kw
                        # 保留 play_count 更高的版本
                        if v.get("play_count", 0) > seen_video_ids[vid].get("play_count", 0):
                            v["_source_keywords"] = existing_kw
                            seen_video_ids[vid] = v
                logger.info("关键词 %s: 搜索 API 返回 %d 条视频", kw, len(search_vids))
            except Exception as e:
                logger.warning("搜索视频采集失败: %s", e)

            if self.checkpoint:
                self.checkpoint.mark_stage(f"search_{kw}", "completed", f"找到 {len(accounts)} 个账号")

            if self._cancelled:
                await self._emit_progress("⏹ 用户取消 (搜索阶段)，返回已采集的部分数据...")
                break

        all_accounts = list(seen_accounts.values())
        all_search_videos = list(seen_video_ids.values())

        await self._emit_progress(
            f"阶段 0 完成: {len(all_accounts)} 个去重账号, "
            f"{len(all_search_videos)} 条去重搜索视频 (原始 {total_search_vids} 条)"
        )

        # ═══════════════════════════════════════════════════════════
        # 阶段 1: QuickScore 无评论快速评分 + 激进淘汰
        # ═══════════════════════════════════════════════════════════
        await self._emit_progress(f"阶段 1: QuickScore 快速评分 ({len(all_search_videos)} 条搜索视频)...")

        # 1a: 对搜索视频 QuickScore
        qualified_videos, eliminated_videos = quick_scorer.score_all(
            all_search_videos,
            accounts={a["username"]: a for a in all_accounts},
        )

        await self._emit_progress(
            f"QuickScore 搜索视频: {len(qualified_videos)} 通过 / {len(eliminated_videos)} 淘汰 "
            f"({len(eliminated_videos) / max(1, len(all_search_videos)) * 100:.0f}% 淘汰率)"
        )

        # 1b: 提取账号详情
        account_map = {a["username"]: a for a in all_accounts}
        for i, acc in enumerate(all_accounts):
            username = acc.get("username", "")
            if not username:
                continue

            if (i + 1) % 5 == 0:
                await self._emit_progress(f"提取账号 ({i+1}/{len(all_accounts)}): @{username}")

            info = await self.extract_account_info(username)
            if info and not info.get("skipped"):
                acc.update(info)
            elif info and info.get("skipped"):
                logger.info("账号 %s 因限流跳过详情提取", username)

            await self._sleep_with_jitter()
            if self._cancelled:
                await self._emit_progress("⏹ 用户取消 (账号提取阶段)，返回已采集的部分数据...")
                break

        if self.checkpoint:
            self.checkpoint.mark_stage("accounts", "completed", f"完成 {len(all_accounts)} 个账号")

        # 1c: 提取账号视频 + QuickScore + 跨账号去重
        all_extracted_videos = []
        account_videos_passed = []

        for i, acc in enumerate(all_accounts):
            username = acc.get("username", "")
            if not username:
                continue

            if (i + 1) % 3 == 0:
                await self._emit_progress(
                    f"提取视频 ({i+1}/{len(all_accounts)}): @{username} "
                    f"(已通过 {len(qualified_videos) + len(account_videos_passed)} 条)"
                )

            videos = await self.extract_videos(username, videos_per_account, enrich_top=enrich_top)
            for v in videos:
                v["account_username"] = username
                vid = v.get("id", "")
                # 跨账号/跨搜索去重
                if vid and vid in seen_video_ids:
                    continue
                if vid:
                    seen_video_ids[vid] = v

                # QuickScore 每条视频
                result = quick_scorer.score(v, acc)
                v["quick_score"] = result.total
                v["product_relevance"] = result.product_relevance
                v["video_quality_score"] = result.video_quality
                v["industry_hits"] = result.industry_hits
                v["is_personal_account"] = result.is_personal_account
                v["_commercial_whitelist"] = result.commercial_whitelist_hit

                if result.passed:
                    v["eliminated_reason"] = ""
                    account_videos_passed.append(v)
                else:
                    v["eliminated_reason"] = result.eliminated_reason
                    all_extracted_videos.append(v)  # 淘汰视频仅做记录

            await self._sleep_with_jitter()
            if self._cancelled:
                await self._emit_progress("⏹ 用户取消 (视频提取阶段)，返回已采集的部分数据...")
                break

        # 合并搜索视频 + 账号视频
        all_qualified = qualified_videos + account_videos_passed  # noqa: F821 (qualified_videos defined above)

        await self._emit_progress(
            f"阶段 1 完成: {len(all_qualified)} 条视频通过 QuickScore "
            f"(搜索 {len(qualified_videos)} + 账号 {len(account_videos_passed)}), "
            f"{len(eliminated_videos) + len(all_extracted_videos)} 条淘汰"
        )

        if not all_qualified:
            await self._emit_progress("⚠ 无视频通过 QuickScore，返回空结果")
            return {
                "keywords": keywords, "region": region,
                "accounts": all_accounts, "videos": [], "comments": [],
                "low_quality_videos": eliminated_videos + all_extracted_videos,
                "total_accounts": len(all_accounts), "total_videos": 0, "total_comments": 0,
                "quick_score_stats": {"passed": 0, "eliminated": len(eliminated_videos) + len(all_extracted_videos)},
            }

        # ═══════════════════════════════════════════════════════════
        # 阶段 2: 轻量评论采样 + QuickIntentScanner
        # ═══════════════════════════════════════════════════════════
        await self._emit_progress(
            f"阶段 2: 轻量评论采样 ({len(all_qualified)} 条视频, "
            f"各 {sample_comment_count} 条, 策略={comment_sampling_strategy})..."
        )

        intent_signaled = []   # 有意图 → 进入阶段 3
        no_intent = []         # 无意图 → 仅保留采样评论
        all_sampled_comments = []
        _stage2_total_comments = 0
        _stage2_total_videos = 0

        for i, v in enumerate(all_qualified):
            vid = v.get("id", "")
            comment_count = v.get("comment_count", 0)
            if not vid:
                no_intent.append(v)
                continue

            if (i + 1) % 10 == 0:
                await self._emit_progress(
                    f"评论采样 ({i+1}/{len(all_qualified)}): "
                    f"已发现 {len(intent_signaled)} 条有意图信号"
                )

            try:
                sample = await self._sample_comments(
                    vid, count=sample_comment_count,
                    strategy=comment_sampling_strategy
                )
            except Exception as e:
                logger.warning("采样评论失败 vid=%s: %s", vid, e)
                sample = []

            # —— 诊断日志: 每个视频的采样结果 ——
            logger.info(
                "[阶段2诊断] vid=%s comment_count=%d sampled=%d has_intent=%s",
                vid, comment_count, len(sample),
                quick_scanner.has_intent(sample) if sample else False
            )

            v["_sampled_comments"] = sample
            _stage2_total_comments += len(sample)
            _stage2_total_videos += 1

            if quick_scanner.has_intent(sample):
                intent_signaled.append(v)
            else:
                no_intent.append(v)
                # 保留采样评论
                for c in sample:
                    c["account_username"] = v.get("account_username", "")
                all_sampled_comments.extend(sample)

            # 取消检查点
            if (i + 1) % 10 == 0 and self._cancelled:
                await self._emit_progress("⏹ 用户取消 (评论采样阶段)，返回已采集的部分数据...")
                break

        await self._emit_progress(
            f"阶段 2 完成: {len(intent_signaled)} 条有意图信号 → 进入深度评论, "
            f"{len(no_intent)} 条无意图 → 仅保留采样 "
            f"(淘汰率 {len(no_intent) / max(1, len(all_qualified)) * 100:.0f}%)"
        )
        # —— 阶段 2 汇总统计 ——
        _stage2_avg = _stage2_total_comments / max(1, _stage2_total_videos)
        logger.info(
            "[阶段2统计] 总视频=%d 总采样评论=%d 平均评论/视频=%.1f "
            "有意图=%d 无意图=%d",
            _stage2_total_videos, _stage2_total_comments, _stage2_avg,
            len(intent_signaled), len(no_intent)
        )
        if _stage2_total_videos > 0 and _stage2_total_comments == 0:
            logger.error(
                "[阶段2异常] %d 个视频全部 0 评论！请检查: "
                "1) _sample_comments URL 是否正确 "
                "2) TikTok 是否限制 fetch() 调用 "
                "3) 页面是否在正确的域名下 (tiktok.com)",
                _stage2_total_videos
            )

        # ═══════════════════════════════════════════════════════════
        # 阶段 3: 深度评论抓取 — 仅对意图信号视频
        # ═══════════════════════════════════════════════════════════
        await self._emit_progress(f"阶段 3: 深度评论抓取 ({len(intent_signaled)} 条视频, 各 {deep_comment_count} 条)...")

        all_deep_comments = []
        deep_analyzed_videos = []

        for i, v in enumerate(intent_signaled):
            vid = v.get("id", "")
            username = v.get("account_username", "")
            if not vid:
                continue

            if (i + 1) % 5 == 0:
                await self._emit_progress(
                    f"深度评论 ({i+1}/{len(intent_signaled)}): @{username}"
                )

            try:
                comments = await self.extract_comments(username, vid, deep_comment_count)
            except Exception as e:
                logger.warning("深度评论抓取失败 vid=%s: %s", vid, e)
                comments = []

            # 按点赞排序
            comments_sorted = sorted(
                comments, key=lambda c: c.get("likes", 0) or 0, reverse=True
            )
            for c in comments_sorted:
                c["account_username"] = username

            # 存储深度评论到视频上 (供 CommentClassifier 使用)
            v["_deep_comments"] = comments_sorted
            deep_analyzed_videos.append(v)
            all_deep_comments.extend(comments_sorted)

            await self._sleep_with_jitter()

            if (i + 1) % 5 == 0 and self._cancelled:
                await self._emit_progress("⏹ 用户取消 (深度评论阶段)，返回已采集的部分数据...")
                break

        await self._emit_progress(
            f"阶段 3 完成: {len(deep_analyzed_videos)} 条视频深度分析, "
            f"{len(all_deep_comments)} 条深度评论 "
            f"(占原始 {len(all_qualified)} 条的 {len(deep_analyzed_videos) / max(1, len(all_qualified)) * 100:.0f}%)"
        )

        # ═══════════════════════════════════════════════════════════
        # 合并结果
        # ═══════════════════════════════════════════════════════════

        # all_videos: QuickScore 通过的全部视频
        all_videos_out = list(all_qualified)

        # 验证模式: 保留采样评论供误杀检测
        if validation_mode:
            # 保留 _sampled_comments 在 no_intent 视频上
            for v in no_intent:
                v["_kept_sampled_comments"] = v.get("_sampled_comments", [])
            # all_videos_out 也保留
        else:
            for v in all_videos_out:
                v.pop("_sampled_comments", None)

        # all_comments: 采样评论 (无意图) + 深度评论 (有意图)
        all_comments_out = all_sampled_comments + all_deep_comments

        if self.checkpoint:
            self.checkpoint.mark_stage("videos", "completed",
                f"QuickScore 通过 {len(all_videos_out)} 条, 深度分析 {len(deep_analyzed_videos)} 条")
            self.checkpoint.mark_stage("comments", "completed",
                f"采样 {len(all_sampled_comments)} + 深度 {len(all_deep_comments)} = {len(all_comments_out)} 条评论")

        # 漏斗统计
        total_raw_search = len(all_search_videos)
        after_dedup = len(all_search_videos)  # 已去重
        total_account_videos = len(account_videos_passed) + len(all_extracted_videos)
        total_raw = total_raw_search + total_account_videos
        qs_passed = len(all_qualified)
        qs_eliminated = len(eliminated_videos) + len(all_extracted_videos)

        funnel_stats = {
            "stage_0_raw_search": total_raw_search,
            "stage_0_account_videos": total_account_videos,
            "stage_0_total_raw": total_raw,
            "stage_0_after_dedup": after_dedup + total_account_videos,
            "stage_1_quickscore_passed": qs_passed,
            "stage_1_quickscore_eliminated": qs_eliminated,
            "stage_2_intent_signaled": len(intent_signaled),
            "stage_2_no_intent": len(no_intent),
            "stage_3_deep_analyzed": len(deep_analyzed_videos),
            # 每层保留率
            "qs_retention_rate": round(qs_passed / max(1, total_raw) * 100, 1),
            "intent_retention_rate": round(len(intent_signaled) / max(1, qs_passed) * 100, 1),
            "deep_retention_rate": round(len(deep_analyzed_videos) / max(1, len(intent_signaled)) * 100, 1),
            # 累计转化率
            "cumulative_to_quickscore": round(qs_passed / max(1, total_raw) * 100, 1),
            "cumulative_to_intent": round(len(intent_signaled) / max(1, total_raw) * 100, 1),
            "cumulative_to_deep": round(len(deep_analyzed_videos) / max(1, total_raw) * 100, 1),
        }

        result = {
            "keywords": keywords,
            "region": region,
            "accounts": all_accounts,
            "videos": all_videos_out,
            "comments": all_comments_out,
            # 新管道特有字段
            "deep_analyzed_videos": deep_analyzed_videos,
            "intent_signaled_count": len(intent_signaled),
            "no_intent_count": len(no_intent),
            # 验证模式: 保留无意图视频 (含采样评论) 供误杀检测
            "no_intent_videos": no_intent if validation_mode else [],
            "quick_score_eliminated_videos": eliminated_videos + all_extracted_videos if validation_mode else [],
            # 漏斗统计 (始终包含)
            "funnel_stats": funnel_stats,
            "quick_score_stats": {
                "passed": qs_passed,
                "eliminated": qs_eliminated,
                "elimination_rate": round(
                    qs_eliminated / max(1, total_raw) * 100, 1
                ),
            },
            # 向后兼容
            "low_quality_videos": eliminated_videos + all_extracted_videos,
            "total_accounts": len(all_accounts),
            "total_videos": len(all_videos_out),
            "total_comments": len(all_comments_out),
        }

        await self._emit_progress(
            f"数据采集完成: {result['total_accounts']} 账号, "
            f"{result['total_videos']} 视频 (其中 {len(deep_analyzed_videos)} 深度分析), "
            f"{result['total_comments']} 评论",
            result
        )

        return result


def _parse_count(text: str) -> int:
    """解析 TikTok 计数文本 -> 整数（支持中英文单位）"""
    if not text or not isinstance(text, str):
        return 0
    text = text.strip().lower().replace(",", "").replace(" ", "")
    try:
        if text.endswith("万"):
            return int(float(text[:-1]) * 10_000)
        elif text.endswith("亿"):
            return int(float(text[:-1]) * 100_000_000)
        elif text.endswith("米"):
            # TikTok 用 "米" 表示 million
            return int(float(text[:-1]) * 1_000_000)
        elif text.endswith("k"):
            return int(float(text[:-1]) * 1000)
        elif text.endswith("m"):
            return int(float(text[:-1]) * 1_000_000)
        elif text.endswith("b"):
            return int(float(text[:-1]) * 1_000_000_000)
        else:
            return int(float(text))
    except (ValueError, TypeError):
        return 0
