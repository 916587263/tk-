"""
TikTok 外贸行业对标视频发现系统 — 统一评分模块

包含两个评分器:
  1. QuickScorer (阶段 1): 无评论快速评分 — 尽早淘汰低价值视频
  2. FinalScorer (阶段 4): 终极统一评分 — 引入商业意图, 仅对 Top 候选评分

QuickScore: 仅基于视频元数据, 零评论依赖, 淘汰 ~70-80% 视频
FinalScore: 三权重 (30/30/40), 首次引入商业意图维度
"""
import math
import re
from typing import Optional
from dataclasses import dataclass, field

from .logger import setup_logger

logger = setup_logger("unified_scorer")


# ═══════════════════════════════════════════════════════════
# QuickScorer 配置
# ═══════════════════════════════════════════════════════════

@dataclass
class QuickScorerConfig:
    """快速评分配置 (阶段 1)"""

    enabled: bool = True

    # ── 权重 (内部归一化) ──
    weight_relevance: float = 0.50   # 产品相关度权重
    weight_quality: float = 0.50     # 视频质量权重

    # ── 淘汰阈值 ──
    min_score: float = 20.0          # 低于此分直接淘汰
    min_likes_no_comment: int = 5    # 无评论时最低点赞数

    # ── 视频质量基准 ──
    play_benchmark: int = 100000     # 播放量基准 (50分参考线)
    engagement_benchmark: float = 0.05  # 互动率基准 (5%=50分)

    # ── 账号降分 ──
    personal_account_penalty: float = 30.0  # 个人/娱乐号降分

    # ── 行业关键词 (用于产品相关度计算) ──
    industry_keywords: list = field(default_factory=lambda: [
        "factory", "manufacturer", "supplier", "wholesale", "export",
        "factory direct", "oem", "manufacturing", "production",
        "工厂", "厂家", "供应商", "批发", "外贸", "生产", "制造", "出口",
    ])

    # ── 个人/娱乐账号检测词 ──
    personal_account_patterns: list = field(default_factory=lambda: [
        "fan account", "fanpage", "fan page", "fandom", "stan",
        "updates", "daily", "vlog", "entertainment",
        "粉丝", "后援", "日常", "娱乐",
    ])

    # ── 商业白名单关键词 (跳过硬淘汰) ──
    # 当 desc/title/bio/nickname 命中 ≥min_hits 个不同关键词时,
    # 跳过硬淘汰逻辑, 让视频进入正常评分流程
    commercial_whitelist: list = field(default_factory=lambda: [
        "manufacturer", "factory", "supplier", "oem", "odm",
        "moq", "wholesale", "distributor", "importer", "exporter",
    ])
    commercial_whitelist_min_hits: int = 2  # 最少命中不同关键词数


# ═══════════════════════════════════════════════════════════
# FinalScorer 配置
# ═══════════════════════════════════════════════════════════

@dataclass
class FinalScorerConfig:
    """终极评分配置 (阶段 4)"""

    enabled: bool = True

    # ── 三权重 (30/30/40) ──
    weight_product: float = 0.30
    weight_quality: float = 0.30
    weight_intent: float = 0.40

    # ── 商业意图子权重 ──
    intent_sub_ratio: float = 0.4      # intent_ratio 子权重
    intent_sub_quality: float = 0.4    # intent_quality 子权重
    intent_sub_diversity: float = 0.2  # intent_diversity 子权重

    # ── 分级阈值 ──
    tier_thresholds: dict = field(default_factory=lambda: {
        "S": 80, "A": 60, "B": 40, "C": 20
    })

    # ── Top N 输出 ──
    top_reference_n: int = 20

    # ── Sigmoid 参数 ──
    sigmoid_center: float = 50.0
    sigmoid_steepness: float = 15.0


# ═══════════════════════════════════════════════════════════
# QuickScore 结果
# ═══════════════════════════════════════════════════════════

@dataclass
class QuickScoreResult:
    """快速评分结果"""
    product_relevance: float = 0.0
    video_quality: float = 0.0
    total: float = 0.0
    passed: bool = False
    eliminated_reason: str = ""
    is_personal_account: bool = False
    industry_hits: list = field(default_factory=list)
    commercial_whitelist_hit: bool = False
    commercial_whitelist_keywords: list = field(default_factory=list)
    breakdown: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════
# FinalScore 结果
# ═══════════════════════════════════════════════════════════

@dataclass
class FinalScoreResult:
    """终极评分结果"""
    product_relevance: float = 0.0     # 复用阶段 1
    video_quality: float = 0.0         # 复用阶段 1
    commercial_intent: float = 0.0     # 来自阶段 3
    final_score: float = 0.0           # 加权总分 (0-100, sigmoid)
    tier: str = "D"
    tier_label: str = "弱对标"
    is_top_reference: bool = False
    reference_rank: int = 0
    breakdown: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════
# QuickScorer (阶段 1)
# ═══════════════════════════════════════════════════════════

class QuickScorer:
    """无评论快速评分器

    仅基于视频元数据 (播放量/互动数据/描述/标签) 和账号信息 (bio),
    计算产品相关度和视频质量两个维度的分数。
    不接触评论数据, 不调用 LLM。
    """

    def __init__(self, config: QuickScorerConfig = None):
        self.config = config or QuickScorerConfig()
        w_sum = self.config.weight_relevance + self.config.weight_quality
        if w_sum <= 0:
            w_sum = 1.0
        self._w_rel = self.config.weight_relevance / w_sum
        self._w_qual = self.config.weight_quality / w_sum

        self._industry_lower = [kw.lower() for kw in self.config.industry_keywords]

        self._personal_patterns = [
            re.compile(re.escape(p.lower()), re.IGNORECASE)
            for p in self.config.personal_account_patterns
        ]

        # 预编译商业白名单关键词 (小写)
        self._commercial_whitelist_lower = [
            kw.lower() for kw in self.config.commercial_whitelist
        ]

    def score(self, video: dict, account: dict = None) -> QuickScoreResult:
        """对单条视频进行快速评分"""
        account = account or {}

        # ── 商业白名单检查 (在硬淘汰之前) ──
        whitelist_hit = self._check_commercial_whitelist(video, account)

        eliminated = self._check_hard_elimination(video, account)
        if eliminated:
            # 如果硬淘汰被触发但白名单也命中, 理论上不会到这里
            # (因为 _check_hard_elimination 内部已检查白名单),
            # 但为安全起见仍记录白名单信息
            eliminated.commercial_whitelist_hit = whitelist_hit
            return eliminated

        relevance, industry_hits = self._score_product_relevance(video, account)
        quality = self._score_video_quality(video)

        is_personal = self._is_personal_account(account)
        if is_personal:
            quality = max(0, quality - self.config.personal_account_penalty)
            relevance = max(0, relevance * 0.5)

        total = self._w_rel * relevance + self._w_qual * quality
        passed = total >= self.config.min_score
        eliminated_reason = "" if passed else f"quick_score {total:.1f} < {self.config.min_score}"

        return QuickScoreResult(
            product_relevance=round(relevance, 1),
            video_quality=round(quality, 1),
            total=round(total, 1),
            passed=passed,
            eliminated_reason=eliminated_reason,
            is_personal_account=is_personal,
            industry_hits=industry_hits,
            commercial_whitelist_hit=whitelist_hit,
            commercial_whitelist_keywords=list(self._commercial_whitelist_lower),
            breakdown={
                "relevance": round(relevance, 1),
                "quality": round(quality, 1),
                "weight_relevance": round(self._w_rel, 2),
                "weight_quality": round(self._w_qual, 2),
                "personal_penalty": is_personal,
            },
        )

    def score_all(self, videos: list[dict], accounts: dict = None) -> tuple[list[dict], list[dict]]:
        """批量评分, 返回 (通过列表, 淘汰列表)"""
        accounts = accounts or {}
        passed, eliminated = [], []
        for v in videos:
            uname = v.get("account_username", "")
            acc = accounts.get(uname, {})
            result = self.score(v, acc)
            v["quick_score"] = result.total
            v["product_relevance"] = result.product_relevance
            v["video_quality_score"] = result.video_quality
            v["industry_hits"] = result.industry_hits
            v["is_personal_account"] = result.is_personal_account
            v["_commercial_whitelist"] = result.commercial_whitelist_hit
            if result.passed:
                v["eliminated_reason"] = ""
                passed.append(v)
            else:
                v["eliminated_reason"] = result.eliminated_reason
                eliminated.append(v)
        return passed, eliminated

    def _check_hard_elimination(self, video: dict, account: dict) -> Optional[QuickScoreResult]:
        """检查硬淘汰条件 — 商业白名单可跳过硬淘汰"""
        likes = video.get("digg_count", 0) or 0
        comments = video.get("comment_count", 0) or 0
        plays = video.get("play_count", 0) or 0

        # ── 商业白名单通道: 命中 ≥N 个不同商业词 → 跳过硬淘汰 ──
        if self._check_commercial_whitelist(video, account):
            return None

        if plays == 0 and likes == 0 and comments == 0:
            return QuickScoreResult(total=0, passed=False, eliminated_reason="zero_engagement")

        if likes < self.config.min_likes_no_comment and comments == 0:
            return QuickScoreResult(
                total=0, passed=False,
                eliminated_reason=f"low_engagement: likes({likes}) < {self.config.min_likes_no_comment} AND comments=0"
            )
        return None

    def _check_commercial_whitelist(self, video: dict, account: dict) -> bool:
        """检测视频是否命中商业白名单 (desc/title/bio/nickname 含 ≥N 个不同商业词)

        商业白名单关键词: manufacturer, factory, supplier, OEM, ODM,
        MOQ, wholesale, distributor, importer, exporter

        命中 ≥ commercial_whitelist_min_hits (默认 2) 个不同关键词 → True
        """
        desc = (video.get("desc") or "").lower()
        tags = " ".join(video.get("tags", []) or []).lower()
        bio = (account.get("bio") or "").lower()
        nickname = (account.get("nickname") or "").lower()

        combined = f"{desc} {tags} {bio} {nickname}"

        hit_keywords = set()
        for kw in self._commercial_whitelist_lower:
            if kw in combined:
                hit_keywords.add(kw)

        return len(hit_keywords) >= self.config.commercial_whitelist_min_hits

    def _score_product_relevance(self, video: dict, account: dict) -> tuple[float, list[str]]:
        hits = []
        score = 0.0

        desc = (video.get("desc") or "").lower()
        tags = " ".join(video.get("tags", []) or []).lower()
        bio = (account.get("bio") or "").lower()
        nickname = (account.get("nickname") or "").lower()

        for kw in self._industry_lower:
            hit_sources = []
            if kw in desc:
                hit_sources.append("desc")
            if kw in tags:
                hit_sources.append("tags")
            if kw in bio:
                hit_sources.append("bio")
            if kw in nickname:
                hit_sources.append("nickname")

            if hit_sources:
                hits.append(kw)
                weight = 0
                if "desc" in hit_sources:
                    weight += 10
                if "bio" in hit_sources:
                    weight += 8
                if "tags" in hit_sources:
                    weight += 5
                if "nickname" in hit_sources:
                    weight += 3
                score += weight

        score = min(100.0, score)
        if score == 0 and desc:
            score = 5.0
        if len(desc) > 20:
            score = min(100.0, score + 5)

        return score, hits

    def _score_video_quality(self, video: dict) -> float:
        plays = video.get("play_count", 0) or 0
        likes = video.get("digg_count", 0) or 0
        comments = video.get("comment_count", 0) or 0
        shares = video.get("share_count", 0) or 0

        play_score = self._log_score(plays, self.config.play_benchmark)

        total_engagement = likes + comments + shares
        if plays > 0:
            engagement_rate = total_engagement / plays
        else:
            engagement_rate = 0.0

        if engagement_rate > 0:
            engagement_score = min(100.0, 50 + 25 * math.log10(engagement_rate / self.config.engagement_benchmark))
        else:
            engagement_score = 0.0
        engagement_score = max(0.0, min(100.0, engagement_score))

        return (play_score + engagement_score) / 2.0

    def _is_personal_account(self, account: dict) -> bool:
        bio = (account.get("bio") or "").lower()
        nickname = (account.get("nickname") or "").lower()
        combined = f"{bio} {nickname}"
        for pat in self._personal_patterns:
            if pat.search(combined):
                return True
        return False

    @staticmethod
    def _log_score(value: float, benchmark: float) -> float:
        if value <= 0 or benchmark <= 0:
            return 0.0
        ratio = value / benchmark
        if ratio <= 0:
            return 0.0
        return max(0.0, min(100.0, 50 + 50 * math.log10(ratio)))


# ═══════════════════════════════════════════════════════════
# FinalScorer (阶段 4)
# ═══════════════════════════════════════════════════════════

class FinalScorer:
    """终极统一评分器

    仅对阶段 3 通过的最终候选视频 (≤10-20 条) 进行评分。
    三权重: 产品相关度(30%) + 视频质量(30%) + 商业意图(40%)。
    首次引入商业意图维度 — 这是"晚调 LLM"策略的关键:
    QuickScore 不碰商业意图, FinalScore 才引入。

    输入依赖:
      - QuickScorer 已写入 video["product_relevance"] 和 video["video_quality_score"]
      - CommentClassifier 已写入 video 的分类结果 (classify_result 参数)

    用法:
        cfg = FinalScorerConfig()
        scorer = FinalScorer(cfg)
        scored_videos, top_refs = scorer.score_all(
            videos, classify_results_by_video_id
        )
    """

    # 等级标签映射
    TIER_LABELS = {
        "S": "强烈对标",
        "A": "高对标",
        "B": "中对标",
        "C": "低对标",
        "D": "弱对标",
    }

    def __init__(self, config: FinalScorerConfig = None):
        self.config = config or FinalScorerConfig()
        # 归一化三权重
        w_sum = self.config.weight_product + self.config.weight_quality + self.config.weight_intent
        if w_sum <= 0:
            w_sum = 1.0
        self._w_prod = self.config.weight_product / w_sum
        self._w_qual = self.config.weight_quality / w_sum
        self._w_int = self.config.weight_intent / w_sum

    # ── 公开接口 ──

    def score(
        self,
        video: dict,
        classify_result=None,  # VideoClassifyResult from comment_classifier
    ) -> FinalScoreResult:
        """对单条视频进行终极评分

        Args:
            video: 视频数据, 需已含 product_relevance, video_quality_score
                    (由 QuickScorer.score_all() 写入)
            classify_result: VideoClassifyResult (来自 CommentClassifier)

        Returns:
            FinalScoreResult
        """
        # 1. 产品相关度 (复用阶段 1)
        product_rel = video.get("product_relevance", 0) or 0

        # 2. 视频质量 (复用阶段 1)
        video_qual = video.get("video_quality_score", 0) or 0

        # 3. 商业意图综合 (来自阶段 3, 首次引入)
        if classify_result is not None:
            intent_score = self._compute_intent_score(classify_result)
        else:
            intent_score = 0.0

        # 4. 加权 → sigmoid
        raw = self._w_prod * product_rel + self._w_qual * video_qual + self._w_int * intent_score
        final = self._sigmoid(raw)

        # 5. 分级
        tier, tier_label = self._assign_tier(final)

        return FinalScoreResult(
            product_relevance=round(product_rel, 1),
            video_quality=round(video_qual, 1),
            commercial_intent=round(intent_score, 1),
            final_score=round(final, 1),
            tier=tier,
            tier_label=tier_label,
            breakdown={
                "product_relevance": round(product_rel, 1),
                "video_quality": round(video_qual, 1),
                "commercial_intent": round(intent_score, 1),
                "raw_weighted": round(raw, 1),
                "final_sigmoid": round(final, 1),
                "weights": {
                    "product": round(self._w_prod, 2),
                    "quality": round(self._w_qual, 2),
                    "intent": round(self._w_int, 2),
                },
            },
        )

    def score_all(
        self,
        videos: list[dict],
        classify_map: dict = None,  # {video_id: VideoClassifyResult or dict}
        quick_intent_map: dict = None,  # {video_id: dict} QuickIntent fallback
    ) -> tuple[list[dict], list[dict]]:
        """批量终极评分, 返回 (全量评分视频, Top N 对标参考)

        Args:
            videos: 阶段 3 通过的视频列表
            classify_map: {video_id: VideoClassifyResult} (LLM 模式)
            quick_intent_map: {video_id: dict} (规则模式, classify_map 为空时使用)

        Returns:
            (all_scored_videos, top_reference_videos)
        """
        classify_map = classify_map or {}
        quick_intent_map = quick_intent_map or {}

        for v in videos:
            vid = v.get("id", "")
            cr = classify_map.get(vid)
            # 规则引擎 fallback: 当 LLM 未运行时, 用 QuickIntent 分数
            if cr is None and quick_intent_map:
                cr = quick_intent_map.get(vid)
            result = self.score(v, cr)

            # 写入视频字段
            v["final_score"] = result.final_score
            v["tier"] = result.tier
            v["tier_label"] = result.tier_label
            v["commercial_intent"] = result.commercial_intent
            v["score_breakdown"] = result.breakdown

        # 排序 + 取 Top N
        sorted_videos = sorted(
            videos, key=lambda v: v.get("final_score", 0), reverse=True
        )
        top_n = self.config.top_reference_n
        top_refs = sorted_videos[:top_n]

        for rank, v in enumerate(top_refs, 1):
            v["is_top_reference"] = True
            v["reference_rank"] = rank

        for v in sorted_videos[top_n:]:
            v["is_top_reference"] = False
            v["reference_rank"] = 0

        return sorted_videos, top_refs

    def aggregate_accounts(
        self, videos: list[dict], accounts: list[dict]
    ) -> list[dict]:
        """将视频级评分聚合为账号级商业价值

        每个账号的 reference_value = 高分级视频数 × 平均 final_score
        """
        acc_map = {a.get("username", ""): a for a in accounts}

        # 按账号分组视频
        from collections import defaultdict
        acc_videos = defaultdict(list)
        for v in videos:
            uname = v.get("account_username", "")
            acc_videos[uname].append(v)

        for uname, vlist in acc_videos.items():
            if uname in acc_map:
                scores = [v.get("final_score", 0) for v in vlist]
                avg_score = sum(scores) / len(scores) if scores else 0
                high_tier_count = sum(
                    1 for v in vlist if v.get("tier", "D") in ("S", "A", "B")
                )
                acc_map[uname]["reference_value"] = round(high_tier_count * avg_score, 1)
                acc_map[uname]["reference_video_count"] = len(vlist)
                acc_map[uname]["high_tier_video_count"] = high_tier_count

        # 按 reference_value 排序
        return sorted(
            acc_map.values(),
            key=lambda a: a.get("reference_value", 0),
            reverse=True,
        )

    # ── 商业意图综合评分 ──

    def _compute_intent_score(self, classify_result) -> float:
        """从分类结果计算商业意图综合分 (0-100)

        兼容两种输入:
          - VideoClassifyResult 对象 (LLM 模式, 来自 CommentClassifier)
          - dict (规则模式, 来自 QuickIntentScanner.score_comments())

        子维度:
          - intent_ratio (40%): 有意图评论占比 → 对数归一化
          - intent_quality (40%): 意图评论平均强度 → 直接映射
          - intent_diversity (20%): 意图类别多样性 → 阶梯映射
        """
        cf = self.config

        # 兼容属性访问 (对象) 和字典访问 (dict)
        def _get(key, default=0.0):
            if hasattr(classify_result, key):
                return getattr(classify_result, key) or default
            elif isinstance(classify_result, dict):
                return classify_result.get(key, default)
            return default

        # intent_ratio: 对数归一化 (基准 10% = 50 分)
        ratio = _get("intent_ratio")
        if ratio > 0:
            ratio_score = min(100.0, 50 + 30 * math.log10(ratio / 0.10))
        else:
            ratio_score = 0.0
        ratio_score = max(0.0, min(100.0, ratio_score))

        # intent_quality: 直接映射 (已为 0-100)
        quality_score = _get("intent_quality_score")

        # intent_diversity: 阶梯映射
        diversity = int(_get("intent_diversity"))
        if diversity >= 5:
            div_score = 100.0
        elif diversity >= 3:
            div_score = 70.0
        elif diversity >= 2:
            div_score = 40.0
        elif diversity >= 1:
            div_score = 20.0
        else:
            div_score = 0.0

        intent_score = (
            cf.intent_sub_ratio * ratio_score
            + cf.intent_sub_quality * quality_score
            + cf.intent_sub_diversity * div_score
        )
        return max(0.0, min(100.0, intent_score))

    # ── 分级 ──

    def _assign_tier(self, score: float) -> tuple[str, str]:
        thresholds = self.config.tier_thresholds
        for tier in ("S", "A", "B", "C"):
            if score >= thresholds.get(tier, 0):
                return tier, self.TIER_LABELS[tier]
        return "D", self.TIER_LABELS["D"]

    # ── Sigmoid ──

    def _sigmoid(self, raw: float) -> float:
        """将加权原始分压缩到 0-100"""
        center = self.config.sigmoid_center
        steepness = self.config.sigmoid_steepness
        try:
            return 100.0 / (1.0 + math.exp(-(raw - center) / steepness))
        except OverflowError:
            return 100.0 if raw > center else 0.0
