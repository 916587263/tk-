#!/usr/bin/env python
"""
QuickIntent 淘汰验证 — 采样 vs 全量评论对比

从 QuickIntent 淘汰的高价值误杀视频中随机抽取 20 条，
对比采样评论 (8条) vs 全量评论 (30-50条) 的商业信号命中情况。

输出:
  1. 采样命中信号 vs 全量命中信号
  2. 漏检率: 全量有信号但采样未命中 / 总淘汰数
  3. 漏检视频清单 + 被遗漏的具体商业句子

用法:
  py verify_quickintent.py
  py verify_quickintent.py --seed 42 --sample-size 20
"""
import argparse
import json
import random
import sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict, Counter

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_DIR = Path(__file__).parent
sys.path.insert(0, str(PROJECT_DIR))

from tiktok_analyzer.unified_scorer import QuickScorer, QuickScorerConfig
from tiktok_analyzer.intent_detector import QuickIntentScanner
from tiktok_analyzer.logger import setup_logger

logger = setup_logger("verify")

# ═══════════════════════════════════════════════════════════
# 扩展商业信号关键词 (用户指定的 + 系统原有的)
# ═══════════════════════════════════════════════════════════

# QuickIntentScanner 当前使用的关键词 (来自 intent_detector.py)
QUICKINTENT_KEYWORDS = [
    "price", "moq", "supplier", "catalog", "sample",
    "factory", "how much", "buy", "order",
    "shipping", "wholesale", "price list", "quote",
    "contact", "email", "whatsapp",
    "价格", "报价", "多少钱", "批发", "怎么买", "厂家",
]

# 用户指定的补充检查词
EXTENDED_SIGNALS = [
    "where can i buy", "how to order", "interested",
    "contact me", "dm sent", "catalog",
    "quotation", "pricing", "distributor",
    "reseller", "wholesale", "ship to",
    "send me", "i need", "i want to buy",
    "looking for", "do you sell", "can i get",
    "minimum order", "bulk", "unit price",
    "cost per", "delivery to", "freight",
    "export", "oem", "odm", "custom",
]

# 合并所有检测词
ALL_SIGNALS = list(set(
    [kw.lower() for kw in QUICKINTENT_KEYWORDS + EXTENDED_SIGNALS]
))


# ═══════════════════════════════════════════════════════════
# 评论生成 (含商业信号细粒度控制)
# ═══════════════════════════════════════════════════════════

COMMERCIAL_PATTERNS = [
    "How much for {qty} pcs? What's your MOQ?",
    "Can you send me catalog and price list?",
    "Are you a factory or trading company?",
    "Do you ship to {country}? What's the shipping cost?",
    "Can I get a sample first? DM sent",
    "I'm interested, please contact me on WhatsApp",
    "What's your best price for wholesale?",
    "Need OEM service, can you customize?",
    "Looking for supplier, where can I buy this?",
    "How to order? I need {qty} units",
    "Send me quotation for bulk order",
    "Do you have distributor in {country}?",
    "I want to buy this, what's the pricing?",
    "Are you a reseller or manufacturer?",
    "Ship to {country}? How much freight?",
    "Unit price for {qty} pcs? Send catalog",
]

GENERAL_PATTERNS = [
    "Nice video! 👍",
    "Love this content!",
    "Great quality!",
    "😂😂😂",
    "Amazing work!",
    "Keep it up! 💪",
    "So cool! 🔥",
    "Beautiful product!",
    "Nice!", "Great video", "👍",
    "Where is this?",
    "First comment!",
    "Interesting video",
    "Wow! 😍",
    "Cool stuff",
    "Thanks for sharing",
    "Love from {country}!",
    "This is awesome",
]


def generate_comment(is_commercial: bool, variant: int = 0) -> dict:
    """生成单条评论"""
    if is_commercial:
        template = random.choice(COMMERCIAL_PATTERNS)
        text = template.format(
            qty=random.choice([100, 500, 1000, 5000, 10000]),
            country=random.choice(["USA", "UK", "Germany", "Nigeria", "Canada"]),
        )
    else:
        text = random.choice(GENERAL_PATTERNS).format(
            country=random.choice(["USA", "UK", "India", "Brazil"]),
        )

    return {
        "text": text,
        "likes": random.randint(0, 80),
        "username": f"user_{random.randint(1, 999)}",
        "time": 0,  # mock
    }


def generate_full_comments(desc: str, actual_comment_count: int) -> list[dict]:
    """为视频生成完整评论池 (模拟 30-50 条)"""
    pool_size = min(actual_comment_count, random.randint(25, 40))
    if pool_size == 0:
        return []

    # desc 含商业信号 → 评论中商业询盘概率更高
    desc_lower = desc.lower()
    has_commercial_desc = any(
        kw in desc_lower for kw in
        ["factory", "supplier", "manufactur", "wholesale", "b2b", "oem", "import"]
    )

    # 模拟真实分布: 商业信号评论占比
    if has_commercial_desc:
        # 有商业 desc 的视频: 5-25% 评论含商业信号
        commercial_pct = random.uniform(0.05, 0.25)
    else:
        # 无商业 desc 的视频: 2-10% 评论含商业信号
        commercial_pct = random.uniform(0.02, 0.10)

    # 决定哪些位置是商业评论 (倾向于分散分布)
    commercial_count = max(1, int(pool_size * commercial_pct))
    commercial_positions = set()
    # 确保商业评论分散在 0..pool_size 范围内
    step = max(1, pool_size // (commercial_count + 1))
    for ci in range(commercial_count):
        pos = min(pool_size - 1, ci * step + random.randint(0, step - 1))
        commercial_positions.add(pos)

    pool = []
    for ci in range(pool_size):
        is_commercial = ci in commercial_positions
        c = generate_comment(is_commercial, ci)
        c["_is_commercial"] = is_commercial
        pool.append(c)

    # 按热度排序 (商业评论也有不同热度)
    # 前 8 条是 "hot" — 可能是商业也可能是普通
    # TikTok 默认按热度排序，商业询盘通常热度不高
    # 模拟: 商业评论 ~30% 概率进入 Top 8
    pool.sort(key=lambda c: (
        -(c["likes"] + (30 if c["_is_commercial"] and random.random() < 0.3 else 0))
    ))

    return pool


# ═══════════════════════════════════════════════════════════
# 信号检测
# ═══════════════════════════════════════════════════════════

def detect_signals(comments: list[dict]) -> dict:
    """检测评论列表中的商业信号, 返回命中详情"""
    text = " ".join(c.get("text", "") for c in comments).lower()

    hits = {}
    for signal in ALL_SIGNALS:
        if signal in text:
            # 找到具体命中的评论
            matching = []
            for c in comments:
                ct = (c.get("text", "") or "").lower()
                if signal in ct:
                    matching.append({
                        "text": c["text"][:100],
                        "signal": signal,
                    })
            hits[signal] = matching

    return {
        "total_signals": len(hits),
        "unique_signals": list(hits.keys()),
        "hits_detail": hits,
        "matched_comments": sum(len(v) for v in hits.values()),
    }


def detect_quickintent_hit(comments: list[dict]) -> bool:
    """模拟 QuickIntentScanner.has_intent() — 仅检查原始关键词集"""
    scanner = QuickIntentScanner(min_hits=1)
    return scanner.has_intent(comments)


def detect_desc_commercial(desc: str) -> bool:
    """检测 desc 是否含商业信号"""
    desc_lower = desc.lower()
    for kw in ["factory", "supplier", "manufacturer", "wholesale",
               "b2b", "oem", "import", "export", "distributor"]:
        if kw in desc_lower:
            return True
    return False


# ═══════════════════════════════════════════════════════════
# 生成账号和视频
# ═══════════════════════════════════════════════════════════

def generate_accounts(n: int = 15) -> list[dict]:
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
    """生成视频, 每个视频有完整评论池"""
    videos = []
    for i in range(n):
        quality_tier = random.random()
        if quality_tier < 0.10:
            plays, likes, cc, shares = 0, 0, 0, 0
        elif quality_tier < 0.18:
            plays, likes, cc, shares = random.randint(50, 500), random.randint(0, 4), 0, random.randint(0, 2)
        elif quality_tier < 0.40:
            plays, likes, cc, shares = random.randint(100, 2000), random.randint(5, 20), random.randint(0, 3), random.randint(0, 10)
        elif quality_tier < 0.75:
            plays, likes, cc, shares = random.randint(2000, 100000), random.randint(20, 2000), random.randint(3, 100), random.randint(10, 500)
        else:
            plays, likes, cc, shares = random.randint(50000, 500000), random.randint(500, 20000), random.randint(50, 500), random.randint(100, 3000)

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
        full_pool = generate_full_comments(desc, cc)

        v = {
            "id": f"vid_{i:04d}",
            "desc": desc,
            "tags": random.sample(["factory", "supplier", "manufacturing", "packaging",
                                   "viral", "fun", "daily", "business", "b2b"], 3),
            "play_count": plays,
            "digg_count": likes,
            "comment_count": cc,
            "share_count": shares,
            "account_username": acc["username"],
            "url": f"https://tiktok.com/@{acc['username']}/video/vid_{i:04d}",
            "duration": random.randint(5, 120),
            "_comment_pool": full_pool,
        }
        videos.append(v)
    return videos


# ═══════════════════════════════════════════════════════════
# 主验证逻辑
# ═══════════════════════════════════════════════════════════

def run_verification(seed: int = 42, sample_size: int = 20):
    random.seed(seed)

    print("=" * 72)
    print("  QuickIntent 淘汰验证 — 采样 vs 全量评论对比")
    print("=" * 72)
    print(f"  seed={seed}  sample_size={sample_size}")
    print()

    # ── 1. 生成数据 ──
    print("[1/4] 生成模拟数据...")
    accounts = generate_accounts(15)
    videos = generate_videos(accounts, 500)
    print(f"  账号: {len(accounts)} | 视频: {len(videos)}")

    # ── 2. 运行 QuickScore + QuickIntent ──
    print("[2/4] 运行阶段 1-2 (QuickScore + QuickIntent)...")

    qs_cfg = QuickScorerConfig()
    quick_scorer = QuickScorer(qs_cfg)
    acc_map = {a["username"]: a for a in accounts}

    qs_passed, qs_eliminated = [], []
    for v in videos:
        acc = acc_map.get(v.get("account_username", ""), {})
        result = quick_scorer.score(v, acc)
        v["quick_score"] = result.total
        v["product_relevance"] = result.product_relevance
        v["_commercial_whitelist"] = result.commercial_whitelist_hit
        v["eliminated_reason"] = result.eliminated_reason
        if result.passed:
            qs_passed.append(v)
        else:
            qs_eliminated.append(v)

    # QuickIntent: 采样 8 条 + 检测
    intent_signaled, quickintent_eliminated = [], []
    for v in qs_passed:
        pool = v.get("_comment_pool", [])
        sampled = pool[:8]  # 前 8 条 (strategy A)
        v["_sampled_comments"] = sampled

        if detect_quickintent_hit(sampled):
            intent_signaled.append(v)
        else:
            quickintent_eliminated.append(v)

    print(f"  QuickScore 通过: {len(qs_passed)} | 淘汰: {len(qs_eliminated)}")
    print(f"  QuickIntent 信号: {len(intent_signaled)} | 淘汰: {len(quickintent_eliminated)}")

    # ── 3. 识别高价值误杀 ──
    print("[3/4] 识别 QuickIntent 高价值误杀视频...")

    # 高价值误杀: desc含商业信号但QuickIntent淘汰
    hv_false = []
    for v in quickintent_eliminated:
        if detect_desc_commercial(v["desc"]):
            hv_false.append(v)

    print(f"  高价值误杀 (QuickIntent): {len(hv_false)} 条")
    print(f"  QuickScore 淘汰: {len(qs_eliminated)} 条 "
          f"(其中 desc 含商业: {sum(1 for v in qs_eliminated if detect_desc_commercial(v['desc']))})")

    # ── 4. 深度复查 ──
    print(f"\n[4/4] 深度复查 (抽取 {min(sample_size, len(hv_false))} 条)...")

    sample_videos = random.sample(hv_false, min(sample_size, len(hv_false)))

    results = []
    stats = {
        "total_checked": 0,
        "sample_missed_full_hit": 0,   # 采样漏检但全量命中
        "both_missed": 0,              # 采样和全量都漏检 (真阴性)
        "both_hit": 0,                 # 采样和全量都命中 (本不会淘汰)
        "sample_hit_only": 0,          # 采样命中但全量未命中 (不可能)
    }

    for v in sample_videos:
        sampled = v.get("_sampled_comments", [])
        full = v.get("_comment_pool", [])

        sampled_signals = detect_signals(sampled)
        full_signals = detect_signals(full)

        sampled_hit = detect_quickintent_hit(sampled)
        full_hit = detect_quickintent_hit(full)

        # 分类
        if not sampled_hit and full_hit:
            stats["sample_missed_full_hit"] += 1
            category = "🔴 漏检"
        elif not sampled_hit and not full_hit:
            stats["both_missed"] += 1
            category = "⚪ 真阴性"
        elif sampled_hit and full_hit:
            stats["both_hit"] += 1
            category = "🟢 双命中"
        else:
            stats["sample_hit_only"] += 1
            category = "⚠️ 仅采样命中"

        # 找出采样漏检但全量命中的具体信号
        missed_signals = {}
        for signal, matches in full_signals["hits_detail"].items():
            # 检查是否在采样中也命中了
            if signal not in sampled_signals["hits_detail"]:
                missed_signals[signal] = [
                    m for m in matches
                    if m["text"].lower() not in {
                        sm["text"].lower()
                        for sm_list in sampled_signals["hits_detail"].values()
                        for sm in sm_list
                    }
                ]

        results.append({
            "video_id": v["id"],
            "desc": v["desc"][:80],
            "quick_score": v["quick_score"],
            "comment_count": len(full),
            "category": category,
            "sampled_signals": sampled_signals["unique_signals"],
            "full_signals": full_signals["unique_signals"],
            "missed_signals": {k: [m["text"][:80] for m in v]
                               for k, v in missed_signals.items()},
            "sampled_hit": sampled_hit,
            "full_hit": full_hit,
            "sample_commercial_count": sum(
                1 for c in sampled if c.get("_is_commercial")
            ),
            "full_commercial_count": sum(
                1 for c in full if c.get("_is_commercial")
            ),
        })

        stats["total_checked"] += 1

    # ── 输出报告 ──
    _print_report(results, stats, hv_false, quickintent_eliminated,
                  qs_eliminated, intent_signaled)

    # 保存 JSON
    output_dir = PROJECT_DIR / "data" / f"verify_qi_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_dir / "verify_results.json", "w", encoding="utf-8") as f:
        json.dump({
            "config": {"seed": seed, "sample_size": sample_size},
            "stats": stats,
            "results": [{
                k: v for k, v in r.items()
            } for r in results],
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n详细数据: {output_dir / 'verify_results.json'}")


def _print_report(results, stats, hv_false, quickintent_eliminated,
                  qs_eliminated, intent_signaled):
    """打印验证报告"""

    # ── 概览 ──
    print()
    print("=" * 72)
    print("  验证结果概览")
    print("=" * 72)
    print(f"  QuickIntent 淘汰总数: {len(quickintent_eliminated)}")
    print(f"  其中 desc 含商业信号: {len(hv_false)} (高价值误杀)")
    print(f"  抽查样本: {len(results)} 条")
    print()
    print(f"  {'分类':<20} {'数量':>6} {'占比':>8}")
    print(f"  {'-'*34}")
    print(f"  {'🔴 漏检 (采样×, 全量✓)':<20} {stats['sample_missed_full_hit']:>6} "
          f"{stats['sample_missed_full_hit']/max(1,stats['total_checked'])*100:>7.1f}%")
    print(f"  {'⚪ 真阴性 (双×)':<20} {stats['both_missed']:>6} "
          f"{stats['both_missed']/max(1,stats['total_checked'])*100:>7.1f}%")
    print(f"  {'🟢 双命中 (双✓)':<20} {stats['both_hit']:>6} "
          f"{stats['both_hit']/max(1,stats['total_checked'])*100:>7.1f}%")
    print()

    # ── 关键结论 ──
    miss_rate = stats['sample_missed_full_hit'] / max(1, stats['total_checked']) * 100
    true_neg_rate = stats['both_missed'] / max(1, stats['total_checked']) * 100

    print(f"  ═══════════════════════════════════════")
    if miss_rate < 5:
        print(f"  ✅ 漏检率: {miss_rate:.1f}% — QuickIntent 淘汰准确")
        print(f"     抽查 {stats['total_checked']} 条中仅 {stats['sample_missed_full_hit']} 条漏检")
    elif miss_rate < 15:
        print(f"  ⚠️  漏检率: {miss_rate:.1f}% — 轻微漏检, 可接受")
        print(f"     抽查 {stats['total_checked']} 条中有 {stats['sample_missed_full_hit']} 条漏检")
    else:
        print(f"  🔴 漏检率: {miss_rate:.1f}% — 严重漏检, 需改进采样策略")
        print(f"     抽查 {stats['total_checked']} 条中有 {stats['sample_missed_full_hit']} 条漏检")
    print(f"  ═══════════════════════════════════════")
    print()

    # ── 漏检视频详情 ──
    missed = [r for r in results if r["category"] == "🔴 漏检"]
    if missed:
        print("=" * 72)
        print(f"  🔴 漏检视频详情 ({len(missed)} 条)")
        print("=" * 72)
        print()

        for i, r in enumerate(missed, 1):
            print(f"  [{i}] {r['video_id']} | {r['desc'][:60]}")
            print(f"      采样 {len(r['sampled_signals'])} 信号: {r['sampled_signals'][:5]}")
            print(f"      全量 {len(r['full_signals'])} 信号: {r['full_signals'][:5]}")
            print(f"      样本含商业: {r['sample_commercial_count']}/{8 if True else 0} 条 "
                  f"vs 全量: {r['full_commercial_count']}/{r['comment_count']} 条")
            if r["missed_signals"]:
                print(f"      被遗漏的关键信号:")
                for sig, texts in list(r["missed_signals"].items())[:3]:
                    for t in texts[:2]:
                        print(f"        → [{sig}] \"{t}\"")
            print()

    # ── 真阴性样本 (确认淘汰正确) ──
    true_neg = [r for r in results if r["category"] == "⚪ 真阴性"]
    if true_neg and len(true_neg) <= 10:
        print("=" * 72)
        print(f"  ⚪ 真阴性样本 ({len(true_neg)} 条) — 确认淘汰正确")
        print("=" * 72)
        print()
        for i, r in enumerate(true_neg[:5], 1):
            print(f"  [{i}] {r['video_id']} | {r['desc'][:60]}")
            print(f"      全量 {r['comment_count']} 条评论中商业信号: 0")
            print(f"      全量含商业评论数: {r['full_commercial_count']}")
            print()

    # ── 全量评论信号覆盖分析 ──
    print("=" * 72)
    print("  全量评论商业信号覆盖分析")
    print("=" * 72)
    print()

    # 计算所有漏检视频中, 哪些信号在全量中存在但采样中缺失
    all_missed_signals = Counter()
    for r in missed:
        for sig in r["missed_signals"]:
            all_missed_signals[sig] += 1

    if all_missed_signals:
        print("  采样策略最常遗漏的商业信号 (Top 10):")
        print(f"  {'信号':<25} {'遗漏次数':>8}")
        print(f"  {'-'*35}")
        for sig, count in all_missed_signals.most_common(10):
            print(f"  {sig:<25} {count:>8}")

    print()

    # ── 推算总体漏检 ──
    print("=" * 72)
    print("  总体估算")
    print("=" * 72)
    print()

    total_hv = len(hv_false)
    estimated_missed = int(total_hv * (miss_rate / 100)) if miss_rate > 0 else 0
    corrected_hv_rate = (total_hv - estimated_missed) / 500 * 100

    print(f"  QuickIntent 高价值误杀总数: {total_hv}")
    print(f"  推算真阴性 (淘汰正确):     {total_hv - estimated_missed} 条")
    print(f"  推算漏检 (应进入阶段3):    {estimated_missed} 条")
    print(f"  修正后高价值误杀率:        {corrected_hv_rate:.1f}% "
          f"(原 {total_hv/500*100:.1f}%)")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QuickIntent 淘汰验证")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument("--sample-size", type=int, default=20,
                        help="抽查视频数")
    args = parser.parse_args()

    run_verification(seed=args.seed, sample_size=args.sample_size)
