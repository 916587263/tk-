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
        # 获取已存在的 context，创建新页面（避免与用户操作冲突）
        contexts = self._browser.contexts
        if contexts:
            self._context = contexts[0]
        else:
            self._context = await self._browser.new_context()
        # 始终创建新页面，避免与用户正在浏览的页面冲突
        self._page = await self._context.new_page()

        # CDP 模式下同样注入 stealth（防护层）
        await self._page.add_init_script(STEALTH_JS)
        await self._emit_progress("CDP 浏览器已连接（使用真实浏览器环境）")

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
            # CDP 模式：断开连接（用户浏览器保持运行）
            logger.info("CDP 模式：断开连接（用户浏览器保持运行）")
        elif self._browser and self._context:
            # 普通 browser 模式：context 由 browser 管理，关闭 browser 即可
            pass
        if self._browser:
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
        """根据关键词搜索 TikTok 账号"""
        await self._emit_progress(f"正在搜索: {keyword} (地区: {region or '不限'})")

        # 构建搜索 URL - 加入地区筛选
        search_query = keyword
        if region:
            search_query = f"{keyword} {region}"

        search_url = TIKTOK_SEARCH_URL.format(query=quote(search_query))
        await self._page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(1.5)

        # 检测限流
        if await self._is_rate_limited():
            if not await self._handle_rate_limit():
                return []
            # 重试：重新加载搜索页
            await self._page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(1.5)
        # 处理验证码
        if not await self.handle_captcha_if_needed():
            logger.error("搜索时验证码未解决")
            return []

        # 点击 "Users" / "用户" / "账号" 标签
        try:
            user_tab_selectors = [
                'span:has-text("用户")',
                'p:has-text("用户")',
                'p:has-text("账号")',
                'p:has-text("Users")',
                '[data-e2e="search-user-tab"]',
                'div[role="tab"]:has-text("Users")',
            ]
            for sel in user_tab_selectors:
                if await self._page.locator(sel).count() > 0:
                    await self._page.locator(sel).first.click()
                    await asyncio.sleep(2)
                    break
        except Exception as e:
            logger.warning("切换到用户标签失败: %s", e)

        # 滚动加载更多
        accounts = []
        scroll_attempts = 0
        max_scrolls = (max_results // 5) + 5

        while len(accounts) < max_results and scroll_attempts < max_scrolls:
            # 提取当前可见账号
            new_accounts = await self._extract_search_accounts()
            for acc in new_accounts:
                if acc not in accounts:
                    accounts.append(acc)

            if len(accounts) >= max_results:
                break

            # 滚动
            await self._page.evaluate("window.scrollBy(0, 800)")
            await self._sleep_with_jitter()
            scroll_attempts += 1

            if not await self.handle_captcha_if_needed():
                break

            if await self._is_rate_limited():
                if not await self._handle_rate_limit():
                    break

        logger.info("搜索到 %d 个账号", len(accounts))
        # 成功获取数据，重置退避
        self._backoff_level = 0

        await self._emit_progress(
            f"搜索完成: 找到 {len(accounts)} 个账号",
            {"accounts_found": len(accounts)}
        )
        return accounts[:max_results]

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

            # 备用：从 DOM 提取
            if not info:
                info = await self._extract_account_from_dom(username, url)

        except Exception as e:
            logger.error("提取账号 %s 信息失败: %s", username, e)
            info = {"username": username, "url": url, "error": str(e)}

        # 保存断点
        if info and self.checkpoint:
            self.checkpoint.mark_scraped("account_info", ck_key, info)

        logger.info("账号 @%s: 粉丝=%s, 点赞=%s", username, info.get("follower_count", "?"), info.get("like_count", "?"))
        return info

    async def _extract_account_from_dom(self, username: str, url: str) -> dict:
        """从 DOM 提取账号信息（新 TikTok UI，不使用 SIGI_STATE）"""
        import re
        info = {"username": username, "url": url}
        try:
            # 方法1: H1 通常包含昵称
            h1 = self._page.locator("h1").first
            if await h1.count() > 0:
                info["nickname"] = (await h1.inner_text()).strip()

            # 方法2: 提取 strong 标签中的统计数据
            # 新 TikTok 将统计数据放在 strong 标签中
            strong_els = self._page.locator("strong")
            strong_count = await strong_els.count()
            stat_values = []
            for j in range(min(strong_count, 10)):
                try:
                    val = (await strong_els.nth(j).inner_text()).strip()
                    if re.match(r"[\d.,]+[KMkmbB]?万?米?", val):
                        stat_values.append(val)
                except Exception:
                    pass
            # 前三项通常为: 关注, 粉丝, 赞
            labels = ["following_count", "follower_count", "like_count"]
            for j in range(min(len(stat_values), len(labels))):
                info[labels[j]] = _parse_count(stat_values[j])

            # 方法3: 提取简介 — 查找 h2 中包含较长文本的元素
            h2_els = self._page.locator("h2")
            h2_count = await h2_els.count()
            for j in range(h2_count):
                try:
                    text = (await h2_els.nth(j).inner_text()).strip()
                    # bio 通常是较长的文本，且不是通知之类的短标签
                    if len(text) > 20 and "通知" not in text and "关于" not in text:
                        if "bio" not in info or len(text) > len(info.get("bio", "")):
                            info["bio"] = text
                except Exception:
                    pass

        except Exception as e:
            logger.error("DOM 提取账号失败: %s", e)

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

        # 临时切换到新页面提取视频，避免旧页面状态影响
        old_page = self._page
        self._page = await self._context.new_page()
        try:
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
                # SIGI 数据已包含精确指标，直接返回
                await self._emit_progress(
                    f"视频提取完成: @{username} ({len(sigi_videos[:max_videos])} 条, SIGI 精确数据)"
                )
                videos = sigi_videos[:max_videos]
            else:
                # 滚动触发懒加载
                await self._page.evaluate("window.scrollBy(0, 800)")
                await asyncio.sleep(2)

                # 卡片计数
                card_count = await self._page.locator('[data-e2e=\"user-post-item\"]').count()
                logger.debug("视频卡片数: %d", card_count)

                videos = []
                scroll_count = 0
                max_scrolls = min((max_videos // 3) + 3, 10)
                seen_ids = set()

                while len(videos) < max_videos and scroll_count < max_scrolls:
                    new_videos = await self._extract_videos_from_page(username)
                    for v in new_videos:
                        vid = v.get("id", v.get("url", ""))
                        if vid and vid not in seen_ids:
                            seen_ids.add(vid)
                            videos.append(v)

                    if len(videos) >= max_videos:
                        break

                    await self._page.evaluate("window.scrollBy(0, 1000)")
                    await self._sleep_with_jitter()
                    scroll_count += 1

                    if not await self.handle_captcha_if_needed():
                        break

                videos = videos[:max_videos]

            # P2 增强: 按点赞数排序，仅对 Top N 进入详情页补充互动数据
            if enrich_top > 0 and videos:
                # 按 digg_count 降序排列，取前 N 条最有价值的视频进入详情页
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
                        # merge: 详情页数据覆盖列表页（更精确）
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
        finally:
            # 关闭视频页，恢复旧页面
            try:
                await self._page.close()
            except Exception:
                pass
            self._page = old_page

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

    async def _extract_videos_from_page(self, username: str) -> list[dict]:
        """从当前用户页提取视频列表（新 TikTok UI DOM 提取，增强版）"""
        import re
        videos = []
        try:
            # 方法1: data-e2e="user-post-item" 视频卡片
            video_items = self._page.locator('[data-e2e="user-post-item"]')
            count = await video_items.count()
            for i in range(min(count, 50)):
                try:
                    item = video_items.nth(i)
                    text = (await item.inner_text()) or ""
                    text = text.strip()

                    # 提取视频链接
                    link = item.locator('a[href*="/video/"]')
                    if await link.count() == 0:
                        continue
                    href = await link.first.get_attribute("href") or ""
                    vid = href.split("/video/")[-1].split("?")[0] if "/video/" in href else ""
                    url = f"https://www.tiktok.com{href}" if href.startswith("/") else href

                    # 提取描述（从图片 alt 属性）
                    img = item.locator("img[alt]")
                    desc = ""
                    if await img.count() > 0:
                        desc = (await img.first.get_attribute("alt")) or ""

                    # 提取标签（从描述中）
                    tags = re.findall(r"#(\w+)", desc) if desc else []

                    # 解析卡片文本中的数字来推断播放/互动数据
                    # TikTok 视频卡片文本格式: "<播放量>\n<点赞数>❤️\n<评论数>💬"
                    numbers = re.findall(r"([\d.,]+[KkMmBb]?万?米?)", text)
                    play_count = 0
                    digg_count = 0
                    comment_count = 0

                    # 第一个数字通常是播放量
                    if numbers:
                        play_count = _parse_count(numbers[0])

                    # 查找点赞/评论（通过 emoji 辅助识别）
                    digg_match = re.search(r"([\d.,]+[KkMmBb]?万?米?)\s*[❤👍💗]", text)
                    if digg_match:
                        digg_count = _parse_count(digg_match.group(1))

                    comment_match = re.search(r"([\d.,]+[KkMmBb]?万?米?)\s*[💬🗨]", text)
                    if comment_match:
                        comment_count = _parse_count(comment_match.group(1))

                    if vid:
                        videos.append({
                            "id": vid,
                            "desc": desc.strip(),
                            "create_time": 0,
                            "duration": 0,
                            "play_count": play_count,
                            "digg_count": digg_count,
                            "comment_count": comment_count,
                            "share_count": 0,
                            "url": url,
                            "tags": tags,
                            "music": "",
                        })
                except Exception:
                    continue

            # 方法2: 通用 DOM — 查找所有带 /video/ 的链接
            if not videos:
                link_els = self._page.locator('a[href*="/video/"]')
                link_count = await link_els.count()
                seen = set()
                for i in range(min(link_count, 50)):
                    try:
                        href = await link_els.nth(i).get_attribute("href") or ""
                        vid = href.split("/video/")[-1].split("?")[0] if "/video/" in href else ""
                        if vid and vid not in seen:
                            seen.add(vid)
                            url = f"https://www.tiktok.com{href}" if href.startswith("/") else href
                            videos.append({
                                "id": vid, "desc": "", "create_time": 0,
                                "duration": 0, "play_count": 0, "digg_count": 0,
                                "comment_count": 0, "share_count": 0,
                                "url": url, "tags": [], "music": "",
                            })
                    except Exception:
                        continue

        except Exception as e:
            logger.error("提取视频列表失败: %s", e)

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

            # 方法2: DOM 提取（fallback）
            if not detail:
                detail = await self._extract_video_detail_from_dom()

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

        # ── 策略3: DOM 提取（最后 fallback，非 CDP 模式）──
        logger.info("API 和 JS 均未获取到评论，尝试 DOM 提取...")
        comments = await self._extract_comments_from_dom(video_id, max_comments)

        if self.checkpoint:
            self.checkpoint.mark_scraped("comments", ck_key, {"comments": comments})

        logger.info("视频 %s: DOM 提取了 %d 条评论", video_id, len(comments))
        return comments[:max_comments]

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
    ) -> dict:
        """执行完整分析流程"""
        all_accounts = []
        all_videos = []
        all_comments = []

        await self._emit_progress(
            f"开始分析: {len(keywords)} 个关键词, 目标 {len(keywords) * accounts_per_keyword} 个账号"
        )

        # 阶段1: 搜索账号
        for kw in keywords:
            await self._emit_progress(f"搜索关键词: {kw}")
            accounts = await self.search_accounts(kw, region, accounts_per_keyword)
            all_accounts.extend(accounts)
            if self.checkpoint:
                self.checkpoint.mark_stage(f"search_{kw}", "completed", f"找到 {len(accounts)} 个账号")

        # 去重
        seen = set()
        unique_accounts = []
        for a in all_accounts:
            uname = a.get("username", "")
            if uname and uname not in seen:
                seen.add(uname)
                unique_accounts.append(a)
        all_accounts = unique_accounts

        await self._emit_progress(f"去重后共 {len(all_accounts)} 个账号")

        # 阶段2: 提取每个账号的详细信息
        for i, acc in enumerate(all_accounts):
            username = acc.get("username", "")
            if not username:
                continue

            await self._emit_progress(f"提取账号 ({i+1}/{len(all_accounts)}): @{username}")

            info = await self.extract_account_info(username)
            if info and not info.get("skipped"):
                acc.update(info)
            elif info and info.get("skipped"):
                logger.info("账号 %s 因限流跳过详情提取，保留搜索结果数据", username)

            await self._sleep_with_jitter()

        if self.checkpoint:
            self.checkpoint.mark_stage("accounts", "completed", f"完成 {len(all_accounts)} 个账号")

        # 阶段3: 提取每个账号的视频
        for i, acc in enumerate(all_accounts):
            username = acc.get("username", "")
            if not username:
                continue

            await self._emit_progress(f"提取视频 ({i+1}/{len(all_accounts)}): @{username}")

            videos = await self.extract_videos(username, videos_per_account, enrich_top=enrich_top)
            for v in videos:
                v["account_username"] = username
            all_videos.extend(videos)

            # 对前 N 条热门视频提取评论
            top_videos = sorted(videos, key=lambda x: x.get("digg_count", 0), reverse=True)[:comments_video_count]

            # ── 前置过滤层: 跳过低质量视频，减少无效 API 调用 ──
            qualified = []
            skipped = 0
            for v in top_videos:
                digg = v.get("digg_count", 0) or 0
                cc = v.get("comment_count", 0) or 0
                if digg < 10:
                    logger.debug("前置过滤: %s 点赞=%s < 10, 跳过评论抓取", v.get("id"), digg)
                    skipped += 1
                elif cc == 0:
                    logger.debug("前置过滤: %s 评论=0, 跳过评论抓取", v.get("id"))
                    skipped += 1
                else:
                    qualified.append(v)
            if skipped:
                logger.info("前置过滤: @%s 跳过 %d/%d 条低质量视频 (点赞<10 或 评论=0)",
                            username, skipped, len(top_videos))
            # ── 过滤结束 ──

            for v in qualified:
                vid = v.get("id", "")
                if not vid:
                    continue
                comments = await self.extract_comments(username, vid, comments_per_video)
                for c in comments:
                    c["account_username"] = username
                all_comments.extend(comments)

            await self._sleep_with_jitter()

        if self.checkpoint:
            self.checkpoint.mark_stage("videos", "completed", f"完成 {len(all_videos)} 条视频")
            self.checkpoint.mark_stage("comments", "completed", f"完成 {len(all_comments)} 条评论")

        result = {
            "keywords": keywords,
            "region": region,
            "accounts": all_accounts,
            "videos": all_videos,
            "comments": all_comments,
            "total_accounts": len(all_accounts),
            "total_videos": len(all_videos),
            "total_comments": len(all_comments),
        }

        await self._emit_progress(
            f"数据采集完成: {result['total_accounts']} 账号, {result['total_videos']} 视频, {result['total_comments']} 评论",
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
