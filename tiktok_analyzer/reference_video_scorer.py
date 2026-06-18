"""
TikTok 外贸行业对标视频发现系统 - 对标参考视频评分器

位于管道 P5 (IntentDetector) 之后、P6 (AI Summary) 之前。
综合视频互动质量、评论质量、采购意向占比、账号行业相关度四维度，
为每条视频生成"对标参考评分"，帮助运营团队快速发现最值得模仿的行业视频。

评分逻辑:
  参考分 = sigmoid_compress(
    0.25 * 视频互动质量 +       // 复用 VideoScorer 已评分
    0.25 * 评论质量 +            // 评论率 + 深度
    0.35 * 采购意向评论占比 +    // 核心维度: 真实买家信号
    0.15 * 账号行业相关度        // bio/tags 行业关键词命中
  )

硬性准入门控: digg>=10 AND comment>=5 AND purchase_intent_comments>0
不达标视频直接跳过，不进入 reference_videos.csv。
"""
import math
from typing import Optional
from dataclasses import dataclass, field
from collections import defaultdict

from .logger import setup_logger

logger = setup_logger("reference_scorer")


# ═══════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════

@dataclass
class ReferenceVideoScorerConfig:
    """对标参考视频评分配置"""
    enabled: bool = True
    weight_video_engagement: float = 0.25
    weight_comment_quality: float = 0.25
    weight_intent_ratio: float = 0.35
    weight_account_relevance: float = 0.15
    reference_tier_thresholds: dict = field(default_factory=lambda: {
        "S": 80, "A": 60, "B": 40, "C": 20
    })
    top_reference_n: int = 20
    intent_ratio_benchmark: float = 0.10       # 采购意向占比基准 (10%=50分)
    comment_quality_benchmark_rate: float = 0.02  # 评论率基准 (2%=50分)
    comment_quality_min_length: int = 10       # 评论最小有效长度 (字符)
    industry_keywords: list = field(default_factory=lambda: [
        "factory", "manufacturer", "supplier", "wholesale", "export",
        "factory direct", "oem", "manufacturing", "production",
        "工厂", "厂家", "供应商", "批发", "外贸", "生产", "制造", "出口"
    ])


# ═══════════════════════════════════════════════════════════
# 对标参考视频评分器
# ═══════════════════════════════════════════════════════════

class ReferenceVideoScorer:
    """对标参考视频评分器

    综合四维加权评分，为每条视频计算"外贸行业对标参考价值"。
    IntentDetector 必须在 ReferenceVideoScorer 之前运行。

    用法:
        rvs_cfg = ReferenceVideoScorerConfig()
        scorer = ReferenceVideoScorer(rvs_cfg)
        ref_data = scorer.score_all(videos, comments, accounts)
        # videos 原地修改: +reference_score, +reference_tier, ...
        # ref_data 包含: {top_reference_videos, reference_benchmarks}
    """

    TIER_THRESHOLDS = [
        (80, "S", "强烈对标"),
        (60, "A", "高对标"),
        (40, "B", "中对标"),
        (20, "C", "低对标"),
        (0,  "D", "弱对标"),
    ]

    def __init__(self, config: ReferenceVideoScorerConfig = None):
        self.config = config or ReferenceVideoScorerConfig()
        self._normalize_weights()

    def _normalize_weights(self):
        """归一化四维权重至总和 1.0"""
        w = [
            self.config.weight_video_engagement,
            self.config.weight_comment_quality,
            self.config.weight_intent_ratio,
            self.config.weight_account_relevance,
        ]
        total = sum(w)
        if total > 0 and abs(total - 1.0) > 0.001:
            self.config.weight_video_engagement /= total
            self.config.weight_comment_quality /= total
            self.config.weight_intent_ratio /= total
            self.config.weight_account_relevance /= total

    # ── 主入口 ──

    def score_all(
        self, videos: list[dict], comments: list[dict], accounts: list[dict]
    ) -> dict:
        """对所有视频进行对标参考评分

        Args:
            videos: 视频列表 (已含 score/tier/virality 来自 VideoScorer)
            comments: 评论列表 (已含 intents/has_intent/top_intent 来自 IntentDetector)
            accounts: 账号列表 (已含 bio/region/video_stats)

        Returns:
            {
                "top_reference_videos": [...],  # top_reference_n 条, 按 reference_score 降序
                "reference_benchmarks": {...},   # 全量统计基准
            }
        """
        if not videos:
            logger.info("无视频数据，跳过对标参考评分")
            return {"top_reference_videos": [], "reference_benchmarks": {}}

        # 构建评论索引: video_id → [comments]
        comments_by_video: dict[str, list[dict]] = defaultdict(list)
        for c in comments:
            vid = c.get("video_id", "")
            if vid:
                comments_by_video[vid].append(c)

        # 构建账号索引: username → account
        accounts_by_username: dict[str, dict] = {}
        for a in accounts:
            uname = a.get("username", "")
            if uname:
                accounts_by_username[uname] = a

        scored_count = 0
        gated_count = 0
        all_scores = []

        for v in videos:
            vid = v.get("id", "")
            v_comments = comments_by_video.get(vid, [])

            # ── 硬性准入门控 ──
            if not self._passes_gate(v, v_comments):
                v["is_top_reference"] = False
                gated_count += 1
                continue

            # 查找关联账号
            uname = v.get("account_username", "")
            account = accounts_by_username.get(uname, {})

            # 四维评分
            s_video = self._score_video_engagement(v)
            s_comment = self._score_comment_quality(v, v_comments)
            s_intent = self._score_intent_ratio(v, v_comments)
            s_account = self._score_account_relevance(v, account)

            raw_total = (
                self.config.weight_video_engagement * s_video +
                self.config.weight_comment_quality * s_comment +
                self.config.weight_intent_ratio * s_intent +
                self.config.weight_account_relevance * s_account
            )

            reference_score = round(self._sigmoid_compress(raw_total), 1)
            tier, tier_label = self._assign_tier(reference_score)

            # 计算采购意向统计
            purchase_intent_comments = sum(
                1 for c in v_comments if c.get("has_intent")
            )
            purchase_intent_ratio = round(
                purchase_intent_comments / max(len(v_comments), 1), 4
            )

            # 注入字段
            v["purchase_intent_comments"] = purchase_intent_comments
            v["purchase_intent_ratio"] = purchase_intent_ratio
            v["reference_score"] = reference_score
            v["reference_tier"] = tier
            v["reference_tier_label"] = tier_label
            v["reference_breakdown"] = {
                "video_engagement": round(s_video, 1),
                "comment_quality": round(s_comment, 1),
                "intent_ratio": round(s_intent, 1),
                "account_relevance": round(s_account, 1),
                "raw_total": round(raw_total, 1),
            }

            all_scores.append(reference_score)
            scored_count += 1

        # 按 reference_score 降序排序
        videos.sort(key=lambda x: x.get("reference_score", 0) or 0, reverse=True)

        # 标记 Top N
        top_n = self.config.top_reference_n
        for i, v in enumerate(videos):
            if v.get("reference_score") and i < top_n:
                v["is_top_reference"] = True
                v["reference_rank"] = i + 1
            else:
                v["is_top_reference"] = False

        top_reference = [v for v in videos if v.get("is_top_reference")]

        # 基准统计
        benchmarks = self._compute_benchmarks(all_scores, scored_count, gated_count, len(videos))

        logger.info(
            "对标参考评分完成: %d 条评分, %d 条门控过滤, Top %d 条对标参考视频",
            scored_count, gated_count, len(top_reference)
        )

        return {
            "top_reference_videos": top_reference,
            "reference_benchmarks": benchmarks,
        }

    # ── 门控 ──

    @staticmethod
    def _passes_gate(video: dict, comments: list[dict]) -> bool:
        """硬性准入门控: digg>=10 AND comment>=5 AND 存在商业意图评论"""
        digg = video.get("digg_count", 0) or 0
        cc = video.get("comment_count", 0) or 0
        if digg < 10:
            return False
        if cc < 5:
            return False
        # 必须有至少一条含采购意图的评论
        has_intent = any(c.get("has_intent") for c in comments)
        if not has_intent:
            return False
        return True

    # ── 四维评分函数 ──

    @staticmethod
    def _score_video_engagement(video: dict) -> float:
        """25%: 视频互动质量 — 复用已有 VideoScorer 评分

        若已有 score_breakdown, 取 engagement_score 和 digg_score 的均值。
        否则回退到原始互动率计算。
        """
        breakdown = video.get("score_breakdown", {})
        if breakdown:
            engagement = breakdown.get("engagement_score", 0)
            digg = breakdown.get("digg_score", 0)
            return (engagement + digg) / 2

        # 回退: 手动计算互动率
        plays = max(video.get("play_count", 0) or 0, 1)
        digg = video.get("digg_count", 0) or 0
        comment = video.get("comment_count", 0) or 0
        share = video.get("share_count", 0) or 0
        engagement_rate = (digg + comment + share) / plays
        return min(100.0, engagement_rate * 1000)

    @staticmethod
    def _score_comment_quality(video: dict, comments: list[dict]) -> float:
        """25%: 评论质量 — 评论率 + 平均评论深度

        评论率分 (0-50): 评论数/播放量 vs benchmark
        深度分 (0-50): 平均评论长度 vs min_length
        """
        plays = max(video.get("play_count", 0) or 0, 1)
        total_comments = video.get("comment_count", 0) or len(comments) or 0

        # 评论率分 (0-50)
        comment_rate = total_comments / plays
        benchmark = 0.02  # 2%
        rate_score = min(50.0, (comment_rate / benchmark) * 50.0)

        # 评论深度分 (0-50): 平均长度
        if comments:
            avg_len = sum(len(c.get("text", "")) for c in comments) / len(comments)
            depth_score = min(50.0, (avg_len / 10) * 50.0)  # 10 字符 = 50 分
        else:
            depth_score = 0.0

        return rate_score + depth_score

    @staticmethod
    def _score_intent_ratio(video: dict, comments: list[dict]) -> float:
        """35%: 采购意向评论占比 — 核心维度

        intent_ratio = 含意图评论数 / 总评论数
        vs intent_ratio_benchmark (默认 0.10), 对数映射
        """
        total = len(comments) or max(video.get("comment_count", 0) or 0, 1)
        intent_count = sum(1 for c in comments if c.get("has_intent"))
        intent_ratio = intent_count / total

        # 对数映射: 基准值 → 50分, 10倍 → 100分
        benchmark = 0.10
        if intent_ratio <= 0:
            return 0.0
        score = 50.0 + 50.0 * math.log10(intent_ratio / benchmark)
        return max(0.0, min(100.0, score))

    @staticmethod
    def _score_account_relevance(video: dict, account: dict) -> float:
        """15%: 账号行业相关度 — bio/tags/desc 中外贸行业关键词命中

        检查 bio、video tags、video desc 中的行业关键词命中数
        """
        keywords = [
            "factory", "manufacturer", "supplier", "wholesale", "export",
            "factory direct", "oem", "manufacturing", "production",
            "工厂", "厂家", "供应商", "批发", "外贸", "生产", "制造", "出口"
        ]

        bio = (account.get("bio", "") or "").lower()
        tags = " ".join(video.get("tags", []) or []).lower()
        desc = (video.get("desc", "") or "").lower()
        combined = f"{bio} {tags} {desc}"

        hits = sum(1 for kw in keywords if kw.lower() in combined)
        # 每命中 1 个关键词 = 10 分, 最多 100 分
        return min(100.0, hits * 10.0)

    # ── 工具函数 ──

    @staticmethod
    def _sigmoid_compress(x: float) -> float:
        """Sigmoid 压缩: 将原始加权分映射到 0-100 (匹配 VideoScorer 公式)"""
        try:
            return 100.0 / (1.0 + math.exp(-(x - 50.0) / 15.0))
        except OverflowError:
            return 100.0 if x > 50.0 else 0.0

    @staticmethod
    def _assign_tier(score: float) -> tuple[str, str]:
        """分 → (tier, label)"""
        for threshold, tier, label in ReferenceVideoScorer.TIER_THRESHOLDS:
            if score >= threshold:
                return tier, label
        return "D", "弱对标"

    def _compute_benchmarks(
        self, all_scores: list[float], scored: int, gated: int, total: int
    ) -> dict:
        """计算全量参考基准"""
        if not all_scores:
            return {
                "total_scored": 0,
                "total_gated": gated,
                "total_videos": total,
                "avg_score": 0.0,
                "max_score": 0.0,
                "tier_distribution": {},
            }

        tiers = defaultdict(int)
        for s in all_scores:
            t, _ = self._assign_tier(s)
            tiers[t] += 1

        return {
            "total_scored": scored,
            "total_gated": gated,
            "total_videos": total,
            "avg_score": round(sum(all_scores) / len(all_scores), 1),
            "max_score": round(max(all_scores), 1),
            "tier_distribution": dict(tiers),
        }
