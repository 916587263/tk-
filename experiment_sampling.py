#!/usr/bin/env python
"""
QuickIntent 评论采样策略对比实验

比较 4 种采样策略在相同视频数据上的表现:
  A: first_n — 前 8 条 (当前方案)
  B: top_and_latest — Top 4 + 更深分页 4
  C: random_8_from_30 — 前 30 条中随机 8
  D: random_12_from_30 — 前 30 条中随机 12

输出:
  - 高价值误杀率
  - 进入阶段3的视频数量
  - Token 预估消耗
  - 评论抓取耗时
  - 最终 Top 视频质量

用法:
  py experiment_sampling.py
  py experiment_sampling.py --seed 42 --videos 500
"""
import argparse
import json
import random
import sys
import time
from pathlib import Path
from datetime import datetime
from collections import defaultdict, Counter

# 修复 Windows GBK 编码问题
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_DIR = Path(__file__).parent
sys.path.insert(0, str(PROJECT_DIR))

from tiktok_analyzer.unified_scorer import QuickScorer, QuickScorerConfig, FinalScorer, FinalScorerConfig
from tiktok_analyzer.intent_detector import QuickIntentScanner
from tiktok_analyzer.comment_classifier import CommentClassifierConfig
from tiktok_analyzer.logger import setup_logger

logger = setup_logger("experiment")


# ═══════════════════════════════════════════════════════════
# 采样策略定义
# ═══════════════════════════════════════════════════════════

STRATEGIES = {
    "A_first_n": {
        "label": "A: 前8条 (当前)",
        "strategy": "first_n",
        "sample_count": 8,
        "description": "当前方案: 抓取前8条hot评论",
    },
    "B_top_latest": {
        "label": "B: Top4+深层4",
        "strategy": "top_and_latest",
        "sample_count": 8,
        "description": "混合策略: Top4 (hot) + 更深分页4条",
    },
    "C_random_8": {
        "label": "C: 30池随机8",
        "strategy": "pool_random",
        "sample_count": 8,
        "pool_size": 30,
        "description": "前30条评论池中随机抽样8条",
    },
    "D_random_12": {
        "label": "D: 30池随机12",
        "strategy": "pool_random",
        "sample_count": 12,
        "pool_size": 30,
        "description": "前30条评论池中随机抽样12条 (增加样本量)",
    },
}

# 商业信号关键词 (用于高价值误杀检测)
HIGH_VALUE_SIGNALS = [
    "price", "moq", "supplier", "catalog", "sample",
    "factory", "distributor", "wholesale", "manufacturer",
    "pricing", "quote", "quotation", "minimum order",
    "oem", "odm", "importer", "exporter",
    "价格", "报价", "供应商", "工厂", "样品", "批发", "起订",
]

# 评论模板库: 模拟真实 TikTok 评论分布
# 约 12% 商业询盘, 88% 泛评论
COMMENT_TEMPLATES = {
    "commercial": [
        "How much for 1000 pcs? What's your MOQ?",
        "Can you send me catalog and price list?",
        "Are you a factory or trading company?",
        "Do you ship to USA? What's the shipping cost?",
        "Can I get a sample first?",
        "What's your best price for wholesale?",
        "Need OEM service, can you customize?",
        "Send catalog to my email please",
        "Price for 5000 units? MOQ please?",
        "Are you the manufacturer? Need OEM service",
        "Shipping cost to Germany?",
        "Can you customize the design?",
        "I need samples before bulk order",
        "Do you have a distributor in UK?",
        "What's the minimum order quantity?",
        "Looking for supplier, DM me price",
        "Factory direct price? No middleman?",
        "Export to Nigeria? What's the cost?",
        "Can you do private label?",
        "Bulk order discount available?",
    ],
    "general": [
        "Nice video! 👍",
        "Love this content!",
        "Great quality!",
        "😂😂😂",
        "Amazing work!",
        "Keep it up! 💪",
        "So cool! 🔥",
        "Beautiful product!",
        "Nice!", "Great video", "👍", "Love it",
        "Where is this?",
        "First comment!",
        "Following you!",
        "Interesting",
        "Wow! 😍",
        "Cool stuff",
        "Thanks for sharing",
    ],
}


# ═══════════════════════════════════════════════════════════
# 数据生成
# ═══════════════════════════════════════════════════════════

def generate_accounts(n: int = 15) -> list[dict]:
    """生成模拟账号"""
    accounts = []
    for i in range(n):
        accounts.append({
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
    return accounts


def generate_videos(accounts: list[dict], n: int = 500) -> list[dict]:
    """生成模拟视频 (含完整评论池)"""
    videos = []
    for i in range(n):
        quality_tier = random.random()
        if quality_tier < 0.10:
            plays, likes, comments_count, shares = 0, 0, 0, 0
        elif quality_tier < 0.18:
            plays = random.randint(50, 500)
            likes = random.randint(0, 4)
            comments_count = 0
            shares = random.randint(0, 2)
        elif quality_tier < 0.40:
            plays = random.randint(100, 2000)
            likes = random.randint(5, 20)
            comments_count = random.randint(0, 3)
            shares = random.randint(0, 10)
        elif quality_tier < 0.75:
            plays = random.randint(2000, 100000)
            likes = random.randint(20, 2000)
            comments_count = random.randint(3, 100)
            shares = random.randint(10, 500)
        else:
            plays = random.randint(50000, 500000)
            likes = random.randint(500, 20000)
            comments_count = random.randint(50, 500)
            shares = random.randint(100, 3000)

        # 描述: 30% 含商业信号
        has_commercial_signal = random.random() < 0.30
        desc = random.choice([
            "Check out our new shopping bag design! #packaging #factory",
            "Door handle installation guide #hardware #supplier",
            "Laminated woven bag production process #manufacturing",
            "Daily life vlog #fun #entertainment",
            "Unboxing my new purchase! #shopping #haul",
            "How to choose the right supplier? #business #import",
            "Factory tour: packaging production line #factory #manufacturing",
            "My favorite products from China #wholesale #b2b",
        ]) if has_commercial_signal else random.choice([
            "Funny dance #viral #fun",
            "Cooking recipe #food #yummy",
            "Travel diary #travel #wanderlust",
            "Pet compilation #cute #dogs",
            "Music cover #music #singing",
        ])

        acc = random.choice(accounts)

        # 生成完整评论池 (用于模拟不同采样策略)
        comment_pool = _generate_comment_pool(comments_count, desc)

        v = {
            "id": f"vid_{i:04d}",
            "desc": desc,
            "tags": random.sample(["factory", "supplier", "manufacturing", "packaging",
                                   "viral", "fun", "daily", "business", "b2b"], 3),
            "play_count": plays,
            "digg_count": likes,
            "comment_count": comments_count,
            "share_count": shares,
            "account_username": acc["username"],
            "url": f"https://tiktok.com/@{acc['username']}/video/vid_{i:04d}",
            "duration": random.randint(5, 120),
            "_comment_pool": comment_pool,  # 完整评论池 (30条)
        }
        videos.append(v)
    return videos


def _generate_comment_pool(actual_comment_count: int, desc: str) -> list[dict]:
    """为一个视频生成 0-30 条模拟评论 (含商业询盘占比)"""
    pool_size = min(actual_comment_count, 30)
    if pool_size == 0:
        return []

    # 如果 desc 含商业信号, 评论中商业询盘概率更高 (15%)
    has_commercial_desc = any(s in desc.lower() for s in
        ["factory", "supplier", "manufactur", "wholesale", "b2b", "oem", "import"])
    commercial_prob = 0.15 if has_commercial_desc else 0.08

    pool = []
    for ci in range(pool_size):
        is_commercial = random.random() < commercial_prob
        if is_commercial:
            text = random.choice(COMMENT_TEMPLATES["commercial"])
        else:
            text = random.choice(COMMENT_TEMPLATES["general"])

        pool.append({
            "text": text,
            "likes": random.randint(0, 80),
            "username": f"user_{random.randint(1, 999)}",
            "time": int(time.time()) - random.randint(3600, 86400 * 30),
        })
    return pool


# ═══════════════════════════════════════════════════════════
# 采样模拟
# ═══════════════════════════════════════════════════════════

def sample_comments(video: dict, strategy_key: str) -> list[dict]:
    """根据策略从视频的完整评论池中采样"""
    cfg = STRATEGIES[strategy_key]
    pool = video.get("_comment_pool", [])
    count = cfg["sample_count"]

    if not pool:
        return []

    strategy = cfg["strategy"]

    if strategy == "first_n":
        return pool[:count]

    elif strategy == "top_and_latest":
        half = max(1, count // 2)
        top = pool[:half]
        # 模拟"更深分页": 如果评论池够大, 取中间部分
        if len(pool) > count:
            later_start = min(len(pool) // 2, len(pool) - (count - half))
            later = pool[later_start:later_start + (count - half)]
        else:
            later = pool[half:half + (count - half)]
        # 合并去重
        seen = set()
        result = []
        for c in top + later:
            key = c["text"][:30]
            if key not in seen:
                seen.add(key)
                result.append(c)
        while len(result) < count and len(result) < len(pool):
            # 补充: 从剩余池中随机取
            remaining = [c for c in pool if c["text"][:30] not in seen]
            if not remaining:
                break
            c = random.choice(remaining)
            seen.add(c["text"][:30])
            result.append(c)
        return result[:count]

    elif strategy == "pool_random":
        pool_size = cfg.get("pool_size", 30)
        effective_pool = pool[:pool_size]
        if len(effective_pool) <= count:
            return list(effective_pool)
        return random.sample(effective_pool, count)

    return pool[:count]


def detect_commercial_signal(desc: str, comments: list = None) -> bool:
    """检测视频描述或评论中是否包含商业意图信号"""
    text = (desc or "").lower()
    if comments:
        for c in comments:
            text += " " + (c.get("text") or "").lower()
    for signal in HIGH_VALUE_SIGNALS:
        if signal in text:
            return True
    return False


# ═══════════════════════════════════════════════════════════
# 单策略实验
# ═══════════════════════════════════════════════════════════

def run_experiment(
    videos: list[dict],
    accounts: list[dict],
    strategy_key: str,
) -> dict:
    """对给定策略运行完整管道, 返回关键指标"""
    cfg = STRATEGIES[strategy_key]
    sample_count = cfg["sample_count"]

    # ── 阶段 0: 模拟已在 videos 中 (所有视频) ──
    total_raw = len(videos)

    # ── 阶段 1: QuickScore (复用统一评分器) ──
    qs_cfg = QuickScorerConfig()
    quick_scorer = QuickScorer(qs_cfg)
    quick_scanner = QuickIntentScanner(min_hits=1)
    acc_map = {a["username"]: a for a in accounts}

    qs_passed = []
    qs_eliminated = []
    for v in videos:
        acc = acc_map.get(v.get("account_username", ""), {})
        result = quick_scorer.score(v, acc)
        v["quick_score"] = result.total
        v["product_relevance"] = result.product_relevance
        v["video_quality_score"] = result.video_quality
        v["_commercial_whitelist"] = result.commercial_whitelist_hit
        v["eliminated_reason"] = result.eliminated_reason
        if result.passed:
            qs_passed.append(v)
        else:
            qs_eliminated.append(v)

    # ── 阶段 2: 评论采样 + QuickIntent ──
    intent_signaled = []
    no_intent = []
    sample_fetch_time = 0.0

    for v in qs_passed:
        # 样本抓取耗时 (模拟)
        if cfg["strategy"] == "top_and_latest":
            sample_fetch_time += 2.0  # 2 次 API 调用
        elif cfg["strategy"] == "pool_random" and cfg.get("pool_size", 0) > sample_count:
            sample_fetch_time += 1.8  # 1 次 API, 数据量大
        else:
            sample_fetch_time += 1.5  # 1 次 API

        sample = sample_comments(v, strategy_key)
        v["_sampled_comments"] = sample

        if quick_scanner.has_intent(sample):
            intent_signaled.append(v)
        else:
            no_intent.append(v)

    # ── 阶段 3: 深度抓取 (模拟) ──
    deep_analyzed = []
    deep_fetch_time = 0.0
    for v in intent_signaled:
        # 深度抓取: 全部评论 (模拟)
        deep_comments = list(v.get("_comment_pool", []))
        v["_deep_comments"] = deep_comments

        # 模拟 LLM 分类结果
        intent_count = sum(
            1 for c in deep_comments
            if detect_commercial_signal("", [c])
        )
        total = len(deep_comments)
        v["intent_ratio"] = round(intent_count / max(1, total), 4)
        v["intent_quality_score"] = round(
            random.uniform(30, 90), 1
        ) if intent_count > 0 else 0
        v["intent_diversity"] = min(5, intent_count)
        v["actionable_intent_count"] = max(0, intent_count - random.randint(0, 2))
        v["is_weak_reference"] = (v["actionable_intent_count"] == 0)

        deep_analyzed.append(v)
        deep_fetch_time += 3.5  # 每条深度抓取 ~3.5s

    # ── 阶段 3.5: 意图过滤 ──
    intent_filtered = []
    for v in deep_analyzed:
        ratio = v.get("intent_ratio", 0)
        actionable = v.get("actionable_intent_count", 0)
        if ratio < 0.03 and actionable < 1:
            v["_intent_filtered_out"] = True
            intent_filtered.append(v)

    passed_to_final = [v for v in deep_analyzed if not v.get("_intent_filtered_out")]

    # ── 阶段 4: FinalScore ──
    fs_cfg = FinalScorerConfig()
    final_scorer = FinalScorer(fs_cfg)

    # 构建简易 classify_map
    classify_map = {}
    for v in deep_analyzed:
        from tiktok_analyzer.comment_classifier import VideoClassifyResult
        classify_map[v["id"]] = VideoClassifyResult(
            video_id=v["id"],
            total_comments=len(v.get("_deep_comments", [])),
            intent_comments=int(v.get("intent_ratio", 0) * len(v.get("_deep_comments", []))),
            intent_ratio=v.get("intent_ratio", 0),
            intent_quality_score=v.get("intent_quality_score", 0),
            intent_diversity=v.get("intent_diversity", 0),
            actionable_intent_count=v.get("actionable_intent_count", 0),
            is_weak_reference=v.get("is_weak_reference", False),
        )

    all_scored, top_refs = final_scorer.score_all(passed_to_final, classify_map)

    # ── 高价值误杀检测 ──
    high_value_false = []
    # QuickScore 淘汰的
    for v in qs_eliminated:
        if detect_commercial_signal(v.get("desc", "")):
            high_value_false.append({
                "video_id": v.get("id", ""),
                "eliminated_by": "QuickScore",
                "quick_score": v.get("quick_score", 0),
                "signal_source": "desc",
            })
    # QuickIntent 淘汰的
    intent_sig_ids = {v.get("id") for v in intent_signaled}
    for v in no_intent:
        if detect_commercial_signal(v.get("desc", ""), v.get("_sampled_comments", [])):
            high_value_false.append({
                "video_id": v.get("id", ""),
                "eliminated_by": "QuickIntent",
                "quick_score": v.get("quick_score", 0),
                "signal_source": "sampled_comments",
            })

    hv_false_rate = round(len(high_value_false) / max(1, total_raw) * 100, 1)

    # ── Token 估算 ──
    # CommentClassifier: 每 6 个深度视频 1 次 LLM 调用，每次 ~800 tokens
    llm_batches = max(1, len(deep_analyzed) // 6)
    cc_tokens = llm_batches * 800
    analyzer_tokens = 2000  # AI 分析
    total_tokens = cc_tokens + analyzer_tokens

    # ── Top 视频质量 ──
    top_5 = top_refs[:5]
    avg_final_score = sum(v.get("final_score", 0) for v in top_5) / max(1, len(top_5))
    avg_intent_ratio = sum(v.get("intent_ratio", 0) for v in top_5) / max(1, len(top_5))
    avg_plays = sum(v.get("play_count", 0) for v in top_5) / max(1, len(top_5))

    # ── 耗时 ──
    total_fetch_time = sample_fetch_time + deep_fetch_time

    return {
        "total_raw": total_raw,
        "qs_passed": len(qs_passed),
        "qs_eliminated": len(qs_eliminated),
        "intent_signaled": len(intent_signaled),
        "no_intent": len(no_intent),
        "deep_analyzed": len(deep_analyzed),
        "intent_filtered": len(intent_filtered),
        "passed_to_final": len(passed_to_final),
        "top_references": len(top_refs),
        "high_value_false_count": len(high_value_false),
        "hv_false_rate": hv_false_rate,
        "hv_false_by_stage": {
            stage: count for stage, count in
            Counter(fv["eliminated_by"] for fv in high_value_false).items()
        },
        "token_estimate": total_tokens,
        "cc_tokens": cc_tokens,
        "fetch_time": round(total_fetch_time, 1),
        "sample_time": round(sample_fetch_time, 1),
        "deep_time": round(deep_fetch_time, 1),
        "top_quality": {
            "avg_final_score": round(avg_final_score, 1),
            "avg_intent_ratio": round(avg_intent_ratio, 3),
            "avg_plays": int(avg_plays),
        },
        "funnel": {
            "qs_retention": round(len(qs_passed) / max(1, total_raw) * 100, 1),
            "intent_retention": round(len(intent_signaled) / max(1, len(qs_passed)) * 100, 1),
            "deep_retention": round(len(deep_analyzed) / max(1, len(intent_signaled)) * 100, 1),
            "intent_filter_retention": round(
                len(passed_to_final) / max(1, len(deep_analyzed)) * 100, 1
            ),
            "final_retention": round(len(top_refs) / max(1, len(passed_to_final)) * 100, 1),
            "cumulative_final": round(len(top_refs) / max(1, total_raw) * 100, 1),
        },
    }


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="QuickIntent 采样策略对比实验")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument("--videos", type=int, default=500, help="模拟视频数")
    parser.add_argument("--accounts", type=int, default=15, help="模拟账号数")
    parser.add_argument("--runs", type=int, default=1,
                        help="每策略重复次数 (用于稳定随机策略结果)")
    args = parser.parse_args()

    random.seed(args.seed)

    print("=" * 72)
    print("  QuickIntent 评论采样策略对比实验")
    print("=" * 72)
    print(f"  视频: {args.videos} | 账号: {args.accounts} | 重复: {args.runs} 次")
    print()

    # ── 生成共享数据 ──
    print("[1/3] 生成共享模拟数据...")
    accounts = generate_accounts(args.accounts)
    videos = generate_videos(accounts, args.videos)
    print(f"  账号: {len(accounts)}")
    print(f"  视频: {len(videos)}")
    print(f"  有评论池视频: {sum(1 for v in videos if v['_comment_pool'])}")

    # ── 运行所有策略 ──
    print(f"\n[2/3] 运行 {len(STRATEGIES)} 种采样策略...")

    all_results = {}
    for key in STRATEGIES:
        print(f"  ▶ {STRATEGIES[key]['label']}...", end=" ")

        if "random" in key and args.runs > 1:
            # 随机策略: 多次运行取平均
            runs_data = []
            for _ in range(args.runs):
                # 重置视频状态
                for v in videos:
                    v.pop("quick_score", None)
                    v.pop("product_relevance", None)
                    v.pop("eliminated_reason", None)
                    v.pop("_sampled_comments", None)
                    v.pop("_deep_comments", None)
                    v.pop("_intent_filtered_out", None)
                    v.pop("final_score", None)
                    v.pop("intent_ratio", None)
                runs_data.append(run_experiment(videos, accounts, key))

            # 平均
            result = {}
            for k in runs_data[0]:
                if isinstance(runs_data[0][k], (int, float)):
                    result[k] = round(sum(r[k] for r in runs_data) / len(runs_data), 1)
                else:
                    result[k] = runs_data[0][k]
        else:
            # 确定性策略 / 单次运行
            for v in videos:
                v.pop("quick_score", None)
                v.pop("product_relevance", None)
                v.pop("eliminated_reason", None)
                v.pop("_sampled_comments", None)
                v.pop("_deep_comments", None)
                v.pop("_intent_filtered_out", None)
                v.pop("final_score", None)
                v.pop("intent_ratio", None)
            result = run_experiment(videos, accounts, key)

        all_results[key] = result
        print(f"误杀={result['hv_false_rate']}% 阶段3={result['deep_analyzed']}条 "
              f"Token={result['token_estimate']:,}")

    # ── 输出对比报告 ──
    print(f"\n[3/3] 生成对比报告...\n")

    _print_comparison_table(all_results)
    _print_funnel_comparison(all_results)
    _print_recommendation(all_results)

    # 保存JSON
    output_dir = PROJECT_DIR / "data" / f"experiment_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "experiment_results.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({
            "config": {"seed": args.seed, "videos": args.videos, "runs": args.runs},
            "strategies": {
                key: {
                    "label": STRATEGIES[key]["label"],
                    "description": STRATEGIES[key]["description"],
                    "results": {k: v for k, v in all_results[key].items()
                                if not isinstance(v, dict)},
                }
                for key in STRATEGIES
            },
            "results": {key: all_results[key] for key in STRATEGIES},
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n详细数据: {output_file}")


def _print_comparison_table(all_results: dict):
    """打印核心指标对比表"""
    print("=" * 72)
    print("  核心指标对比")
    print("=" * 72)
    print()

    # 表头
    header = f"{'策略':<24} {'高价值误杀':>10} {'阶段3视频':>9} {'Token':>10} {'抓取耗时':>9} {'Top5均分':>9} {'意图率':>8}"
    print(header)
    print("-" * len(header))

    # 找基准 (策略 A)
    base = all_results.get("A_first_n", {})

    for key in ["A_first_n", "B_top_latest", "C_random_8", "D_random_12"]:
        r = all_results.get(key, {})
        if not r:
            continue
        label = f"{STRATEGIES[key]['label']:<24}"
        hv = f"{r['hv_false_rate']}% ({r['high_value_false_count']}条)"
        stage3 = r['deep_analyzed']
        token = f"{r['token_estimate']:,}"
        ftime = f"{r['fetch_time']}s"
        top_score = r['top_quality']['avg_final_score']
        intent_r = r['top_quality']['avg_intent_ratio']

        delta_hv = ""
        if key != "A_first_n":
            d = r['hv_false_rate'] - base.get('hv_false_rate', 0)
            delta_hv = f" {'+' if d > 0 else ''}{d:.1f}%"

        delta_tok = ""
        if key != "A_first_n":
            d = r['token_estimate'] - base.get('token_estimate', 0)
            delta_tok = f" {'+' if d > 0 else ''}{d:,}"

        print(f"{label} {hv:>10}{delta_hv:<7} {stage3:>9} {token:>10}{delta_tok:<10} "
              f"{ftime:>9} {top_score:>9} {intent_r:>8.3f}")

    print()


def _print_funnel_comparison(all_results: dict):
    """打印漏斗对比"""
    print("=" * 72)
    print("  漏斗转化对比")
    print("=" * 72)
    print()

    print(f"{'策略':<24} {'原始':>5} {'QS通过':>6} {'有意图':>6} {'阶段3':>6} "
          f"{'滤后':>6} {'Top':>5} {'累计转化':>8}")
    print("-" * 72)

    for key in ["A_first_n", "B_top_latest", "C_random_8", "D_random_12"]:
        r = all_results.get(key, {})
        if not r:
            continue
        f = r.get("funnel", {})
        label = STRATEGIES[key]["label"][:22]
        print(f"{label:<24} {r['total_raw']:>5} {r['qs_passed']:>6} "
              f"{r['intent_signaled']:>6} {r['deep_analyzed']:>6} "
              f"{r['passed_to_final']:>6} {r['top_references']:>5} "
              f"{f.get('cumulative_final', 0):>7.1f}%")

    print()


def _print_recommendation(all_results: dict):
    """输出最优方案推荐"""
    print("=" * 72)
    print("  综合评估")
    print("=" * 72)
    print()

    # 评分: 误杀率 (权重 40%), Token (权重 30%), 耗时 (权重 15%), Top质量 (权重 15%)
    best_key = None
    best_score = float("inf")

    base = all_results.get("A_first_n", {})
    base_hv = base.get("hv_false_rate", 100)
    base_token = base.get("token_estimate", 1)

    for key in ["A_first_n", "B_top_latest", "C_random_8", "D_random_12"]:
        r = all_results.get(key, {})
        if not r:
            continue

        hv = r.get("hv_false_rate", 100)
        token = r.get("token_estimate", 1)
        ftime = r.get("fetch_time", 1000)
        top_score = r["top_quality"]["avg_final_score"]

        # 归一化: 越小越好 (除了 Top 分数)
        score = (
            0.40 * (hv / max(1, base_hv))           # 误杀率 (越低越好)
            + 0.30 * (token / max(1, base_token))    # Token (越低越好)
            + 0.15 * (ftime / max(1, base.get("fetch_time", 1)))  # 耗时 (越低越好)
            + 0.15 * (60 / max(1, top_score))        # Top 质量 (越高越好, 60分基准)
        )

        print(f"  {STRATEGIES[key]['label']:<30} 综合分: {score:.3f}")

        if score < best_score:
            best_score = score
            best_key = key

    print()
    if best_key:
        print(f"  🏆 推荐方案: {STRATEGIES[best_key]['label']}")
        print(f"     理由: {STRATEGIES[best_key]['description']}")
        r = all_results[best_key]
        print(f"     误杀率: {r['hv_false_rate']}% | "
              f"阶段3: {r['deep_analyzed']}条 | "
              f"Token: {r['token_estimate']:,}")
        print(f"     QuickScore 误杀: {r.get('hv_false_by_stage', {}).get('QuickScore', 0)} 条")
        print(f"     QuickIntent 误杀: {r.get('hv_false_by_stage', {}).get('QuickIntent', 0)} 条")

    # 误杀阶段分布
    print()
    print("  误杀阶段分布对比:")
    print(f"  {'策略':<24} {'QuickScore':>12} {'QuickIntent':>13}")
    print("  " + "-" * 49)
    for key in ["A_first_n", "B_top_latest", "C_random_8", "D_random_12"]:
        r = all_results.get(key, {})
        if not r:
            continue
        dist = r.get("hv_false_by_stage", {})
        print(f"  {STRATEGIES[key]['label']:<24} {dist.get('QuickScore', 0):>12} "
              f"{dist.get('QuickIntent', 0):>13}")


if __name__ == "__main__":
    main()
