"""
TikTok 竞争对手分析系统 - 账号过滤器
在账号详情提取完成后、视频提取前执行，过滤低质量/不相关账号。

P0 优先级：最高。过滤可以节省后续爬取时间。
"""
import re
from typing import Optional
from dataclasses import dataclass, field

from .logger import setup_logger

logger = setup_logger("account_filter")


@dataclass
class AccountFilterConfig:
    """账号过滤配置 — 所有字段可从 config.yaml 覆盖"""

    enabled: bool = True

    # ── 数值阈值（0 = 不过滤）──
    min_followers: int = 0          # 最低粉丝数
    max_followers: int = 0          # 最高粉丝数（排除超大号）
    min_likes: int = 0              # 最低点赞总数
    max_likes: int = 0              # 最高点赞总数
    min_videos: int = 0             # 最低视频数
    max_videos: int = 0             # 最高视频数
    min_engagement_rate: float = 0.0  # 最低互动率 (likes/followers)，如 0.01 = 1%

    # ── 布尔条件 ──
    require_verified: bool = False  # 只要认证账号
    require_bio: bool = False       # 必须有简介
    require_avatar: bool = False    # 必须有头像

    # ── 地区过滤 ──
    region_whitelist: list[str] = field(default_factory=list)   # 只保留这些region
    region_blacklist: list[str] = field(default_factory=list)   # 排除这些region
    language_whitelist: list[str] = field(default_factory=list)  # 只保留这些语言 (en/zh/ja/ko...)
    require_contact: bool = False        # 必须有联系方式（邮箱/WhatsApp/微信/网址）

    # ── 关键词过滤（大小写不敏感）──
    bio_keywords: list[str] = field(default_factory=list)       # 简介必须包含（OR）
    bio_keywords_all: list[str] = field(default_factory=list)   # 简介必须包含（AND）
    bio_blacklist: list[str] = field(default_factory=list)      # 简介包含任一则排除
    nickname_blacklist: list[str] = field(default_factory=list) # 昵称包含任一则排除
    username_blacklist: list[str] = field(default_factory=list) # 用户名包含任一则排除


class AccountFilter:
    """账号过滤器

    用法:
        cfg = AccountFilterConfig(min_followers=1000, require_verified=True)
        af = AccountFilter(cfg)
        kept, removed = af.filter(accounts)

    每个被过滤的账号会附加 _filter_reasons 列表。
    """

    def __init__(self, config: AccountFilterConfig):
        self.config = config
        self._stats = {"total": 0, "kept": 0, "removed": 0, "reasons": {}}

    @property
    def stats(self) -> dict:
        return self._stats

    def should_keep(self, account: dict) -> bool:
        """判断单个账号是否保留"""
        cfg = self.config

        # 限流跳过的账号直接保留（没有完整数据，无法判断）
        if account.get("skipped"):
            return True

        reasons = []

        # ── 数值阈值 ──
        followers = account.get("follower_count", 0) or 0
        likes = account.get("like_count", 0) or 0
        videos = account.get("video_count", 0) or 0

        if cfg.min_followers > 0 and followers < cfg.min_followers:
            reasons.append(f"粉丝数 {followers:,} < 最低 {cfg.min_followers:,}")
        if cfg.max_followers > 0 and followers > cfg.max_followers:
            reasons.append(f"粉丝数 {followers:,} > 上限 {cfg.max_followers:,}")
        if cfg.min_likes > 0 and likes < cfg.min_likes:
            reasons.append(f"点赞数 {likes:,} < 最低 {cfg.min_likes:,}")
        if cfg.max_likes > 0 and likes > cfg.max_likes:
            reasons.append(f"点赞数 {likes:,} > 上限 {cfg.max_likes:,}")
        if cfg.min_videos > 0 and videos < cfg.min_videos:
            reasons.append(f"视频数 {videos} < 最低 {cfg.min_videos}")
        if cfg.max_videos > 0 and videos > cfg.max_videos:
            reasons.append(f"视频数 {videos} > 上限 {cfg.max_videos}")

        # 互动率
        if cfg.min_engagement_rate > 0:
            er = likes / max(followers, 1)
            if er < cfg.min_engagement_rate:
                reasons.append(f"互动率 {er:.4f} < 最低 {cfg.min_engagement_rate}")

        # ── 布尔条件 ──
        if cfg.require_verified and not account.get("verified"):
            reasons.append("未认证")
        if cfg.require_bio and not (account.get("bio") or "").strip():
            reasons.append("无简介")
        if cfg.require_avatar and not (account.get("avatar") or "").strip():
            reasons.append("无头像")

        # ── 地区 ──
        region = (account.get("region") or "").upper()
        location = (account.get("location") or "").upper()
        # 组合 region + location 进行匹配
        region_signals = {region}
        if location:
            region_signals.add(location)

        if cfg.region_whitelist:
            wl = [r.upper() for r in cfg.region_whitelist]
            if not any(sig in wl for sig in region_signals if sig):
                reasons.append(f"地区/位置 {region_signals} 不在白名单 {wl}")
        if cfg.region_blacklist:
            bl = [r.upper() for r in cfg.region_blacklist]
            if any(sig in bl for sig in region_signals if sig):
                reasons.append(f"地区/位置 {region_signals} 在黑名单中")

        # ── 语言 ──
        if cfg.language_whitelist:
            lang = (account.get("language") or "").lower()
            wl = [l.lower() for l in cfg.language_whitelist]
            if lang and lang not in wl:
                reasons.append(f"语言 '{lang}' 不在白名单 {wl}")

        # ── 联系方式 ──
        if cfg.require_contact:
            bio = (account.get("bio") or "")
            has_contact = bool(
                re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', bio)  # email
                or re.search(r'(wa\.me|whatsapp|wa/)\s*\+?\d+', bio, re.I)  # WhatsApp
                or re.search(r'(wechat|微信|wx|vx)[\s:：]*\w+', bio, re.I)  # WeChat
                or re.search(r'https?://', bio)  # website
                or re.search(r'\+?\d{8,15}', bio)  # phone
            )
            if not has_contact:
                reasons.append("简介中无联系方式")

        # ── 关键词 ──
        bio = (account.get("bio") or "").lower()
        nickname = (account.get("nickname") or "").lower()
        username = (account.get("username") or "").lower()

        if cfg.bio_keywords:
            if not any(kw.lower() in bio for kw in cfg.bio_keywords):
                reasons.append(f"简介不含关键词: {cfg.bio_keywords}")
        if cfg.bio_keywords_all:
            if not all(kw.lower() in bio for kw in cfg.bio_keywords_all):
                missing = [kw for kw in cfg.bio_keywords_all if kw.lower() not in bio]
                reasons.append(f"简介缺少关键词: {missing}")
        if cfg.bio_blacklist:
            hits = [kw for kw in cfg.bio_blacklist if kw.lower() in bio]
            if hits:
                reasons.append(f"简介含黑名单词: {hits}")
        if cfg.nickname_blacklist:
            hits = [kw for kw in cfg.nickname_blacklist if kw.lower() in nickname]
            if hits:
                reasons.append(f"昵称含黑名单词: {hits}")
        if cfg.username_blacklist:
            hits = [kw for kw in cfg.username_blacklist if kw.lower() in username]
            if hits:
                reasons.append(f"用户名含黑名单词: {hits}")

        # 记录原因
        if reasons:
            account["_filter_reasons"] = reasons
            return False

        return True

    def filter(self, accounts: list[dict]) -> tuple[list[dict], list[dict]]:
        """过滤账号列表

        Returns:
            (kept[], removed[]) — removed 中的 account 附加了 _filter_reasons
        """
        self._stats = {"total": len(accounts), "kept": 0, "removed": 0, "reasons": {}}

        kept, removed = [], []
        for acc in accounts:
            if self.should_keep(acc):
                kept.append(acc)
                self._stats["kept"] += 1
            else:
                removed.append(acc)
                self._stats["removed"] += 1
                for reason in acc.get("_filter_reasons", []):
                    self._stats["reasons"][reason] = self._stats["reasons"].get(reason, 0) + 1

        logger.info(
            "账号过滤: %d → %d 保留, %d 被过滤 (条件: min_followers=%s, verified=%s, region_wl=%s)",
            self._stats["total"], self._stats["kept"], self._stats["removed"],
            self.config.min_followers, self.config.require_verified, self.config.region_whitelist
        )
        if self._stats["reasons"]:
            logger.info("过滤原因分布: %s", self._stats["reasons"])

        return kept, removed
