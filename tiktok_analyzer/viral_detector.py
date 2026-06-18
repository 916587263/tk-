"""
TikTok 竞争对手分析系统 - 爆款视频发现引擎
识别高互动率视频、提取爆款共性特征、输出结构化分析数据。

功能:
  1. 全局爆款 Top N 识别
  2. 每账号按互动率排序 + Top N
  3. 平均点赞/评论/分享计算
  4. 最高互动率视频定位
  5. 爆款共性特征提取
  6. 全局基准 vs 账号对比
"""
import math
from typing import Optional
from dataclasses import dataclass, field
from collections import Counter

from .logger import setup_logger

logger = setup_logger("viral_detector")


# ═══════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════

@dataclass
class ViralDetectorConfig:
    """爆款视频发现配置"""

    enabled: bool = True

    # ── 爆款判定阈值 ──
    viral_score_threshold: float = 60.0       # VideoScorer 评分 >= 此值
    viral_virality_threshold: float = 3.0     # 病毒系数 >= 此值
    viral_engagement_threshold: float = 0.05  # 互动率 >= 5%
    min_plays_for_viral: int = 10000          # 最低播放量（排除噪声）

    # ── Top N ──
    global_top_n: int = 10                    # 全局爆款数
    per_account_top_n: int = 5                # 每账号内 Top N

    # ── 特征提取 ──
    tag_frequency_top_n: int = 20             # 高频标签取前 N
    engagement_benchmark_percentiles: tuple = (25, 50, 75)


# ═══════════════════════════════════════════════════════════
# 引擎
# ═══════════════════════════════════════════════════════════

class ViralDetector:
    """爆款视频发现引擎

    用法:
        cfg = ViralDetectorConfig()
        detector = ViralDetector(cfg)
        result = detector.analyze(all_videos, all_accounts)
        # result["top_viral_videos"]  → 全局 Top 10
        # result["per_account"]       → 每账号爆款分析
        # result["viral_characteristics"] → 爆款共性
        # result["global_benchmarks"] → 全局基准
    """

    def __init__(self, config: ViralDetectorConfig):
        self.config = config

    # ───────────────────── 主入口 ─────────────────────

    def analyze(self, videos: list[dict], accounts: list[dict]) -> dict:
        """分析所有视频，返回完整爆款发现结果。

        副作用：原地修改 video dict（增加 engagement_rank 等字段）
        """
        if not videos:
            logger.warning("无视频数据，跳过爆款分析")
            return self._empty_result()

        cfg = self.config

        # 1. 给每条视频计算互动率并排名
        videos = self._enrich_videos(videos)

        # 2. 全局爆款 Top N
        top_viral = self._find_global_top_viral(videos)

        # 3. 每账号分析
        per_account = self._per_account_analysis(videos, accounts)

        # 4. 全局基准
        benchmarks = self._compute_benchmarks(videos)

        # 5. 爆款共性特征
        characteristics = self._extract_viral_characteristics(top_viral, videos)

        # 6. 账号 vs 基准对比
        per_account = self._benchmark_accounts(per_account, benchmarks)

        logger.info(
            "爆款分析完成: Top%d 爆款视频, %d 个账号, 全局平均互动率=%.2f%%",
            len(top_viral), len(per_account),
            benchmarks.get("avg_engagement_rate", 0) * 100
        )

        return {
            "top_viral_videos": top_viral,
            "per_account": per_account,
            "global_benchmarks": benchmarks,
            "viral_characteristics": characteristics,
        }

    # ───────────────────── Step 1: 视频增强 ─────────────────────

    def _enrich_videos(self, videos: list[dict]) -> list[dict]:
        """为每条视频补充 engagement_rate 和排序标记"""
        # 按账号分组
        by_account: dict[str, list[dict]] = {}
        for v in videos:
            uname = v.get("account_username", "__unknown__")
            by_account.setdefault(uname, []).append(v)

        all_engagement_rates = []

        for uname, acc_videos in by_account.items():
            # 计算互动率并排序
            for v in acc_videos:
                plays = v.get("play_count", 0) or 1
                digg = v.get("digg_count", 0) or 0
                comments = v.get("comment_count", 0) or 0
                shares = v.get("share_count", 0) or 0
                er = round((digg + comments + shares) / plays, 4)
                v["_engagement_rate"] = er
                all_engagement_rates.append(er)

            # 按互动率降序排列，标记排名
            acc_videos.sort(key=lambda x: x.get("_engagement_rate", 0), reverse=True)
            for rank, v in enumerate(acc_videos, 1):
                v["engagement_rank"] = rank
                v["is_account_top5"] = rank <= self.config.per_account_top_n

        # 全局互动率百分位
        if all_engagement_rates:
            sorted_ers = sorted(all_engagement_rates)
            n = len(sorted_ers)
            for v in videos:
                er = v.get("_engagement_rate", 0)
                # 计算百分位: 有多少比例的视频互动率 <= 当前值
                count_lte = sum(1 for x in sorted_ers if x <= er)
                v["engagement_percentile"] = round(count_lte / n * 100, 1)

        return videos

    # ───────────────────── Step 2: 全局爆款 Top N ─────────────────────

    def _find_global_top_viral(self, videos: list[dict]) -> list[dict]:
        """识别全局爆款视频"""
        cfg = self.config

        candidates = []
        for v in videos:
            er = v.get("_engagement_rate", 0)
            score = v.get("score", 0) or 0
            virality = v.get("virality", 0) or 0
            plays = v.get("play_count", 0) or 0

            # 判定: 至少满足评分/病毒系数/互动率/播放量之一
            is_viral = (
                score >= cfg.viral_score_threshold
                and virality >= cfg.viral_virality_threshold
                and er >= cfg.viral_engagement_threshold
                and plays >= cfg.min_plays_for_viral
            )
            v["is_viral"] = is_viral

            if is_viral:
                candidates.append(v)

        # 按 score 降序
        candidates.sort(key=lambda x: x.get("score", 0), reverse=True)

        top_n = candidates[:cfg.global_top_n]
        for i, v in enumerate(top_n):
            v["is_global_top10"] = True
            v["global_rank"] = i + 1

        # 其余标记为 False
        for v in candidates[cfg.global_top_n:]:
            v["is_global_top10"] = False

        logger.info(
            "全局爆款: %d 条候选 → Top %d | 评分范围 %.1f-%.1f",
            len(candidates), len(top_n),
            top_n[0].get("score", 0) if top_n else 0,
            top_n[-1].get("score", 0) if top_n else 0,
        )

        return top_n

    # ───────────────────── Step 3: 每账号分析 ─────────────────────

    def _per_account_analysis(self, videos: list[dict],
                               accounts: list[dict]) -> dict[str, dict]:
        """按账号分组分析"""
        cfg = self.config

        # 建立 username → account 映射
        account_map = {a.get("username", ""): a for a in accounts}

        by_account: dict[str, list[dict]] = {}
        for v in videos:
            uname = v.get("account_username", "__unknown__")
            by_account.setdefault(uname, []).append(v)

        result = {}
        for uname, acc_videos in by_account.items():
            n = len(acc_videos)
            if n == 0:
                continue

            # 按互动率排序（已是排好的）
            sorted_videos = sorted(
                acc_videos,
                key=lambda x: x.get("_engagement_rate", 0),
                reverse=True
            )

            # ── 基础统计 ──
            diggs = [v.get("digg_count", 0) or 0 for v in acc_videos]
            comments_list = [v.get("comment_count", 0) or 0 for v in acc_videos]
            shares = [v.get("share_count", 0) or 0 for v in acc_videos]
            plays = [v.get("play_count", 1) or 1 for v in acc_videos]
            scores = [v.get("score", 0) or 0 for v in acc_videos]
            ers = [v.get("_engagement_rate", 0) for v in acc_videos]

            avg_likes = round(sum(diggs) / n, 1)
            avg_comments = round(sum(comments_list) / n, 1)
            avg_shares = round(sum(shares) / n, 1)
            avg_plays = round(sum(plays) / n, 1)
            avg_score = round(sum(scores) / n, 1)
            avg_er = round(sum(ers) / n, 4)

            # ── 爆款占比 ──
            viral_count = sum(1 for v in acc_videos if v.get("is_viral"))
            viral_ratio = round(viral_count / n, 3) if n > 0 else 0.0

            # ── 最高互动率视频 ──
            highest = sorted_videos[0] if sorted_videos else {}
            highest_er_video = {
                "id": highest.get("id", ""),
                "desc": (highest.get("desc") or "")[:100],
                "engagement_rate": highest.get("_engagement_rate", 0),
                "play_count": highest.get("play_count", 0),
                "digg_count": highest.get("digg_count", 0),
                "comment_count": highest.get("comment_count", 0),
                "share_count": highest.get("share_count", 0),
                "url": highest.get("url", ""),
                "score": highest.get("score", 0),
                "tier": highest.get("tier", ""),
            }

            # ── Top N 视频摘要 ──
            top_videos = []
            for v in sorted_videos[:cfg.per_account_top_n]:
                top_videos.append({
                    "id": v.get("id", ""),
                    "desc": (v.get("desc") or "")[:80],
                    "engagement_rate": v.get("_engagement_rate", 0),
                    "play_count": v.get("play_count", 0) or 0,
                    "digg_count": v.get("digg_count", 0) or 0,
                    "comment_count": v.get("comment_count", 0) or 0,
                    "share_count": v.get("share_count", 0) or 0,
                    "url": v.get("url", ""),
                    "score": v.get("score", 0),
                    "tier": v.get("tier", ""),
                    "virality": v.get("virality", 0),
                    "engagement_rank": v.get("engagement_rank", 0),
                })

            # ── 互动率分布 ──
            sorted_ers = sorted(ers)
            percentiles = {}
            for p in self.config.engagement_benchmark_percentiles:
                idx = int(len(sorted_ers) * p / 100)
                idx = min(idx, len(sorted_ers) - 1)
                percentiles[f"p{p}"] = round(sorted_ers[idx], 4) if sorted_ers else 0.0

            # ── 等级分布 ──
            tier_dist = Counter(v.get("tier", "?") for v in acc_videos)

            # ── 账号信息补充 ──
            acc_info = account_map.get(uname, {})
            result[uname] = {
                "account_username": uname,
                "nickname": acc_info.get("nickname", ""),
                "follower_count": acc_info.get("follower_count", 0),
                "total_videos": n,
                "viral_count": viral_count,
                "viral_ratio": viral_ratio,
                "top_videos": top_videos,
                "highest_engagement_video": highest_er_video,
                "avg_likes": avg_likes,
                "avg_comments": avg_comments,
                "avg_shares": avg_shares,
                "avg_plays": avg_plays,
                "avg_score": avg_score,
                "avg_engagement_rate": avg_er,
                "engagement_percentiles": percentiles,
                "tier_distribution": dict(tier_dist),
            }

        return result

    # ───────────────────── Step 4: 全局基准 ─────────────────────

    def _compute_benchmarks(self, videos: list[dict]) -> dict:
        """计算全局基准值"""
        if not videos:
            return {}

        n = len(videos)
        diggs = [v.get("digg_count", 0) or 0 for v in videos]
        comments_list = [v.get("comment_count", 0) or 0 for v in videos]
        shares = [v.get("share_count", 0) or 0 for v in videos]
        plays = [v.get("play_count", 1) or 1 for v in videos]
        ers = [v.get("_engagement_rate", 0) for v in videos]
        scores = [v.get("score", 0) or 0 for v in videos]

        sorted_ers = sorted(ers)
        percentiles = {}
        for p in self.config.engagement_benchmark_percentiles:
            idx = int(n * p / 100)
            idx = min(idx, n - 1)
            percentiles[f"p{p}"] = round(sorted_ers[idx], 4)

        viral_count = sum(1 for v in videos if v.get("is_viral"))

        return {
            "total_videos": n,
            "viral_count": viral_count,
            "viral_ratio": round(viral_count / n, 3),
            "avg_likes": round(sum(diggs) / n, 1),
            "avg_comments": round(sum(comments_list) / n, 1),
            "avg_shares": round(sum(shares) / n, 1),
            "avg_plays": round(sum(plays) / n, 1),
            "avg_score": round(sum(scores) / n, 1),
            "avg_engagement_rate": round(sum(ers) / n, 4),
            "median_engagement_rate": percentiles.get("p50", 0),
            "engagement_percentiles": percentiles,
            "tier_distribution": dict(Counter(v.get("tier", "?") for v in videos)),
        }

    # ───────────────────── Step 5: 爆款共性特征 ─────────────────────

    def _extract_viral_characteristics(self, top_viral: list[dict],
                                        all_videos: list[dict]) -> dict:
        """从爆款视频中提取共性特征"""
        if not top_viral:
            return {"common_tags": [], "optimal_duration_range": [], "avg_duration": 0,
                    "description_patterns": {}, "note": "无足够爆款视频可供分析"}

        n = len(top_viral)

        # ── 高频标签 ──
        tag_counter = Counter()
        for v in top_viral:
            for tag in (v.get("tags") or []):
                tag_counter[tag.lower()] += 1
        common_tags = tag_counter.most_common(self.config.tag_frequency_top_n)

        # ── 最优时长 ──
        durations = sorted([v.get("duration", 0) or 0 for v in top_viral if v.get("duration", 0)])
        if durations:
            avg_duration = round(sum(durations) / len(durations), 1)
            # IQR 法取最优区间
            n_dur = len(durations)
            q1 = durations[int(n_dur * 0.25)] if n_dur >= 4 else durations[0]
            q3 = durations[int(n_dur * 0.75)] if n_dur >= 4 else durations[-1]
            optimal_range = [q1, q3]
        else:
            avg_duration = 0
            optimal_range = []

        # ── 描述特征 ──
        desc_lengths = [len((v.get("desc") or "").strip()) for v in top_viral]
        emoji_count = sum(
            1 for v in top_viral
            for c in (v.get("desc") or "")
            if ord(c) > 0x1F000 or (0x2600 <= ord(c) <= 0x27BF)
        )
        has_emoji_ratio = round(
            sum(1 for v in top_viral
                if any(ord(c) > 0x1F000 or (0x2600 <= ord(c) <= 0x27BF)
                       for c in (v.get("desc") or ""))) / n, 2
        )
        has_cta_ratio = round(
            sum(1 for v in top_viral
                if any(kw in (v.get("desc") or "").lower()
                       for kw in ["follow", "like", "comment", "share", "check",
                                  "link in bio", "关注", "点赞", "评论", "分享"])) / n, 2
        )
        has_question_ratio = round(
            sum(1 for v in top_viral if "?" in (v.get("desc") or "")) / n, 2
        )

        # ── 全局对比: 爆款 vs 普通视频的特征差异 ──
        all_durations = [v.get("duration", 0) or 0 for v in all_videos if v.get("duration", 0)]
        all_avg_dur = round(sum(all_durations) / len(all_durations), 1) if all_durations else 0
        all_desc_avg = round(
            sum(len((v.get("desc") or "").strip()) for v in all_videos) / len(all_videos), 1
        ) if all_videos else 0

        description_patterns = {
            "avg_length": round(sum(desc_lengths) / n, 1) if desc_lengths else 0,
            "vs_global_avg_length": round(
                (sum(desc_lengths) / n - all_desc_avg) / max(all_desc_avg, 1) * 100, 1
            ) if desc_lengths and all_desc_avg else 0,
            "emoji_usage_rate": has_emoji_ratio,
            "cta_usage_rate": has_cta_ratio,
            "question_usage_rate": has_question_ratio,
            "total_emoji_in_top_viral": emoji_count,
        }

        return {
            "common_tags": [
                {"tag": tag, "count": cnt} for tag, cnt in common_tags
            ],
            "optimal_duration_range": optimal_range,
            "avg_duration": avg_duration,
            "vs_global_avg_duration": round(avg_duration - all_avg_dur, 1),
            "description_patterns": description_patterns,
            "sample_size": n,
        }

    # ───────────────────── Step 6: 基准对比 ─────────────────────

    def _benchmark_accounts(self, per_account: dict, benchmarks: dict) -> dict:
        """将每账号数据与全局基准对比"""
        global_er = benchmarks.get("avg_engagement_rate", 0)
        global_p75 = benchmarks.get("engagement_percentiles", {}).get("p75", 0)
        global_median = benchmarks.get("engagement_percentiles", {}).get("p50", 0)
        global_likes = benchmarks.get("avg_likes", 0)
        global_comments = benchmarks.get("avg_comments", 0)
        global_shares = benchmarks.get("avg_shares", 0)

        for uname, data in per_account.items():
            acc_er = data.get("avg_engagement_rate", 0)
            vs_global = {}

            # 互动率
            if acc_er >= global_p75 and global_p75 > 0:
                vs_global["engagement"] = "above_75th"
            elif acc_er >= global_median and global_median > 0:
                vs_global["engagement"] = "above_median"
            else:
                vs_global["engagement"] = "below_median"

            # 点赞
            acc_likes = data.get("avg_likes", 0)
            if global_likes > 0:
                ratio = acc_likes / global_likes
                vs_global["likes"] = f"{ratio:.1f}x" if ratio >= 1 else f"{ratio:.2f}x"
            else:
                vs_global["likes"] = "N/A"

            # 评论
            acc_comments = data.get("avg_comments", 0)
            if global_comments > 0:
                ratio = acc_comments / global_comments
                vs_global["comments"] = f"{ratio:.1f}x" if ratio >= 1 else f"{ratio:.2f}x"
            else:
                vs_global["comments"] = "N/A"

            # 分享
            acc_shares = data.get("avg_shares", 0)
            if global_shares > 0:
                ratio = acc_shares / global_shares
                vs_global["shares"] = f"{ratio:.1f}x" if ratio >= 1 else f"{ratio:.2f}x"
            else:
                vs_global["shares"] = "N/A"

            data["vs_global_benchmark"] = vs_global

        return per_account

    # ───────────────────── 辅助 ─────────────────────

    @staticmethod
    def _empty_result() -> dict:
        return {
            "top_viral_videos": [],
            "per_account": {},
            "global_benchmarks": {},
            "viral_characteristics": {},
        }
