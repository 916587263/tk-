"""
TikTok 竞争对手分析系统 - 视频评分模块
对视频进行多维度评分 + 基础过滤，识别高价值内容和病毒传播潜力。

P3 优先级。
"""
import math
from typing import Optional
from dataclasses import dataclass, field

from .logger import setup_logger

logger = setup_logger("video_scorer")


# ═══════════════════════════════════════════════════════════
# 视频过滤
# ═══════════════════════════════════════════════════════════

@dataclass
class VideoFilterConfig:
    """视频过滤配置"""

    enabled: bool = False

    # ── 数值阈值 ──
    min_plays: int = 0              # 最低播放量
    min_digg: int = 0               # 最低点赞数
    min_comments: int = 0           # 最低评论数
    min_shares: int = 0             # 最低分享数
    min_engagement_rate: float = 0.0  # 最低互动率

    # ── 内容条件 ──
    require_description: bool = False   # 必须有描述
    require_tags: bool = False          # 必须有标签
    min_description_length: int = 0     # 描述最短长度
    min_tags: int = 0                   # 最少标签数

    # ── 关键词 ──
    desc_blacklist: list[str] = field(default_factory=list)     # 描述含这些词→过滤
    tag_whitelist: list[str] = field(default_factory=list)      # 标签必须包含（OR）

    # ── 时长过滤 ──
    min_duration: int = 0           # 最短时长（秒）
    max_duration: int = 0           # 最长时长（秒）


class VideoFilter:
    """视频过滤器（轻量，内嵌于 VideoScorer 模块）"""

    def __init__(self, config: VideoFilterConfig):
        self.config = config
        self._stats = {"total": 0, "kept": 0, "removed": 0}

    @property
    def stats(self) -> dict:
        return self._stats

    def should_keep(self, video: dict) -> bool:
        cfg = self.config

        plays = video.get("play_count", 0) or 0
        digg = video.get("digg_count", 0) or 0
        comments = video.get("comment_count", 0) or 0
        shares = video.get("share_count", 0) or 0
        desc = (video.get("desc") or "").strip()
        tags = video.get("tags") or []
        duration = video.get("duration", 0) or 0

        if cfg.min_plays > 0 and plays < cfg.min_plays:
            return False
        if cfg.min_digg > 0 and digg < cfg.min_digg:
            return False
        if cfg.min_comments > 0 and comments < cfg.min_comments:
            return False
        if cfg.min_shares > 0 and shares < cfg.min_shares:
            return False

        if cfg.min_engagement_rate > 0:
            total_eng = digg + comments + shares
            if plays > 0 and (total_eng / plays) < cfg.min_engagement_rate:
                return False

        if cfg.require_description and not desc:
            return False
        if cfg.min_description_length > 0 and len(desc) < cfg.min_description_length:
            return False
        if cfg.require_tags and not tags:
            return False
        if cfg.min_tags > 0 and len(tags) < cfg.min_tags:
            return False

        if cfg.desc_blacklist:
            desc_lower = desc.lower()
            if any(kw.lower() in desc_lower for kw in cfg.desc_blacklist):
                return False
        if cfg.tag_whitelist:
            tags_lower = [t.lower() for t in tags]
            if not any(kw.lower() in tags_lower for kw in cfg.tag_whitelist):
                return False

        if cfg.min_duration > 0 and duration < cfg.min_duration:
            return False
        if cfg.max_duration > 0 and duration > cfg.max_duration:
            return False

        return True

    def filter(self, videos: list[dict]) -> tuple[list[dict], list[dict]]:
        self._stats = {"total": len(videos), "kept": 0, "removed": 0}
        kept, removed = [], []
        for v in videos:
            if self.should_keep(v):
                kept.append(v)
                self._stats["kept"] += 1
            else:
                removed.append(v)
                self._stats["removed"] += 1
        logger.info("视频过滤: %d → %d 保留, %d 被过滤", self._stats["total"], self._stats["kept"], self._stats["removed"])
        return kept, removed


# ═══════════════════════════════════════════════════════════
# 视频评分
# ═══════════════════════════════════════════════════════════

@dataclass
class VideoScorerConfig:
    """视频评分配置"""

    enabled: bool = True

    # ── 权重（6维，内部自动归一化）──
    weight_plays: float = 0.20           # 播放量（热度体量）
    weight_engagement: float = 0.30      # 总互动率 = (digg+comment+share)/plays
    weight_digg: float = 0.20            # 点赞数（内容质量）
    weight_comment_rate: float = 0.15    # 评论率（话题性/争议性）
    weight_share_rate: float = 0.10      # 分享率（传播力）
    weight_quality: float = 0.05         # 内容质量（描述+标签+时长）

    # ── 对数缩放基准 ──
    play_benchmark: int = 100_000        # 此播放量 → 50分
    digg_benchmark: int = 10_000         # 此点赞 → 50分

    # ── 固定加分 ──
    has_tags_bonus: float = 5.0          # 有标签加分
    has_description_bonus: float = 5.0   # 有描述加分
    min_description_length: int = 20     # 有效描述最短长度
    optimal_duration_range: tuple = (15, 60)  # 最优时长区间（秒）


class VideoScorer:
    """视频评分器

    输出:
        score: 0-100 综合评分
        tier: S/A/B/C/D 等级
        virality: 病毒传播系数（越高越可能病毒传播）
        quality: 内容质量分（描述+标签+时长）

    用法:
        cfg = VideoScorerConfig()
        scorer = VideoScorer(cfg)
        scored = scorer.score_all(videos)
    """

    TIER_THRESHOLDS = [
        (80, "S", "爆款"),
        (60, "A", "热门"),
        (40, "B", "良好"),
        (20, "C", "一般"),
        (0,  "D", "低质"),
    ]

    def __init__(self, config: VideoScorerConfig):
        self.config = config
        self._weights = self._normalize_weights(config)

    @staticmethod
    def _normalize_weights(cfg: VideoScorerConfig) -> dict[str, float]:
        raw = {
            "plays": cfg.weight_plays,
            "engagement": cfg.weight_engagement,
            "digg": cfg.weight_digg,
            "comment_rate": cfg.weight_comment_rate,
            "share_rate": cfg.weight_share_rate,
            "quality": cfg.weight_quality,
        }
        total = sum(raw.values())
        if total <= 0:
            return {k: 1.0 / len(raw) for k in raw}
        return {k: v / total for k, v in raw.items()}

    # ───────────────────── 单项评分 ─────────────────────

    def _score_plays(self, plays: int) -> float:
        return self._log_score(plays, self.config.play_benchmark)

    def _score_engagement(self, digg: int, comments: int, shares: int, plays: int) -> float:
        """总互动率 → 0-100，10%互动率=100分"""
        if plays <= 0:
            return 0.0
        rate = (digg + comments + shares) / plays
        return min(100.0, rate * 1000.0)

    def _score_digg(self, digg: int) -> float:
        return self._log_score(digg, self.config.digg_benchmark)

    def _score_comment_rate(self, comments: int, plays: int) -> float:
        """评论率 → 0-100，2%=100分"""
        if plays <= 0:
            return 0.0
        rate = comments / plays
        return min(100.0, rate * 5000.0)

    def _score_share_rate(self, shares: int, plays: int) -> float:
        """分享率 → 0-100，2%=100分"""
        if plays <= 0:
            return 0.0
        rate = shares / plays
        return min(100.0, rate * 5000.0)

    def _score_quality(self, desc: str, tags: list[str], duration: int) -> float:
        """内容质量 → 0-100"""
        cfg = self.config
        score = 0.0

        # 描述
        if desc and len(desc.strip()) >= cfg.min_description_length:
            score += cfg.has_description_bonus
        score += min(20.0, len((desc or "").strip()) * 0.1)

        # 标签
        if tags:
            score += cfg.has_tags_bonus
        score += min(10.0, len(tags or []) * 2.0)

        # emoji
        emoji_count = sum(1 for c in (desc or "") if ord(c) > 0x1F000 or (0x2600 <= ord(c) <= 0x27BF))
        score += min(5.0, emoji_count * 1.0)

        # 时长（最优区间）
        opt_min, opt_max = cfg.optimal_duration_range
        if opt_min <= duration <= opt_max:
            score += 10.0
        elif duration > 0:
            # 离最优区间越远分越低
            center = (opt_min + opt_max) / 2
            distance = abs(duration - center) / center
            score += max(0, 10.0 * math.exp(-distance))

        return min(100.0, score)

    # ───────────────────── 综合评分 ─────────────────────

    def score(self, video: dict) -> dict:
        """对单个视频评分"""
        plays = video.get("play_count", 0) or 0
        digg = video.get("digg_count", 0) or 0
        comments = video.get("comment_count", 0) or 0
        shares = video.get("share_count", 0) or 0
        desc = video.get("desc", "") or ""
        tags = video.get("tags") or []
        duration = video.get("duration", 0) or 0

        bd = {}
        bd["plays_raw"] = plays
        bd["plays_score"] = self._score_plays(plays)

        bd["engagement_rate"] = round((digg + comments + shares) / max(plays, 1), 4)
        bd["engagement_score"] = self._score_engagement(digg, comments, shares, plays)

        bd["digg_raw"] = digg
        bd["digg_score"] = self._score_digg(digg)

        bd["comment_rate"] = round(comments / max(plays, 1), 4)
        bd["comment_score"] = self._score_comment_rate(comments, plays)

        bd["share_rate"] = round(shares / max(plays, 1), 4)
        bd["share_score"] = self._score_share_rate(shares, plays)

        bd["quality_score"] = self._score_quality(desc, tags, duration)

        # ── 加权 ──
        w = self._weights
        raw_total = (
            bd["plays_score"]      * w["plays"] +
            bd["engagement_score"] * w["engagement"] +
            bd["digg_score"]       * w["digg"] +
            bd["comment_score"]    * w["comment_rate"] +
            bd["share_score"]      * w["share_rate"] +
            bd["quality_score"]    * w["quality"]
        )
        final_score = round(self._sigmoid_compress(raw_total, center=50.0), 1)

        # 病毒系数: 分享率 * 评论率的几何平均 × 1000（便于阅读）
        share_r = shares / max(plays, 1)
        comment_r = comments / max(plays, 1)
        virality = round(math.sqrt(max(share_r * comment_r, 0)) * 1000, 2)

        # 等级
        tier, tier_label = "D", "低质"
        for threshold, t, label in self.TIER_THRESHOLDS:
            if final_score >= threshold:
                tier, tier_label = t, label
                break

        return {
            "score": final_score,
            "tier": tier,
            "tier_label": tier_label,
            "virality": virality,
            "breakdown": bd,
        }

    def score_all(self, videos: list[dict]) -> list[dict]:
        """批量评分，原地修改并返回按分数降序排列"""
        tiers = {}
        for v in videos:
            result = self.score(v)
            v["score"] = result["score"]
            v["tier"] = result["tier"]
            v["tier_label"] = result["tier_label"]
            v["virality"] = result["virality"]
            v["score_breakdown"] = result["breakdown"]

            tiers[result["tier"]] = tiers.get(result["tier"], 0) + 1

        sorted_videos = sorted(videos, key=lambda v: v.get("score", 0), reverse=True)
        logger.info("视频评分完成: %d 条, 等级分布=%s", len(sorted_videos), tiers)
        return sorted_videos

    # ───────────────────── 聚合统计 ─────────────────────

    def aggregate_by_account(self, videos: list[dict]) -> dict[str, dict]:
        """按账号聚合视频统计"""
        by_account = {}
        for v in videos:
            uname = v.get("account_username", "unknown")
            if uname not in by_account:
                by_account[uname] = {
                    "count": 0,
                    "total_plays": 0,
                    "total_digg": 0,
                    "total_comments": 0,
                    "total_shares": 0,
                    "avg_score": 0.0,
                    "max_score": 0.0,
                    "viral_count": 0,  # virality >= 5 的
                    "tiers": {},
                }
            agg = by_account[uname]
            agg["count"] += 1
            agg["total_plays"] += v.get("play_count", 0) or 0
            agg["total_digg"] += v.get("digg_count", 0) or 0
            agg["total_comments"] += v.get("comment_count", 0) or 0
            agg["total_shares"] += v.get("share_count", 0) or 0
            score = v.get("score", 0)
            agg["avg_score"] = (agg["avg_score"] * (agg["count"] - 1) + score) / agg["count"]
            agg["max_score"] = max(agg["max_score"], score)
            if v.get("virality", 0) >= 5:
                agg["viral_count"] += 1
            tier = v.get("tier", "?")
            agg["tiers"][tier] = agg["tiers"].get(tier, 0) + 1

        # 计算平均分排名
        for uname in by_account:
            agg = by_account[uname]
            agg["avg_score"] = round(agg["avg_score"], 1)
            agg["engagement_rate"] = round(
                (agg["total_digg"] + agg["total_comments"] + agg["total_shares"])
                / max(agg["total_plays"], 1), 4
            )

        return by_account

    # ───────────────────── 工具方法 ─────────────────────

    @staticmethod
    def _log_score(value: int, benchmark: int) -> float:
        if value <= 0:
            return 0.0
        if benchmark <= 0:
            return 50.0
        ratio = value / benchmark
        return min(100.0, 50.0 + 50.0 * math.log10(max(ratio, 0.01)))

    @staticmethod
    def _sigmoid_compress(x: float, center: float = 50.0) -> float:
        return 100.0 / (1.0 + math.exp(-(x - center) / 15.0))
