"""
三级关键词扩展引擎 + 缓存

用法:
  from tiktok_analyzer.keyword_expander import KeywordExpander, KeywordCache

  cache = KeywordCache()
  expander = KeywordExpander(cache=cache)

  result = expander.expand(["non woven bag", "cotton tote"], tier="balanced")
  # result = {"keywords": [...], "tier": "balanced", "count": 16, ...}

Tier 说明:
  compact (≤5词):  原词 + 最核心修饰词 (factory/supplier/product)
  balanced (≤16词): compact + 扩展修饰词 (wholesale/price/buy/export...)
  full (≤45词):     balanced + 多修饰词组合 + 买家视角 + 长尾
"""

import json
import hashlib
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

from .logger import setup_logger

logger = setup_logger("keyword_expander")

BASE_DIR = Path(__file__).parent.parent
CACHE_DIR = BASE_DIR / "cache"
CACHE_FILE = CACHE_DIR / "keyword_cache.json"

# ============================================================
# 模板定义: 每级是修饰词的叠加，不是简单砍半
# ============================================================

# compact: 核心修饰词 — 直接对应外贸获客场景
COMPACT_MODIFIERS = [
    "{word}",
    "{word} factory",
    "{word} supplier",
    "{word} product",
    "{word} manufacturer",
]

# balanced: compact + 更多外贸/采购修饰词
BALANCED_MODIFIERS = COMPACT_MODIFIERS + [
    "{word} wholesale",
    "{word} price",
    "{word} buy",
    "{word} online",
    "buy {word}",
    "{word} for sale",
    "{word} export",
    "{word} company",
    "{word} machine",
    "{word} material",
    "{word} custom",
]

# full: balanced + 多修饰词组合 + 买家视角 + 长尾
FULL_MODIFIERS = BALANCED_MODIFIERS + [
    # 双修饰词组合
    "{word} factory price",
    "{word} factory supplier",
    "{word} wholesale supplier",
    "{word} wholesale price",
    "{word} manufacturer supplier",
    "{word} factory direct",
    "{word} bulk buy",
    "{word} bulk price",
    # 买家视角
    "looking for {word}",
    "where to buy {word}",
    "best {word}",
    "cheap {word}",
    "high quality {word}",
    "{word} review",
    "{word} unboxing",
    # 行业/地区修饰
    "{word} China",
    "{word} made in China",
    "China {word} factory",
    "China {word} manufacturer",
    "{word} USA",
    "{word} UK",
    "{word} near me",
    # 长尾
    "{word} small business",
    "{word} startup",
    "{word} sourcing",
    "how to find {word} supplier",
    "{word} factory tour",
    "{word} production process",
    "{word} manufacturing",
    "{word} OEM",
    "{word} ODM",
    "custom {word}",
    "{word} sample",
    "{word} MOQ",
]

# 每级上限 (config.yaml 可覆盖)
TIER_LIMITS = {
    "compact": 5,
    "balanced": 16,
    "full": 45,
}

TIER_MODIFIERS = {
    "compact": COMPACT_MODIFIERS,
    "balanced": BALANCED_MODIFIERS,
    "full": FULL_MODIFIERS,
}


class KeywordCache:
    """单文件 JSON 缓存，TTL 7 天"""

    def __init__(self, cache_file: Optional[Path] = None, ttl_days: int = 7):
        self.cache_file = cache_file or CACHE_FILE
        self.ttl_days = ttl_days
        self._data: dict = {"entries": {}}
        self._loaded = False

    def _ensure_loaded(self):
        if self._loaded:
            return
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                # 启动时清理过期条目
                self._clean_expired()
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("缓存文件损坏，重建: %s", e)
                self._data = {"entries": {}}
        self._loaded = True

    def _make_key(self, keywords: list[str], tier: str) -> str:
        """生成缓存键: 排序后关键词 + tier 的 hash"""
        normalized = "|".join(sorted(k.strip().lower() for k in keywords))
        raw = f"{normalized}|{tier}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _clean_expired(self):
        """删除过期条目"""
        cutoff = (datetime.now() - timedelta(days=self.ttl_days)).isoformat()
        expired = []
        for key, entry in self._data.get("entries", {}).items():
            if entry.get("cached_at", "") < cutoff:
                expired.append(key)
        for key in expired:
            del self._data["entries"][key]
        if expired:
            logger.info("清理 %d 条过期缓存", len(expired))
            self._save()

    def get(self, keywords: list[str], tier: str) -> Optional[dict]:
        self._ensure_loaded()
        key = self._make_key(keywords, tier)
        entry = self._data.get("entries", {}).get(key)
        if not entry:
            return None
        # 再次检查过期
        cutoff = (datetime.now() - timedelta(days=self.ttl_days)).isoformat()
        if entry.get("cached_at", "") < cutoff:
            del self._data["entries"][key]
            self._save()
            return None
        logger.info("缓存命中: %s (%s)", key, entry.get("cached_at"))
        return entry["result"]

    def set(self, keywords: list[str], tier: str, result: dict):
        self._ensure_loaded()
        key = self._make_key(keywords, tier)
        self._data.setdefault("entries", {})[key] = {
            "result": result,
            "cached_at": datetime.now().isoformat(),
        }
        self._save()

    def _save(self):
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.error("缓存写入失败: %s", e)


class KeywordExpander:
    """三级关键词扩展引擎"""

    def __init__(
        self,
        cache: Optional[KeywordCache] = None,
        tier_limits: Optional[dict] = None,
    ):
        self.cache = cache
        self.limits = tier_limits or TIER_LIMITS

    def expand(
        self,
        keywords: list[str],
        tier: str = "compact",
        use_cache: bool = True,
    ) -> dict:
        """
        扩展关键词列表

        Args:
            keywords: 原始关键词列表
            tier: compact | balanced | full
            use_cache: 是否使用缓存

        Returns:
            {
                "keywords": [...],        # 扩展后的完整列表
                "original": [...],         # 原始关键词
                "tier": "balanced",
                "count": 16,
                "added": 13,               # 新增词数
                "breakdown": {"factory": 3, "supplier": 3, ...},
                "from_cache": False
            }
        """
        tier = tier if tier in TIER_MODIFIERS else "compact"
        max_total = self.limits.get(tier, TIER_LIMITS[tier])

        # 命中缓存则直接返回
        if use_cache and self.cache:
            cached = self.cache.get(keywords, tier)
            if cached:
                cached["from_cache"] = True
                return cached

        modifiers = TIER_MODIFIERS[tier]

        # 按比例分配每个原始词的扩展配额
        n_input = len(keywords)
        per_word_limit = max(3, max_total // n_input)

        expanded = []
        seen = set()
        breakdown = {}

        for word in keywords:
            word = word.strip()
            if not word:
                continue

            count_for_word = 0
            for tmpl in modifiers:
                if count_for_word >= per_word_limit:
                    break
                candidate = tmpl.format(word=word)
                # 去重 + 长度限制 + TikTok 有效字符
                if self._is_valid(candidate) and candidate.lower() not in seen:
                    seen.add(candidate.lower())
                    expanded.append(candidate)
                    count_for_word += 1

                    # breakdown 统计
                    modifier_type = tmpl.replace("{word}", "*").replace(word, "*")
                    breakdown[modifier_type] = breakdown.get(modifier_type, 0) + 1

            # 确保原词一定在列表中
            if word not in expanded:
                if word.lower() not in seen:
                    seen.add(word.lower())
                    expanded.insert(0, word)

        # 如果总数超过上限，截断
        if len(expanded) > max_total:
            # 保留原词在前面
            originals = [w for w in expanded if w in keywords]
            rest = [w for w in expanded if w not in keywords]
            expanded = originals + rest[:max_total - len(originals)]

        result = {
            "keywords": expanded,
            "original": keywords,
            "tier": tier,
            "count": len(expanded),
            "added": len(expanded) - len(keywords),
            "breakdown": dict(sorted(breakdown.items(), key=lambda x: x[1], reverse=True)[:10]),
            "from_cache": False,
        }

        # 写入缓存
        if use_cache and self.cache:
            # 存入不含 from_cache 的副本
            to_cache = {k: v for k, v in result.items() if k != "from_cache"}
            self.cache.set(keywords, tier, to_cache)

        return result

    @staticmethod
    def _is_valid(word: str) -> bool:
        """检查关键词是否有效"""
        if not word or len(word) > 80:
            return False
        # TikTok 搜索不支持特殊字符
        banned = {"<", ">", "{", "}", "[", "]", "\\", '"', "'"}
        if any(c in word for c in banned):
            return False
        # 至少包含一个字母或中文字符
        has_alpha = any(c.isalpha() for c in word)
        has_cjk = any("一" <= c <= "鿿" for c in word)
        if not has_alpha and not has_cjk:
            return False
        return True
