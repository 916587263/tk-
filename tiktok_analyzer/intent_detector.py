"""
TikTok 外贸行业对标视频发现系统 - 评论区采购意图识别器
从评论中识别外贸采购意向: 价格询盘、起订量、供应商搜索、工厂搜索、
定制需求、样品请求、批发请求、物流询问。

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
    # ── 8 大外贸采购意图类别 ──
    "price_inquiry": {
        "label": "💰 价格询盘",
        "label_en": "Price Inquiry",
        "description": "询问价格、报价、FOB/CIF/出厂价",
        "rules": [
            IntentRule(["how much", "price please", "best price", "price list",
                        "quote me", "send quote", "fob price", "exw price",
                        "cif price", "unit price", "per piece", "per kg",
                        "per ton", "whats the price", "cost per", "factory price",
                        "wholesale price", "bulk price", "what is the cost",
                        "price for this", "landed cost", "what price",
                        "give me price", "need price", "price inquiry"], regex=False),
            IntentRule(["多少钱", "什么价格", "报个价", "报价", "出厂价",
                        "批发价", "FOB价", "什么价", "价格多少", "询价",
                        "发一下价格", "报价单", "价格表", "求报价",
                        "多少钱一吨", "多少钱一公斤", "多少钱一个",
                        "单价", "含税价", "不含税", "美金价",
                        "人民币价格", "给个价格", "价位"], regex=False),
            IntentRule([r"(what|how)\s+(much|price|cost)", r"price\s+(for|of|per)\s",
                        r"\$\s*\d+", r"\d+\s*(dollars|usd|yuan|rmb| bucks)"],
                       regex=True, weight=1.2),
        ],
    },
    "moq_inquiry": {
        "label": "📦 起订量询问",
        "label_en": "MOQ Inquiry",
        "description": "询问最小起订量、起批量",
        "rules": [
            IntentRule(["moq", "minimum order", "minimum quantity", "min order",
                        "smallest order", "minimum purchase", "how many minimum",
                        "what is the moq", "minimum order quantity", "min qty",
                        "moq price", "can i order small", "small order ok",
                        "lowest quantity", "low moq", "start quantity",
                        "how many pieces minimum", "how much minimum",
                        "whats your moq", "any moq"], regex=False),
            IntentRule(["起订量", "最小起订", "最少订多少", "MOQ多少",
                        "起批量", "最小起订量", "最少要订多少", "最低起订",
                        "可以少订吗", "小批量可以吗", "几件起订",
                        "多少起批", "混批可以吗", "可以混批吗",
                        "最少订多少件", "最小批量", "最低订购量"], regex=False),
            IntentRule([r"moq\s*[\?:]?\s*\d*", r"minimum\s+(order|quantity)",
                        r"min(imum)?\s+(order|qty|quantity)"],
                       regex=True, weight=1.3),
        ],
    },
    "supplier_search": {
        "label": "🔍 供应商搜索",
        "label_en": "Supplier Search",
        "description": "寻找供应商、货源、合作伙伴",
        "rules": [
            IntentRule(["looking for supplier", "need supplier", "any supplier",
                        "find supplier", "reliable supplier", "trusted supplier",
                        "genuine supplier", "verified supplier", "supplier for",
                        "who supplies", "source this", "where to source",
                        "need vendor", "looking for vendor", "recommend supplier",
                        "good supplier", "best supplier", "supplier contact",
                        "long term supplier", "partner supplier"], regex=False),
            IntentRule(["找供应商", "求供应商", "货源", "找货源", "谁家有货",
                        "靠谱供应商", "推荐供应商", "供应商推荐", "长期合作",
                        "有没有供应商", "谁家做", "有做这个的吗", "供货商",
                        "寻找供应商", "供应商联系", "供应商信息",
                        "有没有做", "谁在做", "哪个供应商"], regex=False),
            IntentRule([r"(looking|searching)\s+for\s+(supplier|vendor|source)",
                        r"(need|want|find)\s+(a\s+)?(supplier|vendor|source)"],
                       regex=True, weight=1.2),
        ],
    },
    "manufacturer_search": {
        "label": "🏭 工厂搜索",
        "label_en": "Manufacturer Search",
        "description": "寻找工厂、厂家直销、制造商",
        "rules": [
            IntentRule(["manufacturer", "factory direct", "direct factory",
                        "looking for factory", "need factory", "find factory",
                        "who manufactures", "who makes this", "made by who",
                        "where is this manufactured", "factory price direct",
                        "source factory", "original manufacturer", "manufacturing",
                        "factory in china", "chinese factory", "direct manufacturer",
                        "not from reseller", "factory contact", "factory visit",
                        "from factory directly", "no middleman"], regex=False),
            IntentRule(["工厂", "厂家", "厂家直销", "源头工厂", "生产厂家",
                        "哪个工厂", "谁家工厂", "找工厂", "求工厂",
                        "工厂直供", "工厂直接", "不是二道贩子", "生产商",
                        "制造商", "代工厂", "原厂", "工厂地址", "工厂联系",
                        "有工厂吗", "是工厂吗", "工厂直销", "一手货源",
                        "自有工厂", "工厂批发", "没有中间商"], regex=False),
            IntentRule([r"(factory|manufacturer|manufacturing)\s+(direct|price|in)",
                        r"(who|where)\s+(manufactures?|makes?|produces?)",
                        r"(made|manufactured)\s+(by|in)"],
                       regex=True, weight=1.3),
        ],
    },
    "customization_request": {
        "label": "🎨 定制需求",
        "label_en": "Customization Request",
        "description": "询问定制、OEM、贴牌、改设计",
        "rules": [
            IntentRule(["oem", "odm", "custom logo", "custom design", "custom size",
                        "custom color", "custom packaging", "private label",
                        "own brand", "my brand", "branding", "custom print",
                        "personalized", "custom made", "customized", "customize",
                        "can you customize", "do you make custom", "custom order",
                        "bespoke", "tailor made", "white label", "your brand",
                        "print my logo", "logo on product", "custom specification",
                        "modify design", "change design", "special size"], regex=False),
            IntentRule(["定制", "定做", "OEM", "ODM", "贴牌", "代工",
                        "印logo", "加logo", "自定义", "来样加工", "来图加工",
                        "可定制吗", "能不能定制", "可以定制吗", "支持定制吗",
                        "改设计", "换包装", "订制", "专版", "开模",
                        "自己的品牌", "打自己logo", "换标", "改颜色",
                        "特殊规格", "非标定制", "个性化", "小批量定制",
                        "按需定制", "怎么定制", "定制流程"], regex=False),
            IntentRule([r"(custom|personalized?|bespoke|tailor)\s*(made|designed?|logo|brand|order|size|packaging)",
                        r"(can|do)\s+you\s+(customize|make|custom|do)\s",
                        r"(your|my|own)\s+(logo|brand|design|label)"],
                       regex=True, weight=1.2),
        ],
    },
    "sample_request": {
        "label": "📋 样品请求",
        "label_en": "Sample Request",
        "description": "索要样品、样板、确认品质",
        "rules": [
            IntentRule(["send sample", "sample available", "sample request",
                        "request sample", "need sample", "sample order",
                        "get sample", "how to get sample", "sample cost",
                        "free sample", "sample price", "sample fee",
                        "can you send sample", "sample before order",
                        "sample lead time", "sample shipping",
                        "want sample", "check sample", "sample quality",
                        "production sample", "pre-production sample"], regex=False),
            IntentRule(["样品", "样板", "打样", "拿样", "寄样",
                        "索样", "要样品", "能寄样品吗", "样品费",
                        "免费样品", "样品免费", "先看样品", "样品确认",
                        "付样品费", "样品运费", "可以先拿样吗",
                        "寄个样品", "提供样品", "看样", "要个样品",
                        "怎么拿样品", "样品怎么样", "有没有样品"], regex=False),
            IntentRule([r"(send|get|need|want|request)\s+(a\s+)?sample",
                        r"sample\s+(available|request|order|cost|price|shipping)"],
                       regex=True, weight=1.2),
        ],
    },
    "wholesale_request": {
        "label": "📊 批发请求",
        "label_en": "Wholesale Request",
        "description": "询问批发、大量采购、代理经销",
        "rules": [
            IntentRule(["wholesale", "bulk order", "bulk buy", "bulk purchase",
                        "large quantity", "bulk price", "distributor", "distribution",
                        "reseller", "resell", "become distributor", "agent wanted",
                        "wholesale price list", "bulk discount", "volume discount",
                        "wholesaler", "dealership", "franchise", "stock lot",
                        "in bulk", "wholesale inquiry", "bulk inquiry",
                        "buy in bulk", "order large", "container load"], regex=False),
            IntentRule(["批发", "大量采购", "批发价", "代理", "经销",
                        "批量", "整柜", "走量", "拿货", "批发商",
                        "想做代理", "招代理吗", "怎么代理", "可以做代理吗",
                        "大量要", "批发什么价", "批发行吗", "能不能批发",
                        "想批发", "怎么批发", "代理价", "分销",
                        "整批", "大批量", "长期大量", "一次拿多少"], regex=False),
            IntentRule([r"(wholesale|bulk)\s+(price|order|buy|purchase|inquiry)",
                        r"(become|be)\s+(a\s+)?(distributor|agent|reseller|dealer)",
                        r"(large|big|huge)\s+(quantity|order|volume)"],
                       regex=True, weight=1.2),
        ],
    },
    "shipping_request": {
        "label": "🚢 物流询问",
        "label_en": "Shipping Request",
        "description": "询问运输、运费、发货国家、时效",
        "rules": [
            IntentRule(["ship to", "shipping to", "delivery to", "send to",
                        "ship worldwide", "international shipping", "freight",
                        "shipping cost", "delivery time", "shipping time",
                        "how long to ship", "do you ship to", "can you ship to",
                        "express delivery", "air freight", "sea freight",
                        "door to door", "ddu", "ddp", "delivery to door",
                        "shipping agent", "forwarder", "port of destination",
                        "shipping method", "what courier", "tracking number",
                        "arrive at", "transit time", "customs clearance"], regex=False),
            IntentRule(["发货", "运费", "物流", "快递", "运输",
                        "能发到", "可以发", "海运", "空运", "铁路运输",
                        "多少钱运费", "运费多少", "包运费吗", "含运费吗",
                        "到门", "到港", "清关", "报关", "时效",
                        "多久到", "几天到", "能发货吗", "发什么快递",
                        "可以走海运吗", "整柜", "拼柜", "到岸", "到付",
                        "门到门", "双清", "双清包税", "关税"], regex=False),
            IntentRule([r"(ship|deliver|send)\s+(to|worldwide|international)",
                        r"(shipping|delivery|freight)\s+(cost|time|method|to)",
                        r"(do|can)\s+you\s+(ship|deliver|send)\s+(to|worldwide)"],
                       regex=True, weight=1.2),
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
        """从意图分析结果生成可读的外贸采购洞察"""
        insights = []
        summary = analysis_result.get("summary", {})

        if not summary:
            return insights

        intent_rate = summary.get("intent_rate", 0)
        total_intent = summary.get("comments_with_intent", 0)
        if intent_rate > 0.3:
            insights.append(f"🔥 高采购意图率 ({intent_rate:.0%}): {total_intent} 条评论含询盘/采购信号")
        elif intent_rate > 0.1:
            insights.append(f"📈 适度采购意图 ({intent_rate:.0%}): {total_intent} 条评论含潜在采购意向")
        else:
            insights.append(f"📉 采购意图较少 ({intent_rate:.0%}): 评论偏向互动/娱乐，可优化内容引导询盘")

        counts = summary.get("category_counts", {})

        if counts.get("price_inquiry", 0) > 3:
            insights.append(f"💰 {counts['price_inquiry']} 条价格询盘 — 建议在视频/简介明确标注价格区间或联系方式")
        if counts.get("moq_inquiry", 0) > 2:
            insights.append(f"📦 {counts['moq_inquiry']} 条起订量询问 — 建议视频中明确标注MOQ/Accept small order")
        if counts.get("supplier_search", 0) > 3:
            insights.append(f"🔍 {counts['supplier_search']} 条供应商搜索 — 增强品牌/工厂背景展示，提高信任度")
        if counts.get("manufacturer_search", 0) > 3:
            insights.append(f"🏭 {counts['manufacturer_search']} 条工厂搜索 — 强调工厂实拍/生产线/质检流程")
        if counts.get("customization_request", 0) > 3:
            insights.append(f"🎨 {counts['customization_request']} 条定制需求 — 明确展示OEM/ODM/贴牌能力")
        if counts.get("sample_request", 0) > 2:
            insights.append(f"📋 {counts['sample_request']} 条样品请求 — 在简介添加拿样流程/样品政策")
        if counts.get("wholesale_request", 0) > 3:
            insights.append(f"📊 {counts['wholesale_request']} 条批发请求 — 强调大批量折扣/代理政策")
        if counts.get("shipping_request", 0) > 3:
            insights.append(f"🚢 {counts['shipping_request']} 条物流询问 — 在视频/简介说明主要出口国家/运输方式")

        return insights
