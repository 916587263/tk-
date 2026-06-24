"""
P2 诊断: Issue #13 商业白名单误杀分析

模拟典型 B2B 工厂视频场景，量化阶段 1 QuickScore 的误杀情况。
不改代码，仅输出诊断报告。
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from tiktok_analyzer.unified_scorer import QuickScorer, QuickScorerConfig

import yaml

# 加载实际配置
cfg_path = BASE_DIR / "config.yaml"
with open(cfg_path, "r", encoding="utf-8") as f:
    raw_cfg = yaml.safe_load(f) or {}
qs_data = raw_cfg.get("quick_scorer", {})
config = QuickScorerConfig(**{
    k: v for k, v in qs_data.items()
    if k in QuickScorerConfig.__dataclass_fields__
})
qs = QuickScorer(config)

# ═══════════════════════════════════════════════════════════
# 场景定义
# ═══════════════════════════════════════════════════════════

scenarios = [
    # (label, desc, plays, likes, comments, bio, nickname, follower_count,
    #  expected: should this B2B video survive?)
    {
        "label": "标准工厂展示 (低播放)",
        "video": {"desc": "PP non woven bag factory production line", "plays": 500, "likes": 8, "comments": 1, "tags": []},
        "account": {"bio": "professional non woven bag manufacturer since 2010", "nickname": "BestBag Factory", "follower_count": 2000},
        "should_survive": True,
        "risk": "低播放(<1K)但含factory+manufacturer, 真实B2B工厂号"
    },
    {
        "label": "OEM/ODM 供应商 (仅商业词)",
        "video": {"desc": "MOQ 500pcs ODM custom bags welcome", "plays": 300, "likes": 4, "comments": 0, "tags": []},
        "account": {"bio": "OEM ODM bag supplier, competitive price", "nickname": "ODM Bag Co", "follower_count": 500},
        "should_survive": True,
        "risk": "desc仅含MOQ/ODM(whitelist中但非industry_keyword), 产品相关度得分极低"
    },
    {
        "label": "经销商/进口商视频",
        "video": {"desc": "we are distributor and importer of packaging materials", "plays": 800, "likes": 6, "comments": 1, "tags": []},
        "account": {"bio": "global distributor of industrial packaging", "nickname": "GlobalPack Distributors", "follower_count": 1500},
        "should_survive": True,
        "risk": "distributor/importer在whitelist但不在industry_keyword, 得分低"
    },
    {
        "label": "含daily的工厂号",
        "video": {"desc": "factory production process of non woven bags", "plays": 5000, "likes": 50, "comments": 5, "tags": ["factory", "manufacturing"]},
        "account": {"bio": "daily updated factory videos, contact us for price", "nickname": "Daily Bag Factory", "follower_count": 10000},
        "should_survive": True,
        "risk": "bio/nickname含'daily'触发personal_account_penalty, 降30分"
    },
    {
        "label": "纯娱乐视频 (应淘汰)",
        "video": {"desc": "funny dance challenge #fyp", "plays": 500000, "likes": 50000, "comments": 1000, "tags": ["fyp", "dance"]},
        "account": {"bio": "just for fun, daily vlog", "nickname": "Fun Creator", "follower_count": 500000},
        "should_survive": False,
        "risk": "纯娱乐内容, 应被淘汰 (对照)"
    },
    {
        "label": "低价小工厂 (MOQ+报价, 低粉)",
        "video": {"desc": "cheap pp woven bags MOQ 100pcs free sample", "plays": 200, "likes": 3, "comments": 1, "tags": ["ppwoven", "bag"]},
        "account": {"bio": "small factory, competitive price", "nickname": "Small Bag Maker", "follower_count": 80},
        "should_survive": True,
        "risk": "典型小工厂, 低粉但含MOQ+sample+factory; 白名单命中但行业分低"
    },
    {
        "label": "外贸出口商 (中国厂家)",
        "video": {"desc": "China factory export non woven bag wholesale price", "plays": 1500, "likes": 25, "comments": 3, "tags": ["export", "chinabag"]},
        "account": {"bio": "China bag manufacturer, export to worldwide", "nickname": "ChinaBag Export", "follower_count": 5000},
        "should_survive": True,
        "risk": "export在industry_keyword中, 应正常评分"
    },
    {
        "label": "低互动工厂视频 (0评论)",
        "video": {"desc": "non woven bag making machine working", "plays": 100, "likes": 2, "comments": 0, "tags": []},
        "account": {"bio": "bag making machine factory", "nickname": "Machine Factory", "follower_count": 300},
        "should_survive": True,
        "risk": "0评论+低赞, 可能触发硬淘汰 likes<5 AND comments=0"
    },
]


# ═══════════════════════════════════════════════════════════
# 诊断执行
# ═══════════════════════════════════════════════════════════

def build_account(data):
    return {
        "username": data["nickname"].lower().replace(" ", "_"),
        "nickname": data["nickname"],
        "bio": data["bio"],
        "follower_count": data["follower_count"],
        "verified": False,
    }

def build_video(data, label):
    return {
        "id": f"diag_{label[:12]}".replace(" ", "_"),
        "desc": data["desc"],
        "play_count": data["plays"],
        "digg_count": data["likes"],
        "comment_count": data["comments"],
        "share_count": max(0, data["comments"] // 5),
        "tags": data.get("tags", []),
        "account_username": data.get("nickname", "test").lower().replace(" ", "_"),
    }

print("=" * 72)
print("P2 诊断: Issue #13 — QuickScore 商业白名单误杀分析")
print("=" * 72)
print(f"配置: min_score={config.min_score}, min_likes_no_comment={config.min_likes_no_comment}")
print(f"白名单 min_hits={config.commercial_whitelist_min_hits}")
print(f"个人号 penalty={config.personal_account_penalty}")
print()

false_positives = []   # should survive but eliminated
false_negatives = []   # should be eliminated but survived

for s in scenarios:
    account = build_account(s["account"])
    video = build_video(s["video"], s["label"])
    result = qs.score(video, account)

    # Determine outcome
    status = "PASS" if result.passed else "ELIM"
    correct = (result.passed == s["should_survive"])
    tag = "OK" if correct else "MISMATCH!"

    if s["should_survive"] and not result.passed:
        false_positives.append((s["label"], result))
    elif not s["should_survive"] and result.passed:
        false_negatives.append((s["label"], result))

    print(f"[{tag}] {s['label']}")
    print(f"  Score: {result.total:.1f} (relevance={result.product_relevance:.1f}, quality={result.video_quality:.1f})")
    print(f"  Whitelist: {result.commercial_whitelist_hit} | Personal: {result.is_personal_account}")
    if result.eliminated_reason:
        print(f"  Reason: {result.eliminated_reason}")
    if not correct:
        print(f"  !!! Expected {'PASS' if s['should_survive'] else 'ELIM'}, got {status}")
        print(f"  Risk: {s['risk']}")
    print()

# ═══════════════════════════════════════════════════════════
# 汇总
# ═══════════════════════════════════════════════════════════

print("=" * 72)
print("诊断总结")
print("=" * 72)
print(f"总场景: {len(scenarios)}")
print(f"假阳性 (应通过却被淘汰): {len(false_positives)}")
print(f"假阴性 (应淘汰却通过): {len(false_negatives)}")

if false_positives:
    print(f"\n!!! 误杀场景 ({len(false_positives)}):")
    for label, r in false_positives:
        print(f"  - {label}: score={r.total:.1f}, relevance={r.product_relevance:.1f}, "
              f"quality={r.video_quality:.1f}, whitelist={r.commercial_whitelist_hit}, "
              f"reason={r.eliminated_reason}")

if false_negatives:
    print(f"\n!!! 漏网场景 ({len(false_negatives)}):")
    for label, _ in false_negatives:
        print(f"  - {label}")

print(f"\n根因分析:")
print(f"  1. 关键词集不重叠: whitelist中的 [moq, odm, distributor, importer, exporter]")
print(f"     不在industry_keywords中 → 白名单豁免硬淘汰，但产品相关度得分极低")
print(f"  2. 个人号误判: 'daily' 模式词匹配了工厂号")
print(f"  3. 低互动硬淘汰: likes<5 AND comments=0 不适合 B2B 场景")
print(f"     (工厂展示视频常有价值但低互动)")
print(f"  4. play_benchmark=100K 对 B2B 太高: 多数工厂视频 <1K 播放")
