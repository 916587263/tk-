"""
TikTok 竞争对手分析系统 - 验证码检测模块
"""
import re
import asyncio
from .logger import setup_logger

logger = setup_logger("captcha")

# 常见验证码关键词
CAPTCHA_KEYWORDS = [
    "captcha", "verify", "verification", "human",
    "robot", "security check", "are you", "prove",
    "slide", "slider", "puzzle", "旋转", "拖动",
    "验证", "验证码", "人机", "滑块", "拼图",
    "请确认", "安全验证", "点按",
    "unusual traffic",
    "login required", "sign in to",
]

async def detect_captcha(page) -> bool:
    """检测页面是否出现验证码（仅检查可见文本，排除 JS 代码）"""
    try:
        # 检查标题
        title = (await page.title()).lower()
        for kw in ["captcha", "verify", "验证码", "人机验证"]:
            if kw in title:
                logger.warning("检测到验证码（标题: %s）", title)
                return True

        # 检查可见文本（不含 script/style 内容）
        try:
            visible_text = (await page.inner_text("body")).lower()
        except Exception:
            visible_text = (await page.content()).lower()

        # 仅在高置信度关键词出现时触发
        high_confidence = [
            "please verify you are human",
            "slide to verify",
            "drag the slider",
            "请完成安全验证",
            "滑动验证",
            "按住滑块拖动",
        ]
        for kw in high_confidence:
            if kw in visible_text:
                logger.warning("检测到验证码（文本: %s）", kw)
                return True

        # 检查常见验证码 DOM 元素
        captcha_selectors = [
            "#captcha-verify-image",
            "#captcha_container",
            ".captcha_verify_container",
            ".captcha_verify_img",
            "#verify-bar",
            ".verify-captcha",
            "#sec_verify",
            ".tiktok-captcha",
        ]
        for selector in captcha_selectors:
            try:
                if await page.locator(selector).count() > 0:
                    logger.warning("检测到验证码元素: %s", selector)
                    return True
            except Exception:
                pass

        return False

    except Exception as e:
        logger.error("验证码检测异常: %s", e)
        return False

async def wait_for_human_intervention(page, timeout_hours: float = 2.0):
    """暂停等待人工处理验证码/登录（async 版本）"""
    logger.warning("=" * 60)
    logger.warning("⏳ 需要人工介入！请在浏览器中完成验证码/登录。")
    logger.warning("   完成后脚本将在检测不到验证码时自动继续。")
    logger.warning("   最长等待 %.0f 小时", timeout_hours)
    logger.warning("=" * 60)

    loop = asyncio.get_running_loop()
    start = loop.time()
    check_interval = 3  # 每 3 秒检查一次

    while loop.time() - start < timeout_hours * 3600:
        await asyncio.sleep(check_interval)
        elapsed = loop.time() - start

        try:
            if not await detect_captcha(page):
                logger.info("✅ 验证码已解除，继续执行（等待了 %.0f 秒）", elapsed)
                return True
        except Exception:
            pass  # 页面可能被关闭或导航

        if elapsed > 60 and int(elapsed) % 30 < check_interval:
            logger.info("  仍在等待人工处理... (已等待 %.0f 秒)", elapsed)

    logger.error("⏰ 等待超时（%.0f 小时），任务中止", timeout_hours)
    return False