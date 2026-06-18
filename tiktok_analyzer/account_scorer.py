"""
TikTok 竞争对手分析系统 - 账号评分模块
对账号进行7维度加权评分，输出 0-100 综合竞争力分数 + S/A/B/C/D 等级。

P1 优先级：高。评分为后续过滤和排序提供量化依据。
"""
import math
from typing import Optional
from dataclasses import dataclass, field

from .logger import setup_logger

logger = setup_logger("account_scorer")


@dataclass
class AccountScorerConfig:
    """账号评分配置"""

    enabled: bool = True

    # ── 权重分配（7维，内部自动归一化）──
    weight_followers: float = 0.30       # 粉丝数（体量）
    weight_engagement: float = 0.25      # 互动率 = likes/followers
    weight_verified: float = 0.10        # 认证加分
    weight_bio_quality: float = 0.10     # 简介完整度
    weight_video_count: float = 0.10     # 视频产量
    weight_consistency: float = 0.10     # 内容一致性 (videos/followers 比)
    weight_region_match: float = 0.05    # 目标地区匹配

    # ── 对数缩放基准值 ──
    follower_benchmark: int = 100_000    # 此粉丝数 → 50分（对数中点）
    like_benchmark: int = 1_000_000      # 此点赞数 → 50分
    video_benchmark: int = 500           # 此视频数 → 50分

    # ── 固定加分 ──
    verified_bonus: float = 15.0         # 认证直接加15分（原始分）
    region_match_bonus: float = 10.0     # 目标地区匹配加10分

    # ── 目标市场 ──
    target_regions: list[str] = field(default_factory=list)  # 如 ["US", "GB"]


class AccountScorer:
    """账号竞争力评分器

    评分范围: 0-100
    等级划分:
        S: >= 80   (顶级)
        A: >= 60   (优秀)
        B: >= 40   (良好)
        C: >= 20   (一般)
        D: < 20    (低质)

    用法:
        cfg = AccountScorerConfig(target_regions=["US"])
        scorer = AccountScorer(cfg)
        scored = scorer.score_all(accounts)
    """

    # ── 等级阈值的平方（用于避免开方）──
    TIER_THRESHOLDS = [
        (80, "S", "顶级"),
        (60, "A", "优秀"),
        (40, "B", "良好"),
        (20, "C", "一般"),
        (0,  "D", "低质"),
    ]

    def __init__(self, config: AccountScorerConfig):
        self.config = config
        # 归一化权重（确保总和=1，用户不需要手动配平）
        self._weights = self._normalize_weights(config)

    @staticmethod
    def _normalize_weights(cfg: AccountScorerConfig) -> dict[str, float]:
        raw = {
            "followers": cfg.weight_followers,
            "engagement": cfg.weight_engagement,
            "verified": cfg.weight_verified,
            "bio_quality": cfg.weight_bio_quality,
            "video_count": cfg.weight_video_count,
            "consistency": cfg.weight_consistency,
            "region_match": cfg.weight_region_match,
        }
        total = sum(raw.values())
        if total <= 0:
            # 全部为0则均分
            return {k: 1.0 / len(raw) for k in raw}
        return {k: v / total for k, v in raw.items()}

    # ───────────────────── 单项评分 ─────────────────────

    def _score_followers(self, followers: int) -> float:
        """粉丝数 → 0-100（对数缩放）"""
        return self._log_score(followers, self.config.follower_benchmark)

    def _score_engagement(self, likes: int, followers: int) -> float:
        """互动率 → 0-100
        5% 互动率 ≈ 100分，1% ≈ 20分
        """
        if followers <= 0:
            return 0
        rate = likes / followers
        return min(100.0, rate * 2000.0)

    def _score_verified(self, verified: bool) -> float:
        """认证 → 0 或 bonus"""
        return self.config.verified_bonus if verified else 0.0

    def _score_bio_quality(self, bio: str) -> float:
        """简介质量 → 0-100
        - 空简介: 0
        - ~67字符: 100
        - 含 emoji 额外+5
        - 含链接额外+3
        """
        if not bio or not bio.strip():
            return 0.0
        bio = bio.strip()
        score = min(100.0, len(bio) * 1.5)

        # emoji 加分
        emoji_count = sum(1 for c in bio if ord(c) > 0x1F000 or (0x2600 <= ord(c) <= 0x27BF))
        score += min(5.0, emoji_count * 2.5)

        # 链接加分
        if "http" in bio or "www." in bio:
            score += 3.0

        # 换行/结构加分（好的 bio 通常有分段）
        if "\n" in bio:
            score += 3.0

        return min(100.0, score)

    def _score_video_count(self, count: int) -> float:
        """视频产量 → 0-100"""
        return self._log_score(count, self.config.video_benchmark)

    def _score_consistency(self, videos: int, followers: int) -> float:
        """内容一致性 → 0-100
        高视频/粉丝比 = 高运营投入度，但也要防止刷量号
        最优区间: 0.002 ~ 0.02 (每100粉丝 0.2~2 个视频)
        """
        if followers <= 0:
            return 0.0
        ratio = videos / followers
        # 用钟形曲线：ratio=0.005 时满分，偏离则降低
        optimal = 0.005
        deviation = abs(ratio - optimal) / optimal
        return max(0.0, 100.0 * math.exp(-deviation))

    def _score_region(self, region: str) -> float:
        """地区匹配 → 0 或 bonus"""
        target = [r.upper() for r in self.config.target_regions]
        if not target:
            return 0.0
        return self.config.region_match_bonus if (region or "").upper() in target else 0.0

    # ───────────────────── 综合评分 ─────────────────────

    def score(self, account: dict) -> dict:
        """对单个账号评分

        Args:
            account: 包含 follower_count/like_count/video_count/verified/bio/region 的 dict

        Returns:
            {"score": 85.2, "tier": "S", "tier_label": "顶级", "breakdown": {...}}
        """
        cfg = self.config
        bd = {}  # breakdown

        followers = account.get("follower_count", 0) or 0
        likes = account.get("like_count", 0) or 0
        videos = account.get("video_count", 0) or 0
        verified = account.get("verified", False) or False
        bio = account.get("bio", "") or ""
        region = account.get("region", "") or ""

        # 各维度评分
        bd["followers_raw"] = followers
        bd["followers_score"] = self._score_followers(followers)

        bd["engagement_rate"] = round(likes / max(followers, 1), 4)
        bd["engagement_score"] = self._score_engagement(likes, followers)

        bd["verified"] = verified
        bd["verified_score"] = self._score_verified(verified)

        bd["bio_length"] = len(bio.strip()) if bio else 0
        bd["bio_score"] = self._score_bio_quality(bio)

        bd["video_count_raw"] = videos
        bd["video_count_score"] = self._score_video_count(videos)

        bd["consistency_ratio"] = round(videos / max(followers, 1), 6)
        bd["consistency_score"] = self._score_consistency(videos, followers)

        bd["region"] = region
        bd["region_match"] = region.upper() in [r.upper() for r in cfg.target_regions] if cfg.target_regions else None
        bd["region_score"] = self._score_region(region)

        # ── 加权总分 ──
        w = self._weights
        raw_total = (
            bd["followers_score"]   * w["followers"] +
            bd["engagement_score"]  * w["engagement"] +
            bd["verified_score"]    * w["verified"] +
            bd["bio_score"]         * w["bio_quality"] +
            bd["video_count_score"] * w["video_count"] +
            bd["consistency_score"] * w["consistency"] +
            bd["region_score"]      * w["region_match"]
        )
        # sigmoid 压缩到 0-100（center=50 保证 50 原始分 → 50 最终分）
        final_score = round(self._sigmoid_compress(raw_total, center=50.0), 1)

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
            "breakdown": bd,
        }

    def score_all(self, accounts: list[dict]) -> list[dict]:
        """批量评分，原地修改 account dict 并返回按分数降序排列的列表

        每个 account 新增字段:
            score, tier, tier_label, score_breakdown
        """
        tiers_count = {}
        for acc in accounts:
            result = self.score(acc)
            acc["score"] = result["score"]
            acc["tier"] = result["tier"]
            acc["tier_label"] = result["tier_label"]
            acc["score_breakdown"] = result["breakdown"]

            tiers_count[result["tier"]] = tiers_count.get(result["tier"], 0) + 1

        sorted_accounts = sorted(accounts, key=lambda a: a.get("score", 0), reverse=True)

        logger.info(
            "账号评分完成: %d 个账号, 等级分布=%s, Top3=%s",
            len(sorted_accounts), tiers_count,
            [(a.get("username"), a.get("score")) for a in sorted_accounts[:3]]
        )
        return sorted_accounts

    # ───────────────────── 工具方法 ─────────────────────

    @staticmethod
    def _log_score(value: int, benchmark: int) -> float:
        """对数缩放：benchmark → 50分，10×benchmark → 100分"""
        if value <= 0:
            return 0.0
        if benchmark <= 0:
            return 50.0
        ratio = value / benchmark
        # log10: 1 → 0, 10 → 1，映射到 50-100
        return min(100.0, 50.0 + 50.0 * math.log10(max(ratio, 0.01)))

    @staticmethod
    def _sigmoid_compress(x: float, center: float = 50.0) -> float:
        """Sigmoid 压缩：平滑映射到 [0, 100]，center 处斜率最大"""
        # 使用 15 的陡峭度，使 0-100 原始分区间有效映射
        return 100.0 / (1.0 + math.exp(-(x - center) / 15.0))
