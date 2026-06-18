"""
TikTok 竞争对手分析系统 - 评论区商业意图识别器
从评论中识别购买意向、产品询问、价格敏感、竞品对比、痛点反馈。

P5 优先级。基于关键词 + 正则规则的 NLP 模块，不依赖外部 API。
"""
import re
from typing import Optional
from dataclasses import dataclass, field
from collections import Counter

from .logger import setup_logger

logger = setup_logger("intent_detector")


# ═══════════════════════════════════════════════════════════
# 意图规则定义
# ═══════════════════════════════════════════════════════════

@dataclass
class IntentRule:
    """单条意图匹配规则"""
    patterns: list[str]          # 正则或关键词列表
    weight: float = 1.0          # 匹配置信度权重
    require_all: bool = False    # True=AND, False=OR
    regex: bool = False          # True=正则, False=关键词子串匹配


# ── 意图类别及对应规则 ──

INTENT_CATEGORIES: dict[str, dict] = {
    "purchase_intent": {
        "label": "🛒 购买意向",
        "label_en": "Purchase Intent",
        "description": "明确表示想购买、下单、入手",
        "rules": [
            IntentRule(["where can i buy", "where to buy", "i need this", "i want this",
                        "take my money", "shut up and take", "how much is", "price of this",
                        "link to buy", "order this", "purchase",
                        # B2B/工厂获客
                        "how to order", "minimum order", "moq", "wholesale",
                        "factory price", "bulk order", "send quote", "send me quote",
                        "catalog", "catalogue", "sample order", "want to buy",
                        "looking for supplier", "need supplier", "become distributor",
                        "import", "export to", "ship to my country", "do you ship",
                        "place order", "how can i order", "price list"], regex=False),
            IntentRule(["哪里买", "怎么买", "多少钱", "想要", "好想要", "想入手",
                        "下单", "求链接", "有链接吗", "哪里能买", "在哪买",
                        "我要买", "买买买", "链接发一下", "求购", "代购",
                        "这个卖吗", "怎么卖", "售价", "价格多少",
                        # B2B/工厂获客
                        "怎么订货", "起订量", "批发价", "出厂价", "大量采购",
                        "发报价", "有目录吗", "样品", "打样", "做代理",
                        "怎么拿货", "货源", "厂家", "工厂", "供应商",
                        "发一下价格", "报价单", "最小起订", "货代",
                        "怎么合作", "想批发", "跨境电商", "外贸"], regex=False),
            IntentRule(["link.*(product|buy|shop)", "where.*(buy|get|purchase|find)"],
                       regex=True, weight=1.5),
        ],
    },
    "product_inquiry": {
        "label": "❓ 产品询问",
        "label_en": "Product Inquiry",
        "description": "询问产品细节、规格、使用方法、成分",
        "rules": [
            IntentRule(["does this work", "is this good", "how to use", "what is this",
                        "what brand", "which product", "can i use", "is it worth",
                        "does it have", "ingredients", "size of", "color option",
                        "is this safe", "does it come with",
                        # B2B/工厂获客
                        "what material", "specification", "custom size", "oem",
                        "custom logo", "private label", "whats the weight",
                        "capacity", "thickness", "gsm", "what material used",
                        "custom design", "your own brand", "production time",
                        "lead time", "certification", "quality standard",
                        "can you make", "do you manufacture"], regex=False),
            IntentRule(["好用吗", "有用吗", "效果怎么样", "适合什么肤质", "什么成分",
                        "安全吗", "孕妇能用吗", "敏感肌能用吗", "怎么用", "使用方法",
                        "是什么牌子", "哪个牌子", "多大容量", "有几个颜色",
                        "会不会过敏", "能祛痘吗", "能美白吗", "好用不",
                        "推荐吗", "值得买吗", "效果好吗", "有用过吗",
                        # B2B/工厂获客
                        "什么材质", "什么材料", "规格", "尺寸", "厚度",
                        "能不能定制", "可以印logo吗", "打样要多久",
                        "有没有现货", "交期多久", "生产周期", "重量多少",
                        "承重", "环保吗", "什么工艺", "可以做OEM吗"], regex=False),
            IntentRule([r"(what|which)\s+(brand|product|one)", r"how\s+(to|do|does).*\buse\b",
                        r"(does|is|can)\s+(this|it)\s+\w+", r"recommend\s+(a|me|some)\s+\w+"],
                       regex=True),
        ],
    },
    "price_sensitivity": {
        "label": "💰 价格敏感",
        "label_en": "Price Sensitivity",
        "description": "关注价格、抱怨太贵、询问折扣、比价",
        "rules": [
            IntentRule(["too expensive", "overpriced", "waste of money", "not worth the price",
                        "cheaper", "discount", "coupon", "promo code", "affordable",
                        "costs too much", "rip off", "price drop", "on sale",
                        "better price", "cheaper alternative",
                        # B2B/工厂获客
                        "fob price", "exw", "cif", "shipping cost", "freight",
                        "container price", "bulk price", "wholesale price",
                        "unit price", "per piece", "per kg", "per ton",
                        "landed cost", "customs duty", "tax", "vat"], regex=False),
            IntentRule(["太贵了", "买不起", "不值这个价", "有优惠吗", "打折",
                        "便宜点", "有没有便宜的", "性价比", "好贵", "太贵",
                        "有没有活动", "什么时候降价", "有券吗", "羊毛",
                        "平替", "平替有吗", "贵是贵", "真贵",
                        # B2B/工厂获客
                        "FOB价", "出厂价多少", "含税吗", "运费多少",
                        "海运", "空运", "集装箱", "一个柜", "整柜",
                        "批发什么价", "量大优惠吗", "含运费吗",
                        "报关", "退税", "到岸价"], regex=False),
            IntentRule([r"\$\d+", r"\d+\s?(dollars|bucks|yuan|块钱|元)",
                        r"(discount|coupon|promo)\s+code", r"\d+%\s?off"],
                       regex=True, weight=1.5),
        ],
    },
    "comparison": {
        "label": "⚖️ 竞品对比",
        "label_en": "Competitor Comparison",
        "description": "与其他品牌/产品对比，替代品询问",
        "rules": [
            IntentRule(["vs", "versus", "compared to", "better than", "or should i get",
                        "instead of", "alternative to", "similar to", "dup for",
                        "which is better", "difference between"], regex=False),
            IntentRule(["比起", "对比", "选哪个", "哪个更好", "有什么区别",
                        "替代", "纠结", "二选一", "还是买", "不如买", "更推荐哪个",
                        "哪家好", "哪家强", "有别的厂家吗", "其他供应商",
                        "有没有更好的", "还有别的吗"], regex=False),
            IntentRule([r"跟.*比", r"和.*比.*哪个", r"还是.*好", r"和.*对比",
                        r"哪个.*(好|强|便宜|划算)", r".*还是.*的.*好"],
                       regex=True),
            IntentRule([r"(\w+)\s+vs\.?\s+(\w+)", r"which\s+(one|is)\s+(better|best)",
                        r"difference\s+between", r"(or|vs)\s+(the\s+)?\w+\s*$"],
                       regex=True),
        ],
    },
    "pain_point": {
        "label": "😤 用户痛点",
        "label_en": "Pain Point",
        "description": "使用体验差、质量问题、售后投诉、效果不满意",
        "rules": [
            IntentRule(["doesn't work", "broke after", "waste of", "disappointed",
                        "terrible", "horrible", "do not buy", "don't buy",
                        "scam", "fake", "poor quality", "defective", "allergic",
                        "side effect", "return policy", "customer service"], regex=False),
            IntentRule(["不好用", "没效果", "用了之后", "过敏了", "烂脸",
                        "差评", "千万别买", "别买", "踩雷", "雷品",
                        "不好", "垃圾", "后悔", "浪费", "假货",
                        "质量差", "坏了", "退货", "客服", "售后",
                        "失望", "被坑", "智商税", "没用", "骗人"], regex=False),
            IntentRule([r"(don'?t|do\s+not|never)\s+(buy|use|recommend)",
                        r"(broke|broken|damaged)\s+(after|in|within)",
                        r"(waste|worst|terrible|horrible)\s+(product|item|purchase)"],
                       regex=True, weight=1.5),
        ],
    },
    "recommendation_request": {
        "label": "🙋 推荐请求",
        "label_en": "Recommendation Request",
        "description": "求推荐、求建议、请求帮助决策",
        "rules": [
            IntentRule(["any recommendations", "what do you recommend", "suggest",
                        "should i get", "help me choose", "advice", "opinion",
                        "what would you", "looking for", "need suggestions",
                        # B2B/工厂获客
                        "reliable supplier", "trusted factory", "trusted manufacturer",
                        "good factory", "recommended supplier", "any factory",
                        "looking for manufacturer", "need partner", "long term",
                        "direct factory", "source factory", "genuine supplier",
                        "verified supplier", "who makes", "who manufactures"], regex=False),
            IntentRule(["求推荐", "推荐一下", "有什么好的", "有没有推荐的",
                        "建议买哪个", "帮我选", "大家觉得", "求分享",
                        "有人用过吗", "谁能推荐", "种草", "安利",
                        "有没有人", "想入", "该不该买", "会不会踩雷",
                        # B2B/工厂获客
                        "靠谱的厂家", "有没有靠谱的", "谁家做得好",
                        "推荐个供应商", "哪家靠谱", "有合作过的吗",
                        "谁家有做", "求靠谱工厂", "有没有工厂推荐"], regex=False),
            IntentRule([r"(any|some)\s+(recommendations?|suggestions?)",
                        r"(what|which)\s+(should|would)\s+(i|you)\s+(get|buy|choose)"],
                       regex=True),
        ],
    },
}


# ═══════════════════════════════════════════════════════════
# 意图检测器
# ═══════════════════════════════════════════════════════════

@dataclass
class IntentDetectorConfig:
    """商业意图检测配置"""
    enabled: bool = True
    min_confidence: float = 0.3             # 最低置信度阈值
    max_intents_per_comment: int = 3        # 每条评论最多返回的意图数
    enabled_categories: list[str] = field(default_factory=lambda: list(INTENT_CATEGORIES.keys()))
    custom_keywords: dict[str, list[str]] = field(default_factory=dict)  # 用户自定义关键词


class IntentDetector:
    """商业意图识别器

    用法:
        detector = IntentDetector()
        results = detector.analyze_comments(comments)
        # results = {
        #     "comments": [{...comment, "intents": [...]}],
        #     "summary": {"purchase_intent": 23, "product_inquiry": 45, ...},
        #     "top_intent_comments": [...],
        # }
    """

    def __init__(self, config: IntentDetectorConfig = None):
        self.config = config or IntentDetectorConfig()
        self._compile_rules()

    def _compile_rules(self):
        """预编译正则规则"""
        self._compiled = {}
        for cat_key in self.config.enabled_categories:
            if cat_key not in INTENT_CATEGORIES:
                continue
            cat = INTENT_CATEGORIES[cat_key]
            self._compiled[cat_key] = []
            for rule in cat["rules"]:
                compiled_rule = IntentRule(
                    patterns=rule.patterns.copy(),
                    weight=rule.weight,
                    require_all=rule.require_all,
                    regex=rule.regex,
                )
                # 预编译正则
                if rule.regex:
                    compiled_rule._compiled_patterns = [
                        re.compile(p, re.IGNORECASE) for p in rule.patterns
                    ]
                self._compiled[cat_key].append(compiled_rule)

    def detect(self, text: str) -> list[dict]:
        """检测单条评论的商业意图

        Returns:
            [
                {"category": "purchase_intent", "label": "🛒 购买意向", "confidence": 0.85, "matched": ["哪里买"]},
                ...
            ]，按置信度降序
        """
        if not text or not text.strip():
            return []

        text_lower = text.lower().strip()
        results = []

        for cat_key in self.config.enabled_categories:
            if cat_key not in self._compiled:
                continue
            cat_info = INTENT_CATEGORIES[cat_key]
            best_confidence = 0.0
            all_matches = []

            for rule in self._compiled[cat_key]:
                matched = []
                if rule.regex:
                    for pat in rule._compiled_patterns:
                        if pat.search(text_lower):
                            matched.append(pat.pattern)
                else:
                    for kw in rule.patterns:
                        if kw.lower() in text_lower:
                            matched.append(kw)

                if rule.require_all:
                    hit = len(matched) == len(rule.patterns)
                else:
                    hit = len(matched) > 0

                if hit:
                    # 置信度 = 基础命中分 + 多关键词匹配加分
                    base_conf = 0.6  # 至少命中一个关键词就有 0.6 基础置信度
                    match_ratio = len(matched) / max(len(rule.patterns), 1)
                    conf = min(1.0, (base_conf + 0.4 * match_ratio) * rule.weight)
                    if conf > best_confidence:
                        best_confidence = conf
                    all_matches.extend(matched)

            if best_confidence >= self.config.min_confidence:
                results.append({
                    "category": cat_key,
                    "label": cat_info["label"],
                    "confidence": round(best_confidence, 2),
                    "matched_keywords": all_matches[:10],  # 最多保留10个
                })

        # 按置信度降序，限制每评论最大意图数
        results.sort(key=lambda x: x["confidence"], reverse=True)
        return results[:self.config.max_intents_per_comment]

    def analyze_comments(self, comments: list[dict]) -> dict:
        """批量分析评论商业意图

        Returns:
            {
                "comments": [...],         # 原评论 + intents 字段
                "summary": {...},          # 各意图类别统计
                "top_intents": [...],      # 高置信度意图评论 (前20)
                "intent_distribution": {   # 按账号分布
                    "username1": {"purchase_intent": 3, ...}
                }
            }
        """
        intent_counts = Counter()
        intent_comments = {k: [] for k in self.config.enabled_categories}
        all_scored = []

        for c in comments:
            text = c.get("text", "")
            intents = self.detect(text)
            c["intents"] = intents
            c["has_intent"] = len(intents) > 0
            c["top_intent"] = intents[0]["category"] if intents else None
            c["top_intent_confidence"] = intents[0]["confidence"] if intents else 0.0

            for intent in intents:
                intent_counts[intent["category"]] += 1
                intent_comments[intent["category"]].append({
                    "username": c.get("username", ""),
                    "text": text[:200],
                    "confidence": intent["confidence"],
                    "video_id": c.get("video_id", ""),
                    "account_username": c.get("account_username", ""),
                })

            all_scored.append(c)

        # Top 高置信度意图评论
        top_intents = sorted(
            [c for c in all_scored if c.get("has_intent")],
            key=lambda x: x.get("top_intent_confidence", 0),
            reverse=True
        )[:20]

        # 按账号分布
        account_distribution = {}
        for c in all_scored:
            uname = c.get("account_username", "unknown")
            if uname not in account_distribution:
                account_distribution[uname] = Counter()
            for intent in (c.get("intents") or []):
                account_distribution[uname][intent["category"]] += 1

        # Convert Counters to dicts for JSON
        account_dist = {
            uname: dict(cnt) for uname, cnt in account_distribution.items()
        }

        summary = {
            "total_comments": len(comments),
            "comments_with_intent": sum(1 for c in all_scored if c.get("has_intent")),
            "intent_rate": round(
                sum(1 for c in all_scored if c.get("has_intent")) / max(len(comments), 1), 3
            ),
            "category_counts": dict(intent_counts),
            "category_percentages": {
                cat: round(cnt / max(len(comments), 1) * 100, 1)
                for cat, cnt in intent_counts.items()
            },
        }

        logger.info(
            "意图识别完成: %d/%d 评论含商业意图 (%.1f%%), 分布=%s",
            summary["comments_with_intent"], summary["total_comments"],
            summary["intent_rate"] * 100, dict(intent_counts)
        )

        return {
            "comments": all_scored,
            "summary": summary,
            "top_intents": top_intents,
            "account_distribution": account_dist,
        }

    def get_insights(self, analysis_result: dict) -> list[str]:
        """从意图分析结果生成可读的商业洞察"""
        insights = []
        summary = analysis_result.get("summary", {})

        if not summary:
            return insights

        intent_rate = summary.get("intent_rate", 0)
        if intent_rate > 0.3:
            insights.append(f"🔥 高商业意图率 ({intent_rate:.0%}): 评论区有强烈购买/询问信号")
        elif intent_rate > 0.1:
            insights.append(f"📈 适度商业意图 ({intent_rate:.0%}): 存在可转化的潜在客户")
        else:
            insights.append(f"📉 商业意图较弱 ({intent_rate:.0%}): 评论区偏向娱乐/社交互动")

        counts = summary.get("category_counts", {})

        if counts.get("purchase_intent", 0) > 5:
            insights.append(f"🛒 {counts['purchase_intent']} 条明确购买意向 — 建议优先关注对应产品")
        if counts.get("product_inquiry", 0) > 10:
            insights.append(f"❓ {counts['product_inquiry']} 条产品询问 — 考虑制作FAQ/科普内容")
        if counts.get("price_sensitivity", 0) > 5:
            insights.append(f"💰 {counts['price_sensitivity']} 条价格敏感评论 — 可能需要定价策略调整")
        if counts.get("pain_point", 0) > 5:
            insights.append(f"😤 {counts['pain_point']} 条用户痛点 — 产品改进/差评应对机会")
        if counts.get("comparison", 0) > 3:
            insights.append(f"⚖️ {counts['comparison']} 条竞品对比 — 了解竞争对手差异化优势")
        if counts.get("recommendation_request", 0) > 5:
            insights.append(f"🙋 {counts['recommendation_request']} 条推荐请求 — 种草/KOL合作机会")

        return insights
