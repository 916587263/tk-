"""
TikTok 外贸行业对标视频发现系统 — 网络响应采集层

所有数据提取通过拦截浏览器自身 XHR 响应完成，不依赖 DOM 渲染。
策略: page.on("response") 被动拦截 TikTok SPA 自身发出的 API 请求,
解析 JSON 后返回结构化数据。零 DOM 依赖。

采集端点:
  - api/search/general/full  → 视频搜索 (含 stats, author, tags)
  - api/search/user/full     → 账号搜索 (含 follower, bio, sec_uid)
  - api/comment/list/        → 评论列表 (含 text, username, likes)
  - api/post/item_list/      → 用户视频列表
"""
import asyncio
import json
import time
from typing import Optional
from dataclasses import dataclass, field
from collections import defaultdict

from .logger import setup_logger

logger = setup_logger("network_collector")


# ═══════════════════════════════════════════════════════════
# 端点注册
# ═══════════════════════════════════════════════════════════

ENDPOINTS = {
    "search_videos":    "api/search/general/full",
    "search_accounts":  "api/search/user/full",
    "comments":         "api/comment/list/",
    "account_videos":   "api/post/item_list/",
}


# ═══════════════════════════════════════════════════════════
# 响应解析器 — 纯函数, 不依赖 self/page
# ═══════════════════════════════════════════════════════════

def parse_search_videos(data: dict) -> list[dict]:
    """解析 api/search/general/full 响应 → 视频列表"""
    items = data.get("data", [])
    videos = []
    for entry in items:
        if entry.get("type") != 1:
            continue  # type=1 是视频, 其他是广告/挑战等
        v = entry.get("item", {})
        if not v:
            continue
        stats = v.get("stats", {}) or {}
        author = v.get("author", {}) or {}
        text_extra = v.get("textExtra", []) or []
        music = v.get("music", {}) or {}
        video_info = v.get("video", {}) or {}

        videos.append({
            "id": v.get("id", ""),
            "desc": (v.get("desc") or "").strip(),
            "create_time": v.get("createTime", 0),
            "duration": video_info.get("duration", 0),
            "play_count": stats.get("playCount", 0),
            "digg_count": stats.get("diggCount", 0),
            "comment_count": stats.get("commentCount", 0),
            "share_count": stats.get("shareCount", 0),
            "url": f"https://www.tiktok.com/@{author.get('uniqueId', '')}/video/{v.get('id', '')}",
            "tags": [t.get("hashtagName", "").lstrip("#") for t in text_extra if t.get("hashtagName")],
            "music": music.get("title", ""),
            "author_unique_id": author.get("uniqueId", ""),
            "author_nickname": author.get("nickname", ""),
            "author_sec_uid": author.get("secUid", ""),
        })
    return videos


def parse_search_accounts(data: dict) -> list[dict]:
    """解析 api/search/user/full 响应 → 账号列表"""
    users = data.get("user_list", [])
    accounts = []
    for u in users:
        ui = u.get("user_info", {}) or {}
        # stats 有的在 user_info 内, 有的在外层 extra_info
        extra = u.get("extra_info", {}) or {}

        accounts.append({
            "username": ui.get("unique_id", ""),
            "nickname": ui.get("nickname", ""),
            "avatar": "",
            "verified": bool(ui.get("custom_verify", "")),
            "follower_count": ui.get("follower_count", 0),
            "following_count": extra.get("following_count", 0),
            "video_count": extra.get("video_count", 0),
            "like_count": ui.get("total_favorited", 0),
            "bio": ui.get("signature", ""),
            "sec_uid": ui.get("sec_uid", ""),
            "uid": ui.get("uid", ""),
            "region": "",
            "language": "",
            "location": "",
            "url": f"https://www.tiktok.com/@{ui.get('unique_id', '')}",
        })
    return accounts


def parse_comments(data: dict, video_id: str) -> list[dict]:
    """解析 api/comment/list/ 响应 → 评论列表"""
    comments = data.get("comments", [])
    results = []
    for c in comments:
        user = c.get("user", {}) or {}
        results.append({
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
    return results


def parse_account_videos(data: dict, username: str) -> list[dict]:
    """解析 api/post/item_list/ 响应 → 视频列表"""
    items = data.get("itemList", [])
    videos = []
    for v in items:
        stats = v.get("stats", {}) or {}
        author = v.get("author", {}) or {}
        music = v.get("music", {}) or {}
        text_extra = v.get("textExtra", []) or []
        video_info = v.get("video", {}) or {}

        videos.append({
            "id": v.get("id", ""),
            "desc": (v.get("desc") or "").strip(),
            "create_time": v.get("createTime", 0),
            "duration": video_info.get("duration", 0),
            "play_count": stats.get("playCount", 0),
            "digg_count": stats.get("diggCount", 0),
            "comment_count": stats.get("commentCount", 0),
            "share_count": stats.get("shareCount", 0),
            "url": f"https://www.tiktok.com/@{username}/video/{v.get('id', '')}",
            "tags": [t.get("hashtagName", "").lstrip("#") for t in text_extra if t.get("hashtagName")],
            "music": music.get("title", ""),
            "account_username": username,
        })
    return videos


# ═══════════════════════════════════════════════════════════
# Params builder
# ═══════════════════════════════════════════════════════════

def _get_webid(page) -> str:
    """从页面 cookie 提取 webId (用于 API 参数)"""
    # webId 通常出现在 URL 参数或 cookie 中; 从浏览器上下文获取
    return ""


# ═══════════════════════════════════════════════════════════
# NetworkCollector
# ═══════════════════════════════════════════════════════════

class NetworkCollector:
    """统一网络响应采集器

    所有数据提取通过 page.on("response") 被动拦截 TikTok SPA
    自身发出的 API 请求, 解析 JSON 后返回结构化数据。

    用法:
        collector = NetworkCollector(page)
        videos = await collector.collect_search_videos("PP non woven bag")
        accounts = await collector.collect_search_accounts("PP non woven bag")
        comments = await collector.collect_comments("username", "video_id")
    """

    def __init__(self, page):
        self._page = page
        self._responses: dict[str, list[dict]] = defaultdict(list)
        self._handler = None

    # ── 内部: 响应拦截 ──

    async def _start_capture(self, *url_patterns: str):
        """开始拦截匹配 url_patterns 的 XHR 响应"""
        self._responses.clear()
        patterns = list(url_patterns)

        async def _on_response(response):
            try:
                url = response.url
                if any(p in url for p in patterns) and response.ok:
                    body = await response.text()
                    data = json.loads(body)
                    for p in patterns:
                        if p in url:
                            self._responses[p].append(data)
                            break
            except Exception:
                pass  # 忽略解析失败

        self._page.on("response", _on_response)
        self._handler = _on_response

    async def _stop_capture(self, timeout: float = 8.0):
        """等待并停止拦截, 返回收集到的响应"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if any(self._responses.values()):
                break
            await asyncio.sleep(0.3)

        # 额外等待, 确保异步请求完成
        remaining = deadline - time.time()
        if remaining > 0:
            await asyncio.sleep(min(remaining, 2.0))

        if self._handler:
            try:
                self._page.remove_listener("response", self._handler)
            except Exception:
                pass
            self._handler = None

    # ── 公开方法 ──

    async def collect_search_videos(
        self, keyword: str, max_results: int = 30
    ) -> list[dict]:
        """搜索视频 — 拦截 api/search/general/full"""
        from urllib.parse import quote
        url = f"https://www.tiktok.com/search?q={quote(keyword)}"

        await self._start_capture("api/search/general/full")
        await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await self._stop_capture(timeout=6.0)

        all_videos = []
        seen_ids = set()
        for data in self._responses.get("api/search/general/full", []):
            for v in parse_search_videos(data):
                vid = v.get("id", "")
                if vid and vid not in seen_ids:
                    seen_ids.add(vid)
                    all_videos.append(v)
            if len(all_videos) >= max_results:
                break

        logger.info("搜索视频: 关键词=%s, 返回 %d 条", keyword, len(all_videos))
        return all_videos[:max_results]

    async def collect_search_accounts(
        self, keyword: str, max_results: int = 20
    ) -> list[dict]:
        """搜索账号 — 拦截 api/search/user/full

        需要在 collect_search_videos() 之后调用 (复用已有页面),
        点击 User 标签触发 user/full 请求。
        """
        # 点击 User 标签触发账号搜索 API
        for sel in [
            'span:has-text("用户")', 'p:has-text("用户")',
            'p:has-text("Users")', '[data-e2e="search-user-tab"]',
        ]:
            try:
                if await self._page.locator(sel).count() > 0:
                    # 清空先前响应, 开始新捕获
                    self._responses.clear()
                    await self._start_capture("api/search/user/full")
                    await self._page.locator(sel).first.click()
                    await self._stop_capture(timeout=6.0)
                    break
            except Exception:
                continue

        all_accounts = []
        seen = set()
        for data in self._responses.get("api/search/user/full", []):
            for a in parse_search_accounts(data):
                uname = a.get("username", "")
                if uname and uname not in seen:
                    seen.add(uname)
                    all_accounts.append(a)
            if len(all_accounts) >= max_results:
                break

        # 如果 user/full 没捕获到, 尝试从 general/full 中提取 author 信息
        # (已在之前的 collect_search_videos 调用中捕获)
        if not all_accounts:
            logger.info("user/full 未返回数据, 从 general/full 提取作者信息")

        logger.info("搜索账号: 关键词=%s, 返回 %d 个", keyword, len(all_accounts))
        return all_accounts[:max_results]

    async def collect_comments(
        self, username: str, video_id: str, max_comments: int = 200
    ) -> list[dict]:
        """提取视频评论 — 拦截 api/comment/list/

        导航到视频页面, TikTok 页面自身加载评论时触发 comment/list API。
        收集所有响应中的评论, 去重后返回。
        """
        url = f"https://www.tiktok.com/@{username}/video/{video_id}"

        self._responses.clear()
        await self._start_capture("api/comment/list/")
        await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await self._stop_capture(timeout=8.0)

        all_comments = []
        seen_cids = set()
        for data in self._responses.get("api/comment/list/", []):
            for c in parse_comments(data, video_id):
                # 用 text+username 去重 (cid 可能不存在)
                key = f"{c['username']}:{c['text'][:50]}"
                if key not in seen_cids:
                    seen_cids.add(key)
                    all_comments.append(c)
            if len(all_comments) >= max_comments:
                break

        logger.info(
            "评论: 视频 %s 捕获 %d 个响应, 提取 %d 条评论",
            video_id,
            len(self._responses.get("api/comment/list/", [])),
            len(all_comments)
        )
        return all_comments[:max_comments]

    async def collect_account_videos(
        self, username: str, max_videos: int = 30
    ) -> list[dict]:
        """提取账号视频列表 — 拦截 api/post/item_list/

        导航到用户主页, TikTok 页面自身加载视频列表时触发 post/item_list API。
        """
        uname = username.strip().lstrip("@")
        url = f"https://www.tiktok.com/@{uname}"

        self._responses.clear()
        await self._start_capture("api/post/item_list/")
        await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await self._stop_capture(timeout=8.0)

        all_videos = []
        seen_ids = set()
        for data in self._responses.get("api/post/item_list/", []):
            for v in parse_account_videos(data, uname):
                vid = v.get("id", "")
                if vid and vid not in seen_ids:
                    seen_ids.add(vid)
                    all_videos.append(v)
            if len(all_videos) >= max_videos:
                break

        logger.info("账号视频: @%s 返回 %d 条", uname, len(all_videos))
        return all_videos[:max_videos]

    async def collect_account_detail(
        self, username: str
    ) -> Optional[dict]:
        """提取单个账号详细信息 — 从 user 主页 SIGI_STATE 或 __UNIVERSAL_DATA"""
        uname = username.strip().lstrip("@")
        url = f"https://www.tiktok.com/@{uname}"

        await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)

        # 尝试从 SIGI_STATE 提取
        try:
            sigi = await self._page.evaluate("() => window.SIGI_STATE || null")
            if sigi:
                if isinstance(sigi, str):
                    sigi = json.loads(sigi)
                # UserModule 结构
                users = sigi.get("UserModule", {}).get("users", {})
                user_data = users.get(uname, users.get(username, None))
                if not user_data:
                    # UserPage 结构
                    user_data = sigi.get("UserPage", {}).get("userInfo", None)
                if user_data:
                    stats = user_data.get("stats", user_data)
                    return {
                        "username": user_data.get("uniqueId", uname),
                        "nickname": user_data.get("nickname", ""),
                        "avatar": user_data.get("avatarMedium", ""),
                        "verified": user_data.get("verified", False),
                        "follower_count": stats.get("followerCount", 0),
                        "following_count": stats.get("followingCount", 0),
                        "video_count": stats.get("videoCount", 0),
                        "like_count": stats.get("heartCount", stats.get("heart", 0)),
                        "bio": user_data.get("signature", ""),
                        "region": user_data.get("region", ""),
                        "language": user_data.get("language", ""),
                        "sec_uid": user_data.get("secUid", ""),
                        "uid": user_data.get("id", ""),
                        "url": url,
                    }
        except Exception as e:
            logger.debug("SIGI_STATE 提取账号详情失败: %s", e)

        return None
