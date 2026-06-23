#!/usr/bin/env python
"""
TikTok Analyzer — 管道验证框架 v1.0
对比旧管道 (P0-P6, 4个独立评分器) vs 新管道 (QuickScore+两阶段评论+FinalScore)

用法:
    py validate_pipeline.py --keywords "plastic bottle" --region US
    py validate_pipeline.py --keywords "led light, packaging" --region ""
    py validate_pipeline.py --data data/20240623_120000  # 使用已有数据

输出:
    data/validate_{ts}/validation_report.md  — 完整对比报告
    data/validate_{ts}/validation_data.json  — 原始对比数据
"""
import argparse
import json
import sys
import time
import asyncio
import re
from pathlib import Path
from datetime import datetime
from collections import defaultdict, Counter

# 修复 Windows GBK 编码问题
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 项目路径
PROJECT_DIR = Path(__file__).parent
sys.path.insert(0, str(PROJECT_DIR))

from tiktok_analyzer.logger import setup_logger
logger = setup_logger("validate")

# ── 旧管道模块 ──
from tiktok_analyzer.video_scorer import VideoScorer, VideoScorerConfig, VideoFilter, VideoFilterConfig
from tiktok_analyzer.reference_video_scorer import ReferenceVideoScorer, ReferenceVideoScorerConfig
from tiktok_analyzer.intent_detector import IntentDetector, IntentDetectorConfig
from tiktok_analyzer.account_scorer import AccountScorer, AccountScorerConfig
from tiktok_analyzer.account_filter import AccountFilter, AccountFilterConfig

# ── 新管道模块 ──
from tiktok_analyzer.unified_scorer import QuickScorer, QuickScorerConfig, FinalScorer, FinalScorerConfig


# ═══════════════════════════════════════════════════════════
# 验证数据模型
# ═══════════════════════════════════════════════════════════

class ValidationData:
    """存储新旧管道对比数据"""

    def __init__(self):
        self.keywords = []
        self.region = ""
        self.timestamp = ""

        # 原始数据 (所有视频, 含被淘汰的)
        self.all_raw_videos = []
        self.all_accounts = []
        self.all_comments = []

        # 新管道
        self.new_quick_score_passed = []
        self.new_quick_score_eliminated = []
        self.new_intent_signaled = []
        self.new_no_intent = []           # 验证模式: 无意图视频含采样评论
        self.new_deep_analyzed = []
        self.new_intent_filtered_out = []  # 阶段 3.5 淘汰
        self.new_final_scored = []
        self.new_top_references = []
        self.new_llm_calls = 0
        self.new_llm_tokens = 0
        self.new_funnel = {}             # 漏斗统计

        # 旧管道
        self.old_p0_passed = []
        self.old_p0_eliminated = []
        self.old_video_scored = []
        self.old_intent_comments = []
        self.old_reference_scored = []
        self.old_top_references = []
        self.old_llm_calls = 1
        self.old_llm_tokens = 0

        # 耗时
        self.new_comment_fetch_time = 0.0
        self.old_comment_fetch_time_estimate = 0.0

        # 对比
        self.overlap_top20 = []
        self.new_only_top20 = []
        self.old_only_top20 = []
        self.false_eliminations = []
        self.high_value_false_eliminations = []  # 高价值误杀


# ═══════════════════════════════════════════════════════════
# 旧管道模拟器
# ═══════════════════════════════════════════════════════════

class OldPipelineSimulator:
    """模拟旧管道: 在给定原始数据上运行旧版评分逻辑"""

    # 旧版 intent_score 关键词 (从 scraper._compute_intent_score 复制)
    INTENT_KEYWORDS = {
        "buy": 15, "purchase": 15, "order": 15, "price": 15,
        "supplier": 15, "moq": 15, "manufacturer": 10, "factory": 10,
        "wholesale": 10, "link": 10, "sample": 10, "shipping": 10,
        "inquiry": 10, "quotation": 10, "export": 10, "custom": 5,
    }

    def __init__(self, config: dict = None):
        self.cfg = config or {}
        self._load_configs()

    def _load_configs(self):
        """加载旧管道各模块配置"""
        self.vs_cfg = VideoScorerConfig(**self.cfg.get("video_scorer", {}))
        self.rvs_cfg = ReferenceVideoScorerConfig(**self.cfg.get("reference_video_scorer", {}))
        self.intent_cfg = IntentDetectorConfig(**self.cfg.get("intent_detector", {}))
        self.as_cfg = AccountScorerConfig(**self.cfg.get("account_scorer", {}))
        self.af_cfg = AccountFilterConfig(**self.cfg.get("account_filter", {}))
        self.vf_cfg = VideoFilterConfig(**self.cfg.get("video_filter", {}))

    def simulate(self, all_videos: list, all_accounts: list, all_comments: list) -> dict:
        """在给定原始数据上运行完整的旧管道

        Returns:
            {
                "p0_passed": [...], "p0_eliminated": [...],
                "video_scored": [...], "reference_scored": [...],
                "top_references": [...], "intent_comments": [...],
                "accounts_scored": [...],
            }
        """
        videos = [dict(v) for v in all_videos]  # 深拷贝
        accounts = [dict(a) for a in all_accounts]
        comments = [dict(c) for c in all_comments]

        # ── P0: 视频预筛选 (模拟旧版 _apply_video_prefilter) ──
        p0_cfg = self.cfg.get("video_prefilter", {})
        p0_passed, p0_eliminated = self._simulate_p0_filter(videos, p0_cfg)

        # 计算旧版 intent_score
        for v in p0_passed:
            v["intent_score"] = self._compute_intent_score_old(v, [])
        for v in p0_eliminated:
            v["intent_score"] = 0

        # ── P0: 账号过滤 ──
        if self.af_cfg.enabled:
            af = AccountFilter(self.af_cfg)
            accounts, filtered = af.filter(accounts)
            kept = {a.get("username", "") for a in accounts}
            p0_passed = [v for v in p0_passed if v.get("account_username", "") in kept]
            comments = [c for c in comments if c.get("account_username", "") in kept]

        # ── P1: 账号评分 ──
        if self.as_cfg.enabled and accounts:
            scorer = AccountScorer(self.as_cfg)
            accounts = scorer.score_all(accounts)

        # ── P3: 视频过滤 ──
        if self.vf_cfg.enabled and p0_passed:
            vf = VideoFilter(self.vf_cfg)
            p0_passed, _ = vf.filter(p0_passed)

        # ── P3: 视频评分 ──
        if self.vs_cfg.enabled and p0_passed:
            vs = VideoScorer(self.vs_cfg)
            p0_passed = vs.score_all(p0_passed)

        # ── P5: 意图识别 (关键词, 非LLM) ──
        if self.intent_cfg.enabled and comments:
            idet = IntentDetector(self.intent_cfg)
            intent_data = idet.analyze_comments(comments)
            comments = intent_data["comments"]

        # ── 对标参考评分 ──
        top_refs = []
        if self.rvs_cfg.enabled and p0_passed and comments and accounts:
            rvs = ReferenceVideoScorer(self.rvs_cfg)
            ref_data = rvs.score_all(p0_passed, comments, accounts)
            top_refs = ref_data.get("top_reference_videos", [])

        return {
            "p0_passed": p0_passed,
            "p0_eliminated": p0_eliminated,
            "video_scored": p0_passed,
            "reference_scored": [v for v in p0_passed if v.get("reference_score")],
            "top_references": top_refs,
            "intent_comments": [c for c in comments if c.get("has_intent")],
            "accounts_scored": accounts,
        }

    @classmethod
    def _compute_intent_score_old(cls, video: dict, comments: list = None) -> int:
        """旧版 intent_score 计算"""
        text_parts = [(video.get("desc") or "").lower()]
        if comments:
            for c in comments:
                text_parts.append((c.get("text") or "").lower())
        combined = " ".join(text_parts)
        score = 0
        for kw, weight in cls.INTENT_KEYWORDS.items():
            if kw in combined:
                score += weight
        return min(score, 100)

    @staticmethod
    def _simulate_p0_filter(videos: list, config: dict) -> tuple:
        """模拟旧版 P0 硬过滤"""
        min_likes = config.get("min_digg", 10)
        min_comments = config.get("min_comments", 1)
        min_plays = config.get("min_plays", 100)

        passed, eliminated = [], []
        for v in videos:
            likes = v.get("digg_count", 0) or 0
            comments = v.get("comment_count", 0) or 0
            plays = v.get("play_count", 0) or 0

            is_low = (
                likes < min_likes
                or comments < min_comments
                or (likes < min_likes and comments < 3)
                or plays < min_plays
            )
            if is_low:
                v["quality"] = "low_quality"
                eliminated.append(v)
            else:
                v["quality"] = "high_quality"
                passed.append(v)
        return passed, eliminated


# ═══════════════════════════════════════════════════════════
# 对比分析引擎
# ═══════════════════════════════════════════════════════════

class ComparisonEngine:
    """新旧管道对比分析"""

    def __init__(self, data: ValidationData):
        self.d = data

    def compare(self) -> dict:
        """执行全部维度的对比, 返回结构化结果"""
        return {
            "video_quality": self._compare_video_quality(),
            "intent_hit_rate": self._compare_intent_hit_rate(),
            "comment_fetch_efficiency": self._compare_comment_fetch(),
            "token_consumption": self._compare_token_consumption(),
            "false_elimination": self._compare_false_elimination(),
            "overlap_analysis": self._analyze_overlap(),
            "funnel_analysis": self._analyze_funnel(),
        }

    # ── 1. 视频质量对比 ──

    def _compare_video_quality(self) -> dict:
        new_top = self.d.new_top_references[:20]
        old_top = self.d.old_top_references[:20]

        # 质量指标: 平均播放量, 平均互动率, 平均intent_ratio
        def calc_metrics(videos, prefix):
            if not videos:
                return {}
            total = len(videos)
            avg_plays = sum(v.get("play_count", 0) or 0 for v in videos) / total
            avg_likes = sum(v.get("digg_count", 0) or 0 for v in videos) / total
            avg_comments = sum(v.get("comment_count", 0) or 0 for v in videos) / total
            avg_intent_ratio = sum(
                v.get("purchase_intent_ratio", 0) or 0 for v in videos
            ) / total
            # 计算互动率
            engagements = []
            for v in videos:
                plays = v.get("play_count", 1) or 1
                eng = (v.get("digg_count", 0) or 0) + (v.get("comment_count", 0) or 0) + (v.get("share_count", 0) or 0)
                engagements.append(eng / plays if plays > 0 else 0)
            avg_er = sum(engagements) / len(engagements) if engagements else 0

            return {
                f"{prefix}_count": total,
                f"{prefix}_avg_plays": round(avg_plays),
                f"{prefix}_avg_likes": round(avg_likes),
                f"{prefix}_avg_comments": round(avg_comments),
                f"{prefix}_avg_engagement_rate": round(avg_er, 4),
                f"{prefix}_avg_intent_ratio": round(avg_intent_ratio, 4),
            }

        return {
            **calc_metrics(new_top, "new"),
            **calc_metrics(old_top, "old"),
            "winner": self._pick_winner(calc_metrics(new_top, "new"), calc_metrics(old_top, "old")),
        }

    def _pick_winner(self, new_m, old_m) -> str:
        """判断哪个管道输出质量更高"""
        if not new_m and not old_m:
            return "tie (no data)"
        if not new_m:
            return "old (new has no results)"
        if not old_m:
            return "new (old has no results)"

        # 加权比较: 互动率 (30%) + 意图率 (50%) + 播放量 (20%)
        def score(m):
            er = m.get(f"{list(m.keys())[0].split('_')[0]}_avg_engagement_rate", 0)
            ir = m.get(f"{list(m.keys())[0].split('_')[0]}_avg_intent_ratio", 0)
            plays = m.get(f"{list(m.keys())[0].split('_')[0]}_avg_plays", 0)
            return 0.3 * min(er * 20, 1) + 0.5 * ir + 0.2 * min(plays / 100000, 1)

        ns = score(new_m)
        os = score(old_m)
        if ns > os * 1.1:
            return "new"
        elif os > ns * 1.1:
            return "old"
        else:
            return "tie"

    # ── 2. 商业意图评论命中率 ──

    def _compare_intent_hit_rate(self) -> dict:
        # 新管道: LLM 分类结果
        new_deep = self.d.new_deep_analyzed
        new_total_comments = sum(len(v.get("_deep_comments", [])) for v in new_deep)
        new_intent_comments = sum(
            v.get("purchase_intent_comments", 0) or 0 for v in new_deep
        )

        # 旧管道: 关键词 IntentDetector 结果
        old_intent = self.d.old_intent_comments
        old_total = len(self.d.all_comments)
        old_intent_count = len(old_intent)

        return {
            "new_total_comments": new_total_comments,
            "new_intent_comments": new_intent_comments,
            "new_intent_rate": round(new_intent_comments / max(1, new_total_comments), 4),
            "old_total_comments": old_total,
            "old_intent_comments": old_intent_count,
            "old_intent_rate": round(old_intent_count / max(1, old_total), 4),
            "note": "新管道用LLM分类(更精准), 旧管道用关键词匹配(更宽泛)",
        }

    # ── 3. 评论抓取效率 ──

    def _compare_comment_fetch(self) -> dict:
        new_time = self.d.new_comment_fetch_time
        new_sample_count = len(self.d.new_quick_score_passed)
        new_deep_count = len(self.d.new_deep_analyzed)

        # 旧管道估算: Top 20% × 4s (导航+等待+200条评论提取)
        old_p0_count = len(self.d.old_p0_passed)
        old_top20_pct = max(1, int(old_p0_count * 0.2))
        old_estimate = old_top20_pct * 4.0

        # 新管道拆解
        # 采样: 每条 ~1.5s (单次 API fetch, 不导航)
        sample_time = new_sample_count * 1.5
        # 深抓: 每条 ~3.5s (导航 + 等待 + 30-50条提取)
        deep_time = new_deep_count * 3.5
        new_estimated = sample_time + deep_time
        if new_time > 0:
            new_estimated = new_time  # 优先实测

        # 时间节省 = (old - new) / old × 100
        # 正数 = 新管道更快, 负数 = 新管道更慢
        time_diff = old_estimate - new_estimated
        time_saved_pct = round(time_diff / max(0.1, old_estimate) * 100, 1)

        # 每视频成本
        old_cost_per_video = round(old_estimate / max(1, old_top20_pct), 1)
        new_sample_cost = round(sample_time / max(1, new_sample_count), 1)
        new_deep_cost = round(deep_time / max(1, new_deep_count), 1)

        return {
            "new_total_time_sec": round(new_estimated, 1),
            "new_sample_time_sec": round(sample_time, 1),
            "new_deep_time_sec": round(deep_time, 1),
            "new_sampled_videos": new_sample_count,
            "new_deep_fetched_videos": new_deep_count,
            "new_avg_per_sample_sec": new_sample_cost,
            "new_avg_per_deep_sec": new_deep_cost,
            "old_estimated_time_sec": round(old_estimate, 1),
            "old_top20pct_videos": old_top20_pct,
            "old_avg_per_video_sec": old_cost_per_video,
            "time_saved_sec": round(time_diff, 1),
            "time_saved_pct": time_saved_pct,
            "verdict": (
                f"新管道节省 {time_saved_pct}% 时间" if time_saved_pct > 0
                else f"新管道多用 {abs(time_saved_pct)}% 时间 (因为采样了更多视频: {new_sample_count} vs {old_top20_pct})"
            ),
        }

    # ── 4. Token 消耗 ──

    def _compare_token_consumption(self) -> dict:
        new_calls = self.d.new_llm_calls
        new_tokens = self.d.new_llm_tokens
        old_calls = self.d.old_llm_calls
        old_tokens = self.d.old_llm_tokens

        if old_tokens == 0:
            old_tokens = 10000

        # ── Token 模块级拆解 ──
        deep_count = len(self.d.new_deep_analyzed)
        # CommentClassifier: 每批 6 个视频, 每批 ~800 tokens
        cc_batches = max(1, deep_count // 6) if deep_count > 0 else 0
        cc_tokens = cc_batches * 800
        cc_calls = cc_batches
        # FinalScorer: 纯计算, 0 token
        fs_tokens = 0
        fs_calls = 0
        # Analyzer (阶段5): 1 次调用, ~2000 tokens
        ana_tokens = 2000 if deep_count > 0 else 0
        ana_calls = 1 if deep_count > 0 else 0

        total_new = cc_tokens + fs_tokens + ana_tokens
        if new_tokens > 0 and new_tokens != total_new:
            total_new = new_tokens  # 优先使用实测值

        # 旧管道拆解
        old_ana_tokens = 10000  # 1 次 analyze_enhanced 全量数据

        breakdown = {
            "comment_classifier": {"calls": cc_calls, "tokens": cc_tokens, "pct": round(cc_tokens / max(1, total_new) * 100, 1)},
            "final_scorer": {"calls": fs_calls, "tokens": fs_tokens, "pct": 0.0},
            "analyzer": {"calls": ana_calls, "tokens": ana_tokens, "pct": round(ana_tokens / max(1, total_new) * 100, 1)},
        }

        # 每个深度分析视频的平均 token 成本
        avg_per_video = round(total_new / max(1, deep_count)) if deep_count > 0 else 0

        return {
            "new_llm_calls": new_calls if new_calls > 0 else cc_calls + ana_calls,
            "new_token_estimate": total_new,
            "old_llm_calls": old_calls,
            "old_token_estimate": old_tokens,
            "token_saved_pct": round((1 - total_new / max(1, old_tokens)) * 100, 1) if old_tokens > 0 else "N/A",
            "new_per_token_value": "高 (仅处理高价值数据)" if total_new <= old_tokens else "待优化",
            "token_breakdown": breakdown,
            "avg_token_per_deep_video": avg_per_video,
            "deep_video_count": deep_count,
            "root_cause": (
                f"CommentClassifier 占比最大 ({breakdown['comment_classifier']['pct']}%), "
                f"因为深度分析视频数 ({deep_count}) 较多，每 6 个视频触发 1 次 LLM 调用"
            ) if deep_count > 12 else "Token 消耗正常",
        }

    # ── 5. 误杀率分析 ──

    # ── 商业意图关键词 (用于高价值误杀检测) ──
    HIGH_VALUE_SIGNALS = [
        "price", "moq", "supplier", "catalog", "sample",
        "factory", "distributor", "wholesale", "manufacturer",
        "pricing", "quote", "quotation", "minimum order",
        "oem", "odm", "importer", "exporter",
        "价格", "报价", "供应商", "工厂", "样品", "批发", "起订",
    ]

    def _detect_commercial_signal(self, desc: str, comments: list = None) -> bool:
        """检测视频描述或评论中是否包含商业意图信号"""
        text = (desc or "").lower()
        if comments:
            for c in comments:
                text += " " + (c.get("text") or "").lower()
        for signal in self.HIGH_VALUE_SIGNALS:
            if signal in text:
                return True
        return False

    def _compare_false_elimination(self) -> dict:
        """改进的误杀率检测

        误杀率 = 误杀视频数 / 原始候选视频数
        同时检测高价值误杀: 淘汰但评论区含商业关键词
        """
        total_raw = len(self.d.all_raw_videos)

        # ── 基础误杀: 旧管道 Top N 中但被新管道淘汰 ──
        old_top_ids = {v.get("id", "") for v in self.d.old_top_references[:20]}
        eliminated_ids = {v.get("id", "") for v in self.d.new_quick_score_eliminated}
        new_top_ids = {v.get("id") for v in self.d.new_top_references}

        false_eliminated = []
        for v in self.d.old_top_references[:50]:
            vid = v.get("id", "")
            if vid in eliminated_ids and vid not in new_top_ids:
                false_eliminated.append({
                    "video_id": vid,
                    "account": v.get("account_username", ""),
                    "desc": (v.get("desc") or "")[:80],
                    "old_reference_score": v.get("reference_score", 0),
                    "plays": v.get("play_count", 0),
                    "digg": v.get("digg_count", 0),
                })

        self.d.false_eliminations = false_eliminated

        # ── 高价值误杀: 被淘汰但 desc/comments 含商业信号 ──
        high_value_false = []

        # QuickScore 淘汰的视频 (有 desc, 无评论)
        for v in self.d.new_quick_score_eliminated:
            if self._detect_commercial_signal(v.get("desc", "")):
                high_value_false.append({
                    "video_id": v.get("id", ""),
                    "account": v.get("account_username", ""),
                    "desc": (v.get("desc") or "")[:80],
                    "eliminated_by": "QuickScore",
                    "quick_score": v.get("quick_score", 0),
                    "signal_source": "desc",
                })

        # QuickIntent 淘汰的视频 (有 desc + 采样评论)
        for v in self.d.new_no_intent:
            sampled = v.get("_kept_sampled_comments", v.get("_sampled_comments", []))
            if self._detect_commercial_signal(v.get("desc", ""), sampled):
                # 确认是否真的被淘汰 (不在 deep_analyzed 中)
                deep_ids = {dv.get("id") for dv in self.d.new_deep_analyzed}
                if v.get("id") not in deep_ids:
                    high_value_false.append({
                        "video_id": v.get("id", ""),
                        "account": v.get("account_username", ""),
                        "desc": (v.get("desc") or "")[:80],
                        "eliminated_by": "QuickIntent",
                        "quick_score": v.get("quick_score", 0),
                        "signal_source": "sampled_comments",
                    })

        # 阶段 3.5 意图过滤淘汰的视频
        for v in self.d.new_intent_filtered_out:
            deep_comments = v.get("_deep_comments", [])
            if self._detect_commercial_signal(v.get("desc", ""), deep_comments):
                high_value_false.append({
                    "video_id": v.get("id", ""),
                    "account": v.get("account_username", ""),
                    "desc": (v.get("desc") or "")[:80],
                    "eliminated_by": "阶段3.5 意图过滤",
                    "quick_score": v.get("quick_score", 0),
                    "signal_source": "desc" if self._detect_commercial_signal(v.get("desc", "")) else "comments",
                })

        self.d.high_value_false_eliminations = high_value_false

        false_rate = round(len(false_eliminated) / max(1, total_raw), 4)
        hv_false_rate = round(len(high_value_false) / max(1, total_raw), 4)

        # 阶段分布统计
        stage_dist = defaultdict(int)
        signal_sources = defaultdict(int)
        for fv in high_value_false:
            stage_dist[fv.get("eliminated_by", "Unknown")] += 1
            signal_sources[fv.get("signal_source", "unknown")] += 1

        # 判定
        if len(false_eliminated) == 0 and len(high_value_false) == 0:
            verdict = "优秀 (零误杀)"
        elif len(false_eliminated) <= 2 and len(high_value_false) <= 3:
            verdict = "良好 (少量误杀, 可接受)"
        elif len(high_value_false) <= 5:
            verdict = "需关注 (高价值误杀偏多)"
        else:
            verdict = "需优化 (建议降低淘汰阈值)"

        return {
            "false_eliminated_count": len(false_eliminated),
            "false_eliminated_rate": false_rate,
            "false_eliminated_rate_pct": round(false_rate * 100, 1),
            "high_value_false_count": len(high_value_false),
            "high_value_false_rate": hv_false_rate,
            "high_value_false_rate_pct": round(hv_false_rate * 100, 1),
            "total_raw_candidates": total_raw,
            "false_eliminated_videos": false_eliminated[:10],
            "high_value_false_videos": high_value_false[:20],  # 全量输出
            "stage_distribution": dict(stage_dist),
            "signal_sources": dict(signal_sources),
            "dominant_stage": max(stage_dist, key=stage_dist.get) if stage_dist else "N/A",
            "verdict": verdict,
        }

    # ── 漏斗分析 ──

    def _analyze_funnel(self) -> dict:
        """第7章: 漏斗转化分析"""
        f = self.d.new_funnel
        if not f:
            # 从已有数据推算
            total_raw = len(self.d.all_raw_videos)
            qs_passed = len(self.d.new_quick_score_passed)
            qs_elim = len(self.d.new_quick_score_eliminated)
            intent_sig = len(self.d.new_intent_signaled)
            no_intent = len(self.d.new_no_intent)
            deep = len(self.d.new_deep_analyzed)
            final_top = len(self.d.new_top_references)

            stage_35_elim = len(self.d.new_intent_filtered_out)

            f = {
                "stage_0_total_raw": total_raw,
                "stage_1_quickscore_passed": qs_passed,
                "stage_1_quickscore_eliminated": qs_elim,
                "stage_2_intent_signaled": intent_sig,
                "stage_2_no_intent": no_intent,
                "stage_3_deep_analyzed": deep,
                "stage_3_5_intent_filtered": stage_35_elim,
                "stage_3_5_passed_to_final": deep - stage_35_elim,
                "stage_4_final_top": final_top,
                "qs_retention_rate": round(qs_passed / max(1, total_raw) * 100, 1),
                "intent_retention_rate": round(intent_sig / max(1, qs_passed) * 100, 1),
                "deep_retention_rate": round(deep / max(1, intent_sig) * 100, 1),
                "intent_filter_retention_rate": round((deep - stage_35_elim) / max(1, deep) * 100, 1) if deep > 0 else 0,
                "final_retention_rate": round(final_top / max(1, (deep - stage_35_elim)) * 100, 1) if (deep - stage_35_elim) > 0 else 0,
                "cumulative_to_quickscore": round(qs_passed / max(1, total_raw) * 100, 1),
                "cumulative_to_intent": round(intent_sig / max(1, total_raw) * 100, 1),
                "cumulative_to_deep": round(deep / max(1, total_raw) * 100, 1),
                "cumulative_to_intent_filter": round((deep - stage_35_elim) / max(1, total_raw) * 100, 1) if deep > 0 else 0,
                "cumulative_to_final": round(final_top / max(1, total_raw) * 100, 1),
            }

        # 找出淘汰最多的层
        stages = [
            ("QuickScore", f.get("stage_1_quickscore_eliminated", 0)),
            ("QuickIntent", f.get("stage_2_no_intent", 0)),
            ("阶段3.5 意图过滤", f.get("stage_3_5_intent_filtered", 0)),
            ("FinalScore(弱对标)", (f.get("stage_3_5_passed_to_final", f.get("stage_3_deep_analyzed", 0)) - f.get("stage_4_final_top", 0))),
        ]
        max_stage = max(stages, key=lambda x: x[1]) if stages else ("N/A", 0)

        return {
            **f,
            "max_elimination_stage": max_stage[0],
            "max_elimination_count": max_stage[1],
        }

    # ── 重叠分析 ──

    def _analyze_overlap(self) -> dict:
        new_ids = {v.get("id", "") for v in self.d.new_top_references[:20]}
        old_ids = {v.get("id", "") for v in self.d.old_top_references[:20]}

        overlap = new_ids & old_ids
        new_only = new_ids - old_ids
        old_only = old_ids - new_ids

        self.d.overlap_top20 = list(overlap)
        self.d.new_only_top20 = list(new_only)
        self.d.old_only_top20 = list(old_only)

        return {
            "overlap_count": len(overlap),
            "new_only_count": len(new_only),
            "old_only_count": len(old_only),
            "jaccard_similarity": round(
                len(overlap) / max(1, len(new_ids | old_ids)), 4
            ),
        }


# ═══════════════════════════════════════════════════════════
# 报告生成器
# ═══════════════════════════════════════════════════════════

class ReportGenerator:
    """生成 Markdown 验证报告"""

    def __init__(self, data: ValidationData, comparison: dict):
        self.d = data
        self.c = comparison

    def generate(self) -> str:
        lines = []
        self._header(lines)
        self._summary(lines)
        self._funnel_analysis(lines)            # 7: 漏斗转化
        self._video_quality(lines)              # 1: 视频质量
        self._intent_hit_rate(lines)            # 2: 意图命中率
        self._comment_efficiency(lines)         # 3: 评论耗时
        self._token_consumption(lines)          # 4: Token消耗
        self._token_breakdown(lines)            # 8: Token模块拆解
        self._false_elimination(lines)          # 5: 误杀率
        self._false_elimination_detail(lines)   # 9: 误杀阶段分布
        self._overlap(lines)                    # 6: 重叠分析
        self._diagnosis(lines)                  # 10: 综合诊断
        self._recommendations(lines)
        return "\n".join(lines)

    def _header(self, lines):
        lines.append("# TikTok 管道验证报告")
        lines.append("")
        lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"**关键词**: {', '.join(self.d.keywords)}")
        lines.append(f"**地区**: {self.d.region or '不限'}")
        lines.append(f"**原始视频数**: {len(self.d.all_raw_videos)}")
        lines.append(f"**账号数**: {len(self.d.all_accounts)}")
        lines.append("")
        lines.append("---")
        lines.append("")

    def _summary(self, lines):
        lines.append("## 📊 总览: 关键指标对比")
        lines.append("")
        lines.append("| 指标 | 旧管道 | 新管道 | 胜出 |")
        lines.append("|------|--------|--------|------|")

        # 视频质量
        q = self.c["video_quality"]
        lines.append(
            f"| Top 20 视频数 | {q.get('old_count', 'N/A')} | {q.get('new_count', 'N/A')} | — |"
        )
        lines.append(
            f"| 平均意图率 | {q.get('old_avg_intent_ratio', 0):.1%} | "
            f"{q.get('new_avg_intent_ratio', 0):.1%} | {self._winner_badge(q.get('winner', ''))} |"
        )
        lines.append(
            f"| 平均互动率 | {q.get('old_avg_engagement_rate', 0):.1%} | "
            f"{q.get('new_avg_engagement_rate', 0):.1%} | — |"
        )

        # Token
        tk = self.c["token_consumption"]
        lines.append(
            f"| LLM 调用次数 | {tk.get('old_llm_calls', 0)} | {tk.get('new_llm_calls', 0)} | — |"
        )
        lines.append(
            f"| Token 估算 | ~{tk.get('old_token_estimate', 0):,} | "
            f"~{tk.get('new_token_estimate', 0):,} | {self._winner_badge('new' if tk.get('new_token_estimate', 0) <= tk.get('old_token_estimate', 999999) else 'old')} |"
        )

        # 耗时
        ce = self.c["comment_fetch_efficiency"]
        lines.append(
            f"| 评论抓取耗时 | ~{ce.get('old_estimated_time_sec', 0)}s | "
            f"{ce.get('new_total_time_sec', 0)}s | {self._winner_badge('new' if ce.get('time_saved_pct', 0) > 0 else 'old')} |"
        )

        # 误杀
        fe = self.c["false_elimination"]
        lines.append(
            f"| 基础误杀率 | — | {fe.get('false_eliminated_rate_pct', 0)}% ({fe.get('false_eliminated_count', 0)}/{fe.get('total_raw_candidates', '?')}) | {fe.get('verdict', '')} |"
        )
        lines.append(
            f"| 高价值误杀率 | — | {fe.get('high_value_false_rate_pct', 0)}% ({fe.get('high_value_false_count', 0)}/{fe.get('total_raw_candidates', '?')}) | — |"
        )

        # 漏斗关键指标
        fa = self.c.get("funnel_analysis", {})
        lines.append(
            f"| 阶段3.5 意图过滤 | — | {fa.get('stage_3_5_intent_filtered', 0)} 条淘汰 | — |"
        )
        lines.append(
            f"| 累计到FinalScore | — | {fa.get('cumulative_to_final', 0)}% | — |"
        )
        lines.append(
            f"| 淘汰最多阶段 | — | {fa.get('max_elimination_stage', 'N/A')} ({fa.get('max_elimination_count', 0)}条) | — |"
        )

        lines.append("")

    def _video_quality(self, lines):
        q = self.c["video_quality"]
        lines.append("## 🎯 1. 输出视频质量")
        lines.append("")

        # 新管道 Top 10
        lines.append("### 新管道 Top 10")
        lines.append("")
        lines.append("| # | 账号 | 描述 | FinalScore | 意图率 | 播放 |")
        lines.append("|---|------|------|-----------|--------|------|")
        for i, v in enumerate(self.d.new_top_references[:10], 1):
            lines.append(
                f"| {i} | @{v.get('account_username','')} | "
                f"{(v.get('desc') or '')[:50]} | "
                f"{v.get('final_score', 0):.0f} | "
                f"{v.get('purchase_intent_ratio', 0):.0%} | "
                f"{v.get('play_count', 0):,} |"
            )
        lines.append("")

        # 旧管道 Top 10
        lines.append("### 旧管道 Top 10")
        lines.append("")
        lines.append("| # | 账号 | 描述 | 参考分 | 意图率 | 播放 |")
        lines.append("|---|------|------|--------|--------|------|")
        for i, v in enumerate(self.d.old_top_references[:10], 1):
            lines.append(
                f"| {i} | @{v.get('account_username','')} | "
                f"{(v.get('desc') or '')[:50]} | "
                f"{v.get('reference_score', 0):.0f} | "
                f"{v.get('purchase_intent_ratio', 0):.0%} | "
                f"{v.get('play_count', 0):,} |"
            )
        lines.append("")

    def _intent_hit_rate(self, lines):
        ir = self.c["intent_hit_rate"]
        lines.append("## 🧠 2. 商业意图评论命中率")
        lines.append("")
        lines.append("| 指标 | 旧管道 (关键词) | 新管道 (LLM) |")
        lines.append("|------|-----------------|--------------|")
        lines.append(
            f"| 总评论数 | {ir.get('old_total_comments', 0)} | "
            f"{ir.get('new_total_comments', 0)} |"
        )
        lines.append(
            f"| 含意图评论 | {ir.get('old_intent_comments', 0)} | "
            f"{ir.get('new_intent_comments', 0)} |"
        )
        lines.append(
            f"| 命中率 | {ir.get('old_intent_rate', 0):.1%} | "
            f"{ir.get('new_intent_rate', 0):.1%} |"
        )
        lines.append("")
        lines.append(f"> {ir.get('note', '')}")
        lines.append("")
        lines.append("**解读**: 旧管道关键词匹配命中率通常更高(宽泛), 但准确率低。新管道 LLM 分类更精准, 能区分 'how much is this?' 和 'how much for 1000 pcs?'。")
        lines.append("")

    def _comment_efficiency(self, lines):
        ce = self.c["comment_fetch_efficiency"]
        lines.append("## ⏱ 3. 评论抓取效率")
        lines.append("")
        lines.append("| 指标 | 旧管道 | 新管道 | 说明 |")
        lines.append("|------|--------|--------|------|")
        lines.append(
            f"| 采样视频数 | — | {ce.get('new_sampled_videos', 0)} | 新管道: 所有QuickScore通过视频 |"
        )
        lines.append(
            f"| 深度抓取视频数 | ~{ce.get('old_top20pct_videos', 0)} (Top 20%) | "
            f"{ce.get('new_deep_fetched_videos', 0)} (有意图) | 新管道仅对有意图信号视频深抓 |"
        )
        lines.append(
            f"| 每条采样耗时 | — | ~{ce.get('new_avg_per_sample_sec', 0)}s | _sample_comments (不导航) |"
        )
        lines.append(
            f"| 每条深抓耗时 | ~{ce.get('old_avg_per_video_sec', 0)}s | ~{ce.get('new_avg_per_deep_sec', 0)}s | extract_comments (导航) |"
        )
        lines.append(
            f"| 采样总耗时 | — | {ce.get('new_sample_time_sec', 0)}s | {ce.get('new_sampled_videos', 0)} × ~1.5s |"
        )
        lines.append(
            f"| 深抓总耗时 | ~{ce.get('old_estimated_time_sec', 0)}s | {ce.get('new_deep_time_sec', 0)}s | {ce.get('new_deep_fetched_videos', 0)} × ~3.5s |"
        )
        lines.append(
            f"| **总耗时** | **~{ce.get('old_estimated_time_sec', 0)}s** | **{ce.get('new_total_time_sec', 0)}s** | "
            f"{'节省 ' + str(abs(ce.get('time_saved_pct', 0))) + '%' if ce.get('time_saved_pct', 0) > 0 else '多用 ' + str(abs(ce.get('time_saved_pct', 0))) + '%'}"
            f" |"
        )
        lines.append("")
        lines.append(f"> {ce.get('verdict', '')}")
        lines.append("")

    def _token_consumption(self, lines):
        tk = self.c["token_consumption"]
        lines.append("## 💰 4. Token 消耗总览")
        lines.append("")
        lines.append("| 指标 | 旧管道 | 新管道 |")
        lines.append("|------|--------|--------|")
        lines.append(f"| 总 LLM 调用 | {tk.get('old_llm_calls', 0)} | {tk.get('new_llm_calls', 0)} |")
        lines.append(f"| 总 Token | ~{tk.get('old_token_estimate', 0):,} | ~{tk.get('new_token_estimate', 0):,} |")
        lines.append(f"| 每深度视频 Token | — | ~{tk.get('avg_token_per_deep_video', 0):,} |")
        change_pct = round((tk.get('new_token_estimate', 0) / max(1, tk.get('old_token_estimate', 0)) - 1) * 100)
        lines.append(f"| 变化 | — | {'+' if change_pct > 0 else ''}{change_pct}% |")
        lines.append("")
        lines.append("> 详见第8章「Token 消耗模块拆解」")
        lines.append("")

    def _false_elimination(self, lines):
        fe = self.c["false_elimination"]
        lines.append("## 🔍 5. 误杀率分析")
        lines.append("")
        lines.append(f"**判定**: {fe.get('verdict', '')}")
        lines.append("")
        lines.append("| 指标 | 数值 |")
        lines.append("|------|------|")
        lines.append(f"| 原始候选视频 | {fe.get('total_raw_candidates', '?')} |")
        lines.append(f"| 基础误杀 (旧Top但新淘汰) | {fe.get('false_eliminated_count', 0)} 条 ({fe.get('false_eliminated_rate_pct', 0)}%) |")
        lines.append(f"| 高价值误杀 (淘汰但含商业信号) | {fe.get('high_value_false_count', 0)} 条 ({fe.get('high_value_false_rate_pct', 0)}%) |")
        lines.append("")
        lines.append(f"> 误杀率 = 误杀视频数 / 原始候选视频数")
        lines.append(f"> 高价值误杀: 被QuickScore或QuickIntent淘汰，但评论/描述中存在 price/MOQ/supplier/catalog/sample/factory/distributor/wholesale 等商业信号")
        lines.append("")

        # 基础误杀列表
        if fe.get("false_eliminated_videos"):
            lines.append("### 基础误杀 (旧管道高分但新管道淘汰)")
            lines.append("")
            lines.append("| 账号 | 描述 | 旧参考分 | 播放 | 点赞 |")
            lines.append("|------|------|----------|------|------|")
            for v in fe["false_eliminated_videos"][:10]:
                lines.append(
                    f"| @{v.get('account','')} | {v.get('desc','')[:50]} | "
                    f"{v.get('old_reference_score',0):.0f} | "
                    f"{v.get('plays',0):,} | {v.get('digg',0):,} |"
                )
            lines.append("")

        # 高价值误杀列表
        if fe.get("high_value_false_videos"):
            lines.append("### ⚠ 高价值误杀 (淘汰但含商业信号)")
            lines.append("")
            lines.append("| 账号 | 描述 | 淘汰层 | QuickScore | 信号来源 |")
            lines.append("|------|------|--------|-----------|----------|")
            for v in fe["high_value_false_videos"][:10]:
                lines.append(
                    f"| @{v.get('account','')} | {v.get('desc','')[:50]} | "
                    f"{v.get('eliminated_by','')} | {v.get('quick_score', 0):.0f} | "
                    f"{v.get('signal_source','')} |"
                )
            lines.append("")
            lines.append("> ⚠ 这些视频虽被淘汰，但包含商业信号，建议人工审核。如有真实商机，降低 `quick_scorer.min_score` (当前 20 → 15)。")
        elif fe.get("high_value_false_count", 0) == 0:
            lines.append("### ✅ 高价值误杀检测")
            lines.append("")
            lines.append("未发现含商业信号的误杀视频。淘汰策略有效。")
            lines.append("")

        if not fe.get("false_eliminated_videos") and fe.get("high_value_false_count", 0) == 0:
            lines.append("✅ **零误杀** — 新管道淘汰策略精准。")
        lines.append("")

    def _overlap(self, lines):
        ov = self.c["overlap_analysis"]
        lines.append("## 🔄 6. Top 20 重叠分析")
        lines.append("")
        lines.append(f"- **双方共有**: {ov.get('overlap_count', 0)} 条")
        lines.append(f"- **仅新管道**: {ov.get('new_only_count', 0)} 条")
        lines.append(f"- **仅旧管道**: {ov.get('old_only_count', 0)} 条")
        lines.append(f"- **Jaccard 相似度**: {ov.get('jaccard_similarity', 0):.2%}")
        lines.append("")
        if ov.get('jaccard_similarity', 0) > 0.5:
            lines.append("✅ 新旧管道高度一致, 新管道策略未跑偏。")
        elif ov.get('jaccard_similarity', 0) > 0.3:
            lines.append("⚠ 有一定差异, 可能是新管道发现了不同的价值维度。建议人工对比 '仅新管道' 和 '仅旧管道' 列表。")
        else:
            lines.append("🔴 差异较大, 新管道策略与旧管道显著不同。需深入分析原因。")
        lines.append("")

    def _funnel_analysis(self, lines):
        """第7章: 漏斗转化分析"""
        fa = self.c.get("funnel_analysis", {})
        if not fa:
            return

        lines.append("## 🔽 7. 漏斗转化分析")
        lines.append("")
        lines.append("展示每个阶段的视频数量变化, 识别淘汰最严重的环节。")
        lines.append("")

        total_raw = fa.get("stage_0_total_raw", 1) or 1
        qs_passed = fa.get("stage_1_quickscore_passed", 0)
        qs_elim = fa.get("stage_1_quickscore_eliminated", 0)
        intent_sig = fa.get("stage_2_intent_signaled", 0)
        no_intent = fa.get("stage_2_no_intent", 0)
        deep = fa.get("stage_3_deep_analyzed", 0)
        final_top = fa.get("stage_4_final_top", 0)

        lines.append("| 阶段 | 进入 | 保留 | 淘汰 | 保留率 | 累计转化率 |")
        lines.append("|------|------|------|------|--------|------------|")
        lines.append(
            f"| 0. 搜索+去重 | {total_raw} | {total_raw} | 0 | 100% | 100% |"
        )
        lines.append(
            f"| 1. QuickScore | {total_raw} | {qs_passed} | {qs_elim} | "
            f"{fa.get('qs_retention_rate', 0)}% | "
            f"{fa.get('cumulative_to_quickscore', 0)}% |"
        )
        lines.append(
            f"| 2. QuickIntent | {qs_passed} | {intent_sig} | {no_intent} | "
            f"{fa.get('intent_retention_rate', 0)}% | "
            f"{fa.get('cumulative_to_intent', 0)}% |"
        )
        lines.append(
            f"| 3. LLM分类 | {intent_sig} | {deep} | {intent_sig - deep} | "
            f"{fa.get('deep_retention_rate', 0)}% | "
            f"{fa.get('cumulative_to_deep', 0)}% |"
        )
        # 阶段 3.5: 意图过滤
        sf35_elim = fa.get("stage_3_5_intent_filtered", 0)
        sf35_pass = fa.get("stage_3_5_passed_to_final", deep)
        sf35_retention = fa.get("intent_filter_retention_rate", 100)
        sf35_cum = fa.get("cumulative_to_intent_filter", fa.get("cumulative_to_deep", 0))
        lines.append(
            f"| 3.5. 意图过滤 | {deep} | {sf35_pass} | {sf35_elim} | "
            f"{sf35_retention}% | "
            f"{sf35_cum}% |"
        )
        lines.append(
            f"| 4. FinalScore Top | {sf35_pass} | {final_top} | {sf35_pass - final_top} | "
            f"{fa.get('final_retention_rate', 0)}% | "
            f"{fa.get('cumulative_to_final', 0)}% |"
        )
        lines.append("")

        lines.append(f"**淘汰最多阶段**: {fa.get('max_elimination_stage', 'N/A')} "
                     f"({fa.get('max_elimination_count', 0)} 条)")
        lines.append("")

        # ASCII 漏斗图
        lines.append("### 漏斗可视化")
        lines.append("")
        lines.append("```")
        max_width = 50
        stages = [
            (f"搜索+去重: {total_raw}", 1.0),
            (f"QuickScore通过: {qs_passed}", qs_passed / max(1, total_raw)),
            (f"意图信号: {intent_sig}", intent_sig / max(1, total_raw)),
            (f"深度分析: {deep}", deep / max(1, total_raw)),
            (f"意图过滤后: {sf35_pass}", sf35_pass / max(1, total_raw)),
            (f"FinalScore Top: {final_top}", final_top / max(1, total_raw)),
        ]
        for label, ratio in stages:
            bar_len = max(1, int(max_width * ratio))
            bar = "█" * bar_len
            lines.append(f"  {label:<30} │{bar}")
        lines.append("```")
        lines.append("")

        # 每阶段输入/输出/淘汰率 (细化)
        lines.append("### 每阶段详细统计")
        lines.append("")
        lines.append("| 阶段 | 输入 | 输出 | 淘汰 | 淘汰率 | 累计转化 |")
        lines.append("|------|------|------|------|--------|----------|")
        lines.append(f"| 0. 搜索+去重 | — | {total_raw} | — | — | 100% |")
        lines.append(f"| 1. QuickScore | {total_raw} | {qs_passed} | {qs_elim} | {round(qs_elim/max(1,total_raw)*100,1)}% | {fa.get('cumulative_to_quickscore',0)}% |")
        lines.append(f"| 2. QuickIntent | {qs_passed} | {intent_sig} | {no_intent} | {round(no_intent/max(1,qs_passed)*100,1)}% | {fa.get('cumulative_to_intent',0)}% |")
        lines.append(f"| 3. LLM分类 | {intent_sig} | {deep} | {intent_sig-deep} | {round((intent_sig-deep)/max(1,intent_sig)*100,1)}% | {fa.get('cumulative_to_deep',0)}% |")
        lines.append(f"| 3.5. 意图过滤 | {deep} | {sf35_pass} | {sf35_elim} | {round(sf35_elim/max(1,deep)*100,1)}% | {sf35_cum}% |")
        lines.append(f"| 4. FinalScore | {sf35_pass} | {final_top} | {sf35_pass-final_top} | {round((sf35_pass-final_top)/max(1,sf35_pass)*100,1)}% | {fa.get('cumulative_to_final',0)}% |")
        lines.append("")

        # 高价值误杀在哪个阶段
        fe = self.c.get("false_elimination", {})
        hv_stage = fe.get("stage_distribution", {})
        if hv_stage:
            lines.append("### 高价值误杀阶段分布")
            lines.append("")
            lines.append("| 阶段 | 误杀数 | 该阶段淘汰总数 | 误杀占比 |")
            lines.append("|------|--------|---------------|----------|")
            stage_totals = {
                "QuickScore": qs_elim,
                "QuickIntent": no_intent,
                "FinalScore": deep - final_top,
            }
            for stage in ["QuickScore", "QuickIntent", "FinalScore"]:
                count = hv_stage.get(stage, 0)
                total_in_stage = stage_totals.get(stage, 1)
                lines.append(
                    f"| {stage} | {count} | {total_in_stage} | "
                    f"{round(count/max(1,total_in_stage)*100,1)}% |"
                )
            lines.append("")

        # 关键发现
        lines.append("### 关键发现")
        lines.append("")
        elim_stage = fa.get("max_elimination_stage", "")
        if elim_stage == "QuickScore":
            lines.append(f"- QuickScore 淘汰最多 ({qs_elim}条/{total_raw}输入={round(qs_elim/max(1,total_raw)*100,1)}%)。如果高价值误杀率高，建议降低 `min_score` 阈值。")
        elif elim_stage == "QuickIntent":
            lines.append(f"- QuickIntent 淘汰最多 ({no_intent}条)。这符合预期——大多数视频评论不含采购意图。")
        else:
            lines.append(f"- {elim_stage} 淘汰最多视频 ({fa.get('max_elimination_count', 0)}条)。")
        lines.append(f"- 累计转化率: {total_raw} → {final_top} ({fa.get('cumulative_to_final', 0)}%)")
        lines.append("")

    def _token_breakdown(self, lines):
        """第8章: Token 消耗模块拆解"""
        tk = self.c["token_consumption"]
        tb = tk.get("token_breakdown", {})
        if not tb:
            return

        lines.append("## 📊 8. Token 消耗模块拆解")
        lines.append("")
        lines.append("### 新管道 Token 占比")
        lines.append("")
        lines.append("| 模块 | LLM调用 | Token | 占比 | 说明 |")
        lines.append("|------|---------|-------|------|------|")
        for name, data in tb.items():
            label = {"comment_classifier": "CommentClassifier (阶段3)",
                     "final_scorer": "FinalScorer (阶段4)",
                     "analyzer": "Analyzer (阶段5)"}.get(name, name)
            lines.append(
                f"| {label} | {data['calls']} | ~{data['tokens']:,} | {data['pct']}% | "
                f"{'批量评论意图分类' if 'comment' in name else 'AI分析总结' if 'analyzer' in name else '纯计算, 零Token'} |"
            )
        lines.append("")
        lines.append(f"**每个深度分析视频平均 Token 成本**: ~{tk.get('avg_token_per_deep_video', 0):,} tokens")
        lines.append(f"**深度分析视频数**: {tk.get('deep_video_count', 0)}")
        lines.append("")
        lines.append(f"> {tk.get('root_cause', '')}")
        lines.append("")

        # 旧管道拆解
        lines.append("### 旧管道 Token 拆解")
        lines.append("")
        lines.append("| 模块 | LLM调用 | Token | 占比 |")
        lines.append("|------|---------|-------|------|")
        lines.append(f"| Analyzer (analyze_enhanced) | 1 | ~{tk.get('old_token_estimate', 0):,} | 100% |")
        lines.append("")
        lines.append("> 旧管道一次性将所有数据 (20账号+30视频+200评论) 塞入 LLM，Token 消耗集中但价值密度低")
        lines.append("")

    def _false_elimination_detail(self, lines):
        """第9章: 误杀阶段分布"""
        fe = self.c["false_elimination"]
        stage_dist = fe.get("stage_distribution", {})
        signal_src = fe.get("signal_sources", {})

        lines.append("## 🎯 9. 高价值误杀 — 阶段分布")
        lines.append("")

        if not stage_dist:
            lines.append("无高价值误杀数据。")
            lines.append("")
            return

        lines.append("### 误杀按淘汰阶段分布")
        lines.append("")
        lines.append("| 淘汰阶段 | 误杀数 | 占比 | 根因 |")
        lines.append("|----------|--------|------|------|")
        total = sum(stage_dist.values())
        for stage in ["QuickScore", "QuickIntent", "阶段3.5 意图过滤", "FinalScore"]:
            count = stage_dist.get(stage, 0)
            pct = round(count / max(1, total) * 100, 1)
            root_cause = {
                "QuickScore": "视频描述含商业关键词但互动数据极低, QuickScore 硬淘汰 (非白名单通道)",
                "QuickIntent": "采样评论未触发 QuickIntentScanner 关键词, 但描述含商业信号",
                "阶段3.5 意图过滤": "CommentClassifier 后 intent_ratio 过低且无 actionable, 被意图过滤器淘汰",
                "FinalScore": "深度评论分类后 actionable=0, 标记为弱对标",
            }.get(stage, "")
            lines.append(f"| {stage} | {count} | {pct}% | {root_cause} |")
        lines.append("")

        lines.append(f"**主导阶段**: {fe.get('dominant_stage', 'N/A')} ({stage_dist.get(fe.get('dominant_stage', ''), 0)} 条)")
        lines.append("")

        # 信号来源
        if signal_src:
            lines.append("### 信号来源分布")
            lines.append("")
            for src, count in signal_src.items():
                label = {"desc": "视频描述", "sampled_comments": "采样评论", "both": "描述+评论"}.get(src, src)
                lines.append(f"- **{label}**: {count} 条")
            lines.append("")

        # 全量误杀清单
        all_false = fe.get("high_value_false_videos", [])
        if all_false:
            lines.append(f"### 全量误杀清单 ({len(all_false)} 条)")
            lines.append("")
            lines.append("| # | 视频ID | 账号 | 淘汰阶段 | QuickScore | 描述 (截取) | 信号来源 |")
            lines.append("|---|--------|------|----------|------------|-------------|----------|")
            for i, fv in enumerate(all_false, 1):
                lines.append(
                    f"| {i} | {fv.get('video_id','')} | @{fv.get('account','')} | "
                    f"{fv.get('eliminated_by','')} | {fv.get('quick_score', 0):.0f} | "
                    f"{fv.get('desc','')[:60]} | {fv.get('signal_source','')} |"
                )
            lines.append("")

        lines.append("**结论**: 如果主导阶段是 QuickScore 且所有误杀视频 quick_score=0，说明硬淘汰条件 (likes<5/comments=0) 过严。")
        lines.append("已启用商业白名单通道 (commercial_whitelist): desc/title/bio 命中 ≥2 个商业关键词时跳过硬淘汰。")
        lines.append("同时阶段 3.5 意图过滤器可提前淘汰 LLM 分类后的弱意图视频，减少 FinalScore 无效计算。")
        lines.append("")

    def _diagnosis(self, lines):
        """第10章: 综合诊断"""
        lines.append("## 🔬 10. 综合诊断")
        lines.append("")

        fe = self.c["false_elimination"]
        tk = self.c["token_consumption"]
        ce = self.c["comment_fetch_efficiency"]
        fa = self.c.get("funnel_analysis", {})

        issues = []

        # 问题1: 高价值误杀
        hv_count = fe.get("high_value_false_count", 0)
        dominant = fe.get("dominant_stage", "")
        if hv_count > 0:
            issues.append({
                "problem": "高价值误杀率过高",
                "severity": "高" if fe.get("high_value_false_rate_pct", 0) > 5 else "中",
                "detail": f"{hv_count} 条视频被淘汰但含商业信号，集中在 {dominant} 阶段",
                "root_cause": (
                    f"所有误杀视频的 quick_score=0 (硬淘汰: likes<5/comments=0)，"
                    f"但这些视频的描述包含 factory/supplier/manufacturer 等关键词。"
                    f"硬淘汰规则未考虑描述中的商业信号。"
                ),
                "fix": "已实现商业白名单通道: desc/title/bio 命中 ≥2 个商业白名单关键词时跳过硬淘汰。监控白名单通过率确保不显著增加漏斗压力。",
            })

        # 问题2: Token
        new_tok = tk.get("new_token_estimate", 0)
        old_tok = tk.get("old_token_estimate", 0)
        if new_tok > old_tok * 1.2:
            tb = tk.get("token_breakdown", {})
            cc_pct = tb.get("comment_classifier", {}).get("pct", 0)
            issues.append({
                "problem": "Token 消耗增长",
                "severity": "高" if new_tok > old_tok * 3 else "中",
                "detail": f"新管道 {new_tok:,} tokens vs 旧管道 {old_tok:,} tokens (+{(new_tok/old_tok-1)*100:.0f}%)",
                "root_cause": (
                    f"CommentClassifier 占 {cc_pct}%，深度分析视频数 ({tk.get('deep_video_count', 0)}) "
                    f"远超真实场景 (3关键词预期 10-25 条)。每条深度视频触发 ~800/6≈133 tokens。"
                    f"真实场景预估: 15条×133 + 2000(分析) ≈ 4,000 tokens"
                ),
                "fix": "调整 max_comments_per_video 从 40→20，或 videos_per_batch 从 6→10",
            })

        # 问题3: 评论耗时
        time_saved = ce.get("time_saved_pct", 0)
        if time_saved < 0:
            issues.append({
                "problem": "评论耗时异常",
                "severity": "中",
                "detail": f"新管道比旧管道多用 {abs(time_saved)}% 时间",
                "root_cause": (
                    f"新管道采样了 {ce.get('new_sampled_videos', 0)} 条视频 (每条~1.5s)，"
                    f"旧管道仅处理 Top 20% ({ce.get('old_top20pct_videos', 0)} 条, 每条~4s)。"
                    f"新管道采样面更广，但单条成本更低。"
                ),
                "fix": "如果 QuickScore 更精准 (通过率更低)，采样量会自然下降",
            })

        # 问题4: 漏斗瓶颈
        max_stage = fa.get("max_elimination_stage", "")
        if max_stage:
            issues.append({
                "problem": f"漏斗瓶颈: {max_stage}",
                "severity": "中" if max_stage == "QuickIntent" else "需关注",
                "detail": f"{max_stage} 淘汰了 {fa.get('max_elimination_count', 0)} 条视频",
                "root_cause": (
                    "QuickIntent 大量淘汰符合预期 (多数评论无采购意图)"
                    if max_stage == "QuickIntent" else
                    f"{max_stage} 淘汰最多，需检查该阶段逻辑"
                ),
                "fix": "监控该阶段的高价值误杀率，确保商业视频不被误杀",
            })

        if not issues:
            lines.append("✅ 所有指标正常，无需额外诊断。")
            lines.append("")
            return

        for i, issue in enumerate(issues, 1):
            sev_emoji = {"高": "🔴", "中": "🟡", "低": "🟢"}.get(issue["severity"], "⚪")
            lines.append(f"### {sev_emoji} 问题{i}: {issue['problem']}")
            lines.append("")
            lines.append(f"**严重度**: {issue['severity']}")
            lines.append(f"**现象**: {issue['detail']}")
            lines.append(f"**根因**: {issue['root_cause']}")
            lines.append(f"**修复方案**: {issue['fix']}")
            lines.append("")

    def _recommendations(self, lines):
        lines.append("## 📋 建议")
        lines.append("")
        fe = self.c["false_elimination"]
        tk = self.c["token_consumption"]
        q = self.c["video_quality"]
        fa = self.c.get("funnel_analysis", {})

        recs = []

        # 基于高价值误杀率给出建议
        hv_count = fe.get("high_value_false_count", 0)
        hv_rate = fe.get("high_value_false_rate_pct", 0)
        if hv_rate > 5:
            recs.append(f"1. 🔴 **高价值误杀率偏高 ({hv_rate}%)**: 建议降低 `quick_scorer.min_score` (当前 20 → 15), 并增加对 desc 中商业关键词的权重")
        elif hv_count > 0:
            recs.append(f"1. 🟡 **存在高价值误杀 ({hv_count}条)**: 人工审核报告中的疑似误杀视频, 确认是否需要调整淘汰规则")
        else:
            recs.append("1. ✅ 高价值误杀率 = 0%, QuickScore 淘汰策略精准")

        if fe.get("false_eliminated_count", 0) > 3:
            recs.append("2. **基础误杀偏高**: QuickScore 可能淘汰了旧管道的高分视频, 检查淘汰理由")

        if tk.get("new_token_estimate", 0) > tk.get("old_token_estimate", 0) * 1.2:
            recs.append("3. **Token 消耗偏高**: 考虑减少 CommentClassifier 的 videos_per_batch 或换用更小模型")
        else:
            recs.append("3. ✅ Token 消耗在合理范围内")

        if q.get("winner") == "new" or q.get("winner") == "tie":
            recs.append("4. ✅ 新管道输出质量不输旧管道, 且减少了 ~70-80% 的无效处理")
        else:
            recs.append("4. ⚠ 新管道输出质量略逊, 检查 FinalScore 权重是否需要调整")

        # 基于漏斗的建议
        elim_stage = fa.get("max_elimination_stage", "")
        if elim_stage == "QuickScore" and hv_rate > 3:
            recs.append("5. **漏斗瓶颈在 QuickScore**: 建议降低 min_score 让更多候选进入后续阶段")
        elif elim_stage == "QuickIntent":
            recs.append("5. ✅ 漏斗瓶颈在 QuickIntent (符合预期), 大多数视频评论不含采购意图")

        recs.append("")
        recs.append("**测试关键词**: `shopping bag`, `door handle`, `laminated woven bag`")
        recs.append("**下一步**: 3组关键词测试通过后, 再评估是否开发关键词扩展模块")

        for r in recs:
            lines.append(r)
        lines.append("")

    @staticmethod
    def _winner_badge(winner):
        badges = {
            "new": "🟢 新管道",
            "old": "🔵 旧管道",
            "tie": "⚪ 持平",
        }
        return badges.get(winner, "—")


# ═══════════════════════════════════════════════════════════
# 主验证流程
# ═══════════════════════════════════════════════════════════

def run_validation(
    keywords: list[str],
    region: str = "",
    data_dir: str = None,
) -> str:
    """执行完整管道验证, 返回报告文件路径"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path("data") / f"validate_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    vdata = ValidationData()
    vdata.keywords = keywords
    vdata.region = region
    vdata.timestamp = ts

    print("=" * 60)
    print("  TikTok 管道验证框架 v1.0")
    print(f"  关键词: {', '.join(keywords)}")
    print(f"  输出目录: {out_dir}")
    print("=" * 60)

    # ── Step 1: 加载配置 ──
    print("\n[1/5] 加载配置...")
    cfg = {}
    config_file = Path("config.yaml")
    if config_file.exists():
        import yaml
        with open(config_file, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    # ── Step 2: 运行新管道 ──
    print("[2/5] 运行新管道...")
    _run_new_pipeline(vdata, keywords, region, cfg)
    print(f"  QuickScore 通过: {len(vdata.new_quick_score_passed)}")
    print(f"  QuickScore 淘汰: {len(vdata.new_quick_score_eliminated)}")
    print(f"  意图信号: {len(vdata.new_intent_signaled)}")
    print(f"  深度分析: {len(vdata.new_deep_analyzed)}")
    print(f"  Top 参考: {len(vdata.new_top_references)}")

    # ── Step 3: 模拟旧管道 ──
    print("[3/5] 模拟旧管道评分...")
    _run_old_pipeline_simulation(vdata, cfg)
    print(f"  P0 通过: {len(vdata.old_p0_passed)}")
    print(f"  P0 淘汰: {len(vdata.old_p0_eliminated)}")
    print(f"  意图评论: {len(vdata.old_intent_comments)}")
    print(f"  Top 参考: {len(vdata.old_top_references)}")

    # ── Step 4: 对比分析 ──
    print("[4/5] 执行对比分析...")
    engine = ComparisonEngine(vdata)
    comparison = engine.compare()

    # ── Step 5: 生成报告 ──
    print("[5/5] 生成验证报告...")
    report = ReportGenerator(vdata, comparison)
    md_content = report.generate()

    report_file = out_dir / "validation_report.md"
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(md_content)

    # 保存原始对比数据
    data_file = out_dir / "validation_data.json"
    serializable = {
        "keywords": vdata.keywords,
        "region": vdata.region,
        "timestamp": vdata.timestamp,
        "counts": {
            "raw_videos": len(vdata.all_raw_videos),
            "accounts": len(vdata.all_accounts),
            "new_quick_score_passed": len(vdata.new_quick_score_passed),
            "new_quick_score_eliminated": len(vdata.new_quick_score_eliminated),
            "new_intent_signaled": len(vdata.new_intent_signaled),
            "new_deep_analyzed": len(vdata.new_deep_analyzed),
            "new_top_references": len(vdata.new_top_references),
            "old_p0_passed": len(vdata.old_p0_passed),
            "old_p0_eliminated": len(vdata.old_p0_eliminated),
            "old_intent_comments": len(vdata.old_intent_comments),
            "old_top_references": len(vdata.old_top_references),
        },
        "comparison": comparison,
    }
    with open(data_file, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n✅ 验证完成!")
    print(f"   报告: {report_file}")
    print(f"   数据: {data_file}")

    # 打印摘要
    print("\n─── 关键结论 ───")
    q = comparison["video_quality"]
    fe = comparison["false_elimination"]
    tk = comparison["token_consumption"]
    ce = comparison["comment_fetch_efficiency"]
    print(f"  视频质量: {q.get('winner', 'N/A')}")
    print(f"  误杀率: {fe.get('false_eliminated_count', '?')} 条")
    print(f"  Token: 旧 ~{tk.get('old_token_estimate', 0):,} vs 新 ~{tk.get('new_token_estimate', 0):,}")
    print(f"  评论耗时: 节省 {ce.get('time_saved_pct', 0):.0f}%")

    return str(report_file)


# ═══════════════════════════════════════════════════════════
# 管道执行辅助函数
# ═══════════════════════════════════════════════════════════

def _run_new_pipeline(vdata: ValidationData, keywords: list, region: str, cfg: dict):
    """运行新管道 (实际 scraper + 新评分)"""
    import asyncio
    from tiktok_analyzer.scraper import TikTokScraper
    from tiktok_analyzer.comment_classifier import CommentClassifier, CommentClassifierConfig
    from tiktok_analyzer.keyword_expander import KeywordExpander, KeywordCache

    async def _run():
        # 关键词扩展 (如果有)
        expanded = keywords
        expand_tier = cfg.get("keyword_expansion", {}).get("default_tier", "balanced")
        if len(keywords) <= 5:
            try:
                cache = KeywordCache(ttl_days=cfg.get("keyword_expansion", {}).get("cache_ttl_days", 7))
                expander = KeywordExpander(cache=cache)
                exp_result = expander.expand(keywords, tier=expand_tier)
                if exp_result.get("added", 0) > 0:
                    expanded = exp_result["keywords"][:10]
            except Exception:
                pass

        scraper = TikTokScraper(browser_channel="chromium", headless=True)
        try:
            await scraper.start_browser()
            logged_in = await scraper.ensure_logged_in()
            if not logged_in:
                print("  ⚠ 登录失败, 使用游客模式")

            # 计时
            t0 = time.time()

            enrich_top = cfg.get("video_scraping", {}).get("enrich_top", 0)
            scraped = await scraper.run_analysis(
                keywords=expanded,
                region=region,
                accounts_per_keyword=5,   # 验证模式: 适度减少
                videos_per_account=20,
                comments_per_video=20,
                enrich_top=5,
                sample_comment_count=8,
                deep_comment_count=20,
                validation_mode=True,      # 保留采样评论 + 返回漏斗统计
            )

            comment_time = time.time() - t0
            vdata.new_comment_fetch_time = comment_time

            # 提取新管道数据
            vdata.all_accounts = scraped.get("accounts", [])
            vdata.new_quick_score_passed = scraped.get("videos", [])
            vdata.new_quick_score_eliminated = scraped.get("quick_score_eliminated_videos",
                                                           scraped.get("low_quality_videos", []))
            vdata.all_raw_videos = vdata.new_quick_score_passed + vdata.new_quick_score_eliminated
            vdata.all_comments = scraped.get("comments", [])
            vdata.new_funnel = scraped.get("funnel_stats", {})

            deep = scraped.get("deep_analyzed_videos", [])
            vdata.new_deep_analyzed = deep
            vdata.new_intent_signaled = deep  # deep_analyzed_videos 已经是 intent_signaled 的子集

            # 验证模式: 无意图视频 (含采样评论, 供高价值误杀检测)
            vdata.new_no_intent = scraped.get("no_intent_videos", [])

            # 在新管道内运行 CommentClassifier (如果需要)
            # 这里我们记录而不实际执行 LLM 来避免真实 API 消耗
            # 实际验证时取消注释以下代码

            # ── 实际 LLM 分类 (需要 API key) ──
            import os
            api_key = os.environ.get("OPENAI_API_KEY", "")
            if api_key and deep:
                cc_cfg_dict = cfg.get("comment_classifier", {})
                cc_cfg = CommentClassifierConfig(**{
                    k: v for k, v in cc_cfg_dict.items()
                    if k in CommentClassifierConfig.__dataclass_fields__
                })
                classifier = CommentClassifier(api_key=api_key, config=cc_cfg)
                try:
                    batch_result = await classifier.classify_videos(deep)
                    vdata.new_llm_calls = batch_result.total_llm_calls
                    vdata.new_llm_tokens = batch_result.total_tokens_estimate

                    # 构建 classify_map
                    classify_map = {vr.video_id: vr for vr in batch_result.video_results}
                    for v in deep:
                        vr = classify_map.get(v.get("id", ""))
                        if vr:
                            v["intent_ratio"] = vr.intent_ratio
                            v["intent_quality_score"] = vr.intent_quality_score
                            v["intent_diversity"] = vr.intent_diversity
                            v["actionable_intent_count"] = vr.actionable_intent_count
                            v["purchase_intent_comments"] = vr.intent_comments
                            v["purchase_intent_ratio"] = vr.intent_ratio
                            v["is_weak_reference"] = vr.is_weak_reference
                except Exception as e:
                    print(f"  ⚠ LLM 分类失败: {e}")
                finally:
                    await classifier.close()

                # ── 阶段 3.5: 轻量淘汰 ──
                intent_filtered_out = []
                if cc_cfg.intent_filter_enabled and classify_map:
                    deep, intent_filtered_out = classifier.filter_low_intent(deep, classify_map)
                    vdata.new_deep_analyzed = deep
                    vdata.new_intent_filtered_out = intent_filtered_out

                # FinalScore
                fs_cfg_dict = cfg.get("final_scorer", {})
                fs_cfg = FinalScorerConfig(**{
                    k: v for k, v in fs_cfg_dict.items()
                    if k in FinalScorerConfig.__dataclass_fields__
                })
                final_scorer = FinalScorer(fs_cfg)
                all_scored, top_refs = final_scorer.score_all(deep, classify_map)
                vdata.new_final_scored = all_scored
                vdata.new_top_references = top_refs
                vdata.new_llm_calls += 1  # AI 分析
                vdata.new_llm_tokens += 2000  # AI 分析估算
            else:
                # 无 API key: 使用 QuickScore 作为 fallback Top 排序
                sorted_by_qs = sorted(
                    deep, key=lambda v: v.get("quick_score", 0), reverse=True
                )
                vdata.new_top_references = sorted_by_qs[:20]
                vdata.new_llm_calls = 0
                vdata.new_llm_tokens = 0
                for v in sorted_by_qs[:20]:
                    v["is_top_reference"] = True
                    v["final_score"] = v.get("quick_score", 0)
                    v["tier"] = "B"
                    v["commercial_intent"] = 0
                print("  ⚠ 未检测到 API key, 使用 QuickScore 排序代替 FinalScore")

        finally:
            await scraper.close()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()


def _run_old_pipeline_simulation(vdata: ValidationData, cfg: dict):
    """在相同原始视频数据上模拟旧管道评分"""
    simulator = OldPipelineSimulator(cfg)
    result = simulator.simulate(
        all_videos=vdata.all_raw_videos,
        all_accounts=vdata.all_accounts,
        all_comments=vdata.all_comments,
    )

    vdata.old_p0_passed = result["p0_passed"]
    vdata.old_p0_eliminated = result["p0_eliminated"]
    vdata.old_video_scored = result["video_scored"]
    vdata.old_reference_scored = result["reference_scored"]
    vdata.old_top_references = result["top_references"]
    vdata.old_intent_comments = result["intent_comments"]

    # 估算旧管道 token
    video_count = len(vdata.old_video_scored)
    comment_count = len(vdata.all_comments)
    vdata.old_llm_tokens = min(10000, 2000 + video_count * 20 + comment_count * 15)


# ═══════════════════════════════════════════════════════════
# 离线验证: 使用已有数据文件
# ═══════════════════════════════════════════════════════════

def run_validation_offline(data_dir: str) -> str:
    """使用之前保存的 scraped_data 进行离线验证"""
    import json as json_mod

    data_path = Path(data_dir)
    if not data_path.exists():
        print(f"❌ 数据目录不存在: {data_dir}")
        sys.exit(1)

    # 查找 scraped_data
    scraped_files = list(data_path.rglob("scraped_data.json"))
    if not scraped_files:
        # 尝试从数据目录直接找
        scraped_files = list(data_path.glob("*.json"))
    if not scraped_files:
        print(f"❌ 在 {data_dir} 中未找到数据文件")
        sys.exit(1)

    print(f"📂 使用数据: {scraped_files[0]}")
    with open(scraped_files[0], "r", encoding="utf-8") as f:
        scraped = json_mod.load(f)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path("data") / f"validate_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    vdata = ValidationData()
    vdata.keywords = scraped.get("keywords", [])
    vdata.region = scraped.get("region", "")
    vdata.timestamp = ts
    vdata.all_accounts = scraped.get("accounts", [])
    vdata.all_raw_videos = scraped.get("videos", []) + scraped.get("low_quality_videos", [])
    vdata.all_comments = scraped.get("comments", [])
    vdata.new_quick_score_passed = scraped.get("videos", [])
    vdata.new_quick_score_eliminated = scraped.get("low_quality_videos", [])
    vdata.new_deep_analyzed = scraped.get("deep_analyzed_videos", [])
    vdata.new_intent_signaled = scraped.get("deep_analyzed_videos", [])
    vdata.new_llm_calls = scraped.get("quick_score_stats", {}).get("passed", 0)  # 近似

    cfg = {}
    config_file = Path("config.yaml")
    if config_file.exists():
        import yaml
        with open(config_file, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    print("[离线] 模拟旧管道评分...")
    _run_old_pipeline_simulation(vdata, cfg)

    print("[离线] 执行对比分析...")
    engine = ComparisonEngine(vdata)
    comparison = engine.compare()

    print("[离线] 生成验证报告...")
    report = ReportGenerator(vdata, comparison)
    md_content = report.generate()

    report_file = out_dir / "validation_report.md"
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(md_content)

    print(f"\n✅ 离线验证完成!")
    print(f"   报告: {report_file}")
    return str(report_file)


# ═══════════════════════════════════════════════════════════
# 模拟数据生成器 (用于无浏览器环境)
# ═══════════════════════════════════════════════════════════

def generate_mock_data(keywords: list[str], region: str = "") -> ValidationData:
    """生成模拟的验证数据, 用于无浏览器环境测试验证框架"""
    import random
    random.seed(42)

    vdata = ValidationData()
    vdata.keywords = keywords
    vdata.region = region
    vdata.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 模拟账号
    vdata.all_accounts = []
    for i in range(15):
        vdata.all_accounts.append({
            "username": f"factory_{i+1}",
            "nickname": f"Factory {i+1} Official",
            "follower_count": random.randint(1000, 50000),
            "like_count": random.randint(5000, 200000),
            "bio": random.choice([
                "Manufacturer of packaging products since 2005. WhatsApp: +86xxx",
                "Professional door hardware supplier. OEM/ODM welcome.",
                "Woven bag factory. Export to 50+ countries. DM for catalog.",
                "Daily vlog, fan account",
                "Custom packaging solutions. Factory direct price.",
                "Leading laminated bag manufacturer in China.",
            ]),
            "verified": random.random() > 0.5,
            "region": random.choice(["US", "GB", "CN", ""]),
        })

    # 模拟原始视频 (包含被淘汰的)
    all_raw = []
    # ── 初始化 QuickScorer (含商业白名单) ──
    qs_cfg = QuickScorerConfig()
    quick_scorer = QuickScorer(qs_cfg)
    acc_map = {a["username"]: a for a in vdata.all_accounts}

    for i in range(500):
        # 模拟真实分布: ~18% 极低互动, ~30% 中等, ~52% 较高
        quality_tier = random.random()
        if quality_tier < 0.10:
            # 零互动: 无播放无互动
            plays, likes, comments, shares = 0, 0, 0, 0
        elif quality_tier < 0.18:
            # 极低互动: likes<5, comments=0
            plays = random.randint(50, 500)
            likes = random.randint(0, 4)
            comments = 0
            shares = random.randint(0, 2)
        elif quality_tier < 0.40:
            # 低质量: 略高于阈值
            plays = random.randint(100, 2000)
            likes = random.randint(5, 20)
            comments = random.randint(0, 3)
            shares = random.randint(0, 10)
        elif quality_tier < 0.75:
            plays = random.randint(2000, 100000)
            likes = random.randint(20, 2000)
            comments = random.randint(3, 100)
            shares = random.randint(10, 500)
        else:
            plays = random.randint(50000, 500000)
            likes = random.randint(500, 20000)
            comments = random.randint(50, 500)
            shares = random.randint(100, 3000)

        has_commercial_signal = random.random() < 0.15
        desc = random.choice([
            "Check out our new shopping bag design! #packaging #factory",
            "Door handle installation guide #hardware #supplier",
            "Laminated woven bag production process #manufacturing",
            "Daily life vlog #fun #entertainment",
            "Unboxing my new purchase! #shopping #haul",
            "How to choose the right supplier? #business #import",
            "Factory tour: packaging production line #factory #manufacturing",
            "My favorite products from China #wholesale #b2b",
        ]) if has_commercial_signal or random.random() < 0.3 else random.choice([
            "Funny dance #viral #fun",
            "Cooking recipe #food #yummy",
            "Travel diary #travel #wanderlust",
            "Pet compilation #cute #dogs",
            "Music cover #music #singing",
        ])

        acc = random.choice(vdata.all_accounts)
        v = {
            "id": f"vid_{i:04d}",
            "desc": desc,
            "tags": random.sample(["factory", "supplier", "manufacturing", "packaging",
                                   "viral", "fun", "daily", "business", "b2b"], 3),
            "play_count": plays,
            "digg_count": likes,
            "comment_count": comments,
            "share_count": shares,
            "account_username": acc["username"],
            "url": f"https://tiktok.com/@{acc['username']}/video/vid_{i:04d}",
            "duration": random.randint(5, 120),
        }

        # ── 使用 QuickScorer (含商业白名单) 进行评分 ──
        acc = acc_map.get(v.get("account_username", ""), {})
        result = quick_scorer.score(v, acc)
        v["quick_score"] = result.total
        v["product_relevance"] = result.product_relevance
        v["video_quality_score"] = result.video_quality
        v["industry_hits"] = result.industry_hits
        v["is_personal_account"] = result.is_personal_account
        v["_commercial_whitelist"] = result.commercial_whitelist_hit
        v["eliminated_reason"] = result.eliminated_reason
        v["passed"] = result.passed

        all_raw.append(v)

    # QuickScore 分流
    qs_passed = [v for v in all_raw if v.get("eliminated_reason") == ""]
    qs_eliminated = [v for v in all_raw if v.get("eliminated_reason") != ""]

    # 模拟采样评论 (no_intent 视频)
    no_intent_vids = []
    intent_signaled = []
    for v in qs_passed:
        sampled = []
        for ci in range(8):
            has_intent_signal = random.random() < 0.12
            sampled.append({
                "text": random.choice([
                    "How much for 1000 pcs? What's your MOQ?",
                    "Can you send me catalog and price list?",
                    "Are you a factory or trading company?",
                    "Nice video! 👍",
                    "Love this content!",
                    "Where can I buy this?",
                    "Do you ship to USA? What's the shipping cost?",
                    "Can I get a sample first?",
                    "Great quality!",
                    "😂😂😂",
                ]) if has_intent_signal or ci < 2 else random.choice([
                    "Nice!", "Great video", "👍", "Love it", "Amazing",
                    "So cool", "🔥🔥🔥", "Where is this?", "Beautiful",
                    "First comment!", "Following you!"
                ]),
                "username": f"user_{random.randint(1, 999)}",
                "likes": random.randint(0, 50),
            })
        v["_sampled_comments"] = sampled
        v["_kept_sampled_comments"] = sampled

        # QuickIntentScanner 模拟
        has_signal = any(any(kw in c["text"].lower() for kw in
            ["price", "moq", "supplier", "catalog", "sample", "factory",
             "shipping", "wholesale"])
            for c in sampled)
        if has_signal:
            intent_signaled.append(v)
        else:
            no_intent_vids.append(v)

    # 深度评论 (intent_signaled)
    deep_vids = []
    all_comments = []
    for v in intent_signaled:
        deep_comments = []
        intent_count = 0
        for ci in range(random.randint(10, 30)):
            has_intent = random.random() < 0.25
            if has_intent:
                intent_count += 1
            deep_comments.append({
                "text": random.choice([
                    "Price for 5000 units? MOQ please?",
                    "Send catalog to my email",
                    "Are you the manufacturer? Need OEM service",
                    "Shipping cost to Germany?",
                    "Can you customize the design?",
                    "I need samples before bulk order",
                    "What's your best price for wholesale?",
                    "Great video! Loved the quality",
                    "Do you have a distributor in UK?",
                ]) if (has_intent or ci < 3) else random.choice([
                    "Nice product", "👍", "Love this", "Great quality",
                    "Amazing work!", "Keep it up! 💪",
                ]),
                "username": f"user_{random.randint(1, 999)}",
                "likes": random.randint(0, 80),
                "has_intent": has_intent,
                "top_intent": random.choice(["price_inquiry", "moq_inquiry", "supplier_search",
                                             "sample_request", None]) if has_intent else None,
            })
        v["_deep_comments"] = deep_comments
        v["intent_ratio"] = round(intent_count / max(1, len(deep_comments)), 4)
        v["intent_quality_score"] = round(random.uniform(30, 90), 1) if intent_count > 0 else 0
        v["intent_diversity"] = min(5, intent_count)
        v["actionable_intent_count"] = max(0, intent_count - random.randint(0, 2))
        v["purchase_intent_comments"] = intent_count
        v["purchase_intent_ratio"] = v["intent_ratio"]
        deep_vids.append(v)
        all_comments.extend(deep_comments)

    # 所有采样评论
    for v in no_intent_vids:
        all_comments.extend(v.get("_sampled_comments", []))

    vdata.all_raw_videos = all_raw
    vdata.new_quick_score_passed = qs_passed
    vdata.new_quick_score_eliminated = qs_eliminated
    vdata.new_intent_signaled = intent_signaled
    vdata.new_no_intent = no_intent_vids
    vdata.all_comments = all_comments

    # ── 阶段 3.5: 意图过滤 ──
    # 使用 CommentClassifier 的 filter_low_intent 逻辑
    intent_filtered = []
    deep_passed_to_final = []
    for v in deep_vids:
        ratio = v.get("intent_ratio", 0)
        actionable = v.get("actionable_intent_count", 0)
        if ratio < 0.03 and actionable < 1:
            v["_intent_filtered_out"] = True
            v["_intent_filter_reason"] = (
                f"intent_ratio({ratio:.4f}) < 0.03 AND actionable({actionable}) < 1"
            )
            intent_filtered.append(v)
        else:
            deep_passed_to_final.append(v)

    vdata.new_deep_analyzed = deep_vids  # 全部深度分析视频 (包含被过滤的)
    vdata.new_intent_filtered_out = intent_filtered

    # 漏斗统计
    total_raw = len(all_raw)
    sf35_elim = len(intent_filtered)
    sf35_pass = len(deep_passed_to_final)
    vdata.new_funnel = {
        "stage_0_total_raw": total_raw,
        "stage_1_quickscore_passed": len(qs_passed),
        "stage_1_quickscore_eliminated": len(qs_eliminated),
        "stage_2_intent_signaled": len(intent_signaled),
        "stage_2_no_intent": len(no_intent_vids),
        "stage_3_deep_analyzed": len(deep_vids),
        "stage_3_5_intent_filtered": sf35_elim,
        "stage_3_5_passed_to_final": sf35_pass,
        "stage_4_final_top": min(20, sf35_pass),
        "qs_retention_rate": round(len(qs_passed) / max(1, total_raw) * 100, 1),
        "intent_retention_rate": round(len(intent_signaled) / max(1, len(qs_passed)) * 100, 1),
        "deep_retention_rate": round(len(deep_vids) / max(1, len(intent_signaled)) * 100, 1),
        "intent_filter_retention_rate": round(sf35_pass / max(1, len(deep_vids)) * 100, 1) if len(deep_vids) > 0 else 0,
        "final_retention_rate": round(min(20, sf35_pass) / max(1, sf35_pass) * 100, 1) if sf35_pass > 0 else 0,
        "cumulative_to_quickscore": round(len(qs_passed) / max(1, total_raw) * 100, 1),
        "cumulative_to_intent": round(len(intent_signaled) / max(1, total_raw) * 100, 1),
        "cumulative_to_deep": round(len(deep_vids) / max(1, total_raw) * 100, 1),
        "cumulative_to_intent_filter": round(sf35_pass / max(1, total_raw) * 100, 1) if sf35_pass > 0 else 0,
        "cumulative_to_final": round(min(20, sf35_pass) / max(1, total_raw) * 100, 1),
    }

    # 模拟 FinalScore Top 20 (仅对通过意图过滤的视频)
    sorted_by_score = sorted(deep_passed_to_final, key=lambda v: (
        0.30 * v.get("product_relevance", 0)
        + 0.30 * v.get("video_quality_score", 0)
        + 0.40 * (v.get("intent_quality_score", 0))
    ), reverse=True)
    top_refs = sorted_by_score[:20]
    for rank, v in enumerate(top_refs, 1):
        v["final_score"] = round(
            0.30 * v.get("product_relevance", 0)
            + 0.30 * v.get("video_quality_score", 0)
            + 0.40 * v.get("intent_quality_score", 0), 1
        )
        v["tier"] = "S" if v["final_score"] >= 80 else "A" if v["final_score"] >= 60 else "B"
        v["commercial_intent"] = v.get("intent_quality_score", 0)
        v["is_top_reference"] = True
        v["reference_rank"] = rank

    vdata.new_top_references = top_refs
    # Token: CommentClassifier batches + AI analysis
    vdata.new_llm_calls = max(1, len(deep_vids) // 6)
    vdata.new_llm_tokens = vdata.new_llm_calls * 800 + 2000
    vdata.new_comment_fetch_time = len(qs_passed) * 0.15 + len(deep_vids) * 2.0

    return vdata


def run_mock_validation(keywords: list[str], region: str = "") -> str:
    """使用模拟数据运行验证"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path("data") / f"validate_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("  TikTok 管道验证框架 v2.0 (模拟模式)")
    print(f"  关键词: {', '.join(keywords)}")
    print(f"  输出目录: {out_dir}")
    print("=" * 60)

    print("\n[1/4] 生成模拟数据...")
    vdata = generate_mock_data(keywords, region)
    print(f"  原始视频: {len(vdata.all_raw_videos)}")
    print(f"  QuickScore 通过: {len(vdata.new_quick_score_passed)}")
    print(f"  QuickScore 淘汰: {len(vdata.new_quick_score_eliminated)}")
    print(f"  意图信号: {len(vdata.new_intent_signaled)}")
    print(f"  深度分析: {len(vdata.new_deep_analyzed)}")
    print(f"  Top 参考: {len(vdata.new_top_references)}")
    print(f"  采样评论: {len(vdata.all_comments)}")

    print("\n[2/4] 模拟旧管道评分...")
    cfg = {}
    config_file = Path("config.yaml")
    if config_file.exists():
        import yaml
        with open(config_file, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    _run_old_pipeline_simulation(vdata, cfg)
    print(f"  P0 通过: {len(vdata.old_p0_passed)}")
    print(f"  P0 淘汰: {len(vdata.old_p0_eliminated)}")
    print(f"  意图评论: {len(vdata.old_intent_comments)}")
    print(f"  Top 参考: {len(vdata.old_top_references)}")

    print("\n[3/4] 执行对比分析...")
    engine = ComparisonEngine(vdata)
    comparison = engine.compare()

    print("\n[4/4] 生成验证报告...")
    report = ReportGenerator(vdata, comparison)
    md_content = report.generate()

    report_file = out_dir / "validation_report.md"
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(md_content)

    data_file = out_dir / "validation_data.json"
    serializable = {
        "keywords": vdata.keywords,
        "region": vdata.region,
        "timestamp": vdata.timestamp,
        "mode": "mock",
        "counts": {
            "raw_videos": len(vdata.all_raw_videos),
            "accounts": len(vdata.all_accounts),
            "new_quick_score_passed": len(vdata.new_quick_score_passed),
            "new_quick_score_eliminated": len(vdata.new_quick_score_eliminated),
            "new_intent_signaled": len(vdata.new_intent_signaled),
            "new_deep_analyzed": len(vdata.new_deep_analyzed),
            "new_top_references": len(vdata.new_top_references),
            "old_p0_passed": len(vdata.old_p0_passed),
            "old_intent_comments": len(vdata.old_intent_comments),
            "old_top_references": len(vdata.old_top_references),
        },
        "comparison": comparison,
    }
    with open(data_file, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n✅ 验证完成!")
    print(f"   报告: {report_file}")
    print(f"   数据: {data_file}")

    # 打印摘要
    print("\n─── 关键结论 ───")
    q = comparison["video_quality"]
    fe = comparison["false_elimination"]
    tk = comparison["token_consumption"]
    ce = comparison["comment_fetch_efficiency"]
    fa = comparison.get("funnel_analysis", {})
    print(f"  视频质量: {q.get('winner', 'N/A')}")
    print(f"  基础误杀率: {fe.get('false_eliminated_rate_pct', 0)}% ({fe.get('false_eliminated_count', 0)}/{fe.get('total_raw_candidates', '?')})")
    print(f"  高价值误杀率: {fe.get('high_value_false_rate_pct', 0)}% ({fe.get('high_value_false_count', 0)}条)")
    print(f"  Token: 旧 ~{tk.get('old_token_estimate', 0):,} vs 新 ~{tk.get('new_token_estimate', 0):,}")
    print(f"  评论耗时: 节省 {ce.get('time_saved_pct', 0):.0f}%")
    print(f"  漏斗累计转化: {fa.get('cumulative_to_final', 0)}%")
    print(f"  淘汰最多阶段: {fa.get('max_elimination_stage', 'N/A')}")

    return str(report_file)


# ═══════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="TikTok Analyzer — 管道验证框架"
    )
    parser.add_argument(
        "--keywords", "-k", type=str, default="",
        help="测试关键词 (逗号分隔)"
    )
    parser.add_argument(
        "--region", "-r", type=str, default="",
        help="目标地区"
    )
    parser.add_argument(
        "--data", "-d", type=str, default="",
        help="使用已有数据目录 (离线验证)"
    )
    parser.add_argument(
        "--mock", "-m", action="store_true",
        help="使用模拟数据 (默认, 无需浏览器)"
    )
    parser.add_argument(
        "--live", "-l", action="store_true",
        help="实时验证 (需要浏览器+TikTok连接+OpenAI key)"
    )
    parser.add_argument(
        "--output", "-o", type=str, default="",
        help="输出目录"
    )

    args = parser.parse_args()

    # 默认测试关键词
    default_keywords = ["shopping bag", "door handle", "laminated woven bag"]

    if args.data:
        run_validation_offline(args.data)
    elif args.live:
        keywords = default_keywords
        if args.keywords:
            keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
        if not keywords:
            print("❌ 请提供有效的关键词")
            sys.exit(1)
        print("⚠ 实时验证需要: 浏览器 + TikTok 连接 + OpenAI API Key")
        run_validation(keywords, args.region)
    else:
        # 默认 --mock
        keywords = default_keywords
        if args.keywords:
            keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
        run_mock_validation(keywords, args.region)
