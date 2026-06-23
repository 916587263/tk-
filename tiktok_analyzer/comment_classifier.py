"""
TikTok 外贸行业对标视频发现系统 — LLM 评论意图分类器 (阶段 3)

在新管道中位于"轻量评论采样 + QuickIntentScanner"之后、"FinalScore"之前。
仅对已通过阶段 2 快速意图扫描的视频进行深度评论分析和 LLM 分类。

核心功能:
  1. 批量 LLM 分类: 将 5-8 个视频的评论打包为一次 LLM 调用
  2. 逐条评论标注: has_intent, category, intensity, actionable, extracted_info
  3. 聚合为视频级指标: intent_ratio, intent_quality, intent_diversity, actionable_count

淘汰逻辑:
  - actionable_intent_count = 0 → 标记为"弱对标" (仍保留但降权)
"""
import json
import math
from typing import Optional
from dataclasses import dataclass, field
from openai import AsyncOpenAI
import httpx

from .logger import setup_logger

logger = setup_logger("comment_classifier")


# ═══════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════

@dataclass
class CommentClassifierConfig:
    """评论分类器配置"""

    enabled: bool = True

    # ── 批量设置 ──
    videos_per_batch: int = 6        # 每批处理的视频数
    max_comments_per_video: int = 40 # 每个视频最多传入 LLM 的评论数

    # ── LLM 设置 ──
    model: str = "gpt-4o-mini"       # 分类任务用 mini 即可, 降低成本
    temperature: float = 0.1         # 低温度, 确保分类一致性
    max_tokens: int = 4000

    # ── 意图类别 (与 IntentDetector 保持一致) ──
    intent_categories: list = field(default_factory=lambda: [
        {"key": "price_inquiry", "label": "价格询盘", "description": "询问价格/报价/费用"},
        {"key": "moq_inquiry", "label": "起订量询问", "description": "询问最小起订量/MOQ"},
        {"key": "supplier_search", "label": "供应商搜索", "description": "寻找供应商/货源"},
        {"key": "manufacturer_search", "label": "工厂搜索", "description": "寻找工厂/生产商/OEM"},
        {"key": "customization_request", "label": "定制需求", "description": "OEM/ODM/定制/贴牌需求"},
        {"key": "sample_request", "label": "样品请求", "description": "索要样品/打样"},
        {"key": "wholesale_request", "label": "批发请求", "description": "批量采购/批发/囤货"},
        {"key": "shipping_request", "label": "物流询问", "description": "国际物流/运费/运输"},
    ])

    # ── 后分类轻量淘汰 (阶段 3.5) ──
    # 在 CommentClassifier 之后、FinalScore 之前,
    # 淘汰意图信号过弱的视频, 避免所有视频涌入 FinalScore
    intent_filter_enabled: bool = True
    intent_filter_min_ratio: float = 0.03   # intent_ratio < 3% → 淘汰
    intent_filter_min_actionable: int = 1   # actionable_intent_count < 1 → 淘汰
    # 两个条件同时满足才淘汰 (AND): intent_ratio 低 AND 无可转化商机


# ═══════════════════════════════════════════════════════════
# 结果数据结构
# ═══════════════════════════════════════════════════════════

@dataclass
class ClassifiedComment:
    """单条评论的分类结果"""
    comment_index: int = 0
    text: str = ""
    has_intent: bool = False
    category: Optional[str] = None
    intensity: float = 0.0          # 意图强度 0.0-1.0
    actionable: bool = False        # 是否可直接转化为商机
    extracted_info: dict = field(default_factory=dict)  # {product, quantity, price, ...}


@dataclass
class VideoClassifyResult:
    """单个视频的评论分类聚合结果"""
    video_id: str = ""
    total_comments: int = 0
    intent_comments: int = 0
    intent_ratio: float = 0.0       # 有意图评论占比
    intent_quality_score: float = 0.0  # 意图评论平均强度 (0-100)
    intent_diversity: int = 0       # 命中了多少个不同意图类别
    actionable_intent_count: int = 0  # 可转化商机数
    categories_hit: list = field(default_factory=list)  # 命中的类别列表
    classified_comments: list = field(default_factory=list)  # ClassifiedComment[]
    is_weak_reference: bool = False  # actionable=0 → 弱对标


@dataclass
class BatchClassifyResult:
    """批量分类总结果"""
    video_results: list = field(default_factory=list)  # VideoClassifyResult[]
    total_llm_calls: int = 0
    total_tokens_estimate: int = 0


# ═══════════════════════════════════════════════════════════
# LLM Prompt
# ═══════════════════════════════════════════════════════════

CLASSIFY_PROMPT = """你是一个 TikTok 外贸采购意向分析助手。请分析以下视频的评论，判断每条评论是否包含外贸采购意向。

## 意图类别
{intent_categories_desc}

## 输出格式
对每条评论，输出一个 JSON 对象:
```json
{{
  "video_index": 0,
  "comment_index": 0,
  "has_intent": true,
  "category": "price_inquiry",
  "intensity": 0.8,
  "actionable": true,
  "extracted_info": {{"product": "plastic bottles", "quantity": "1000 pcs"}}
}}
```

字段说明:
- has_intent: 是否包含外贸采购意向 (true/false)
- category: 意图类别 (从上述列表中选择, 无意图则为 null)
- intensity: 意图强度 (0.0-1.0), 明确采购需求=0.8+, 泛泛询问=0.3-0.5
- actionable: 是否可直接转化为商机 (有产品+数量/价格需求=true)
- extracted_info: 提取的关键信息 (产品名/数量/价格/地区等)，无则为 null

## 待分析评论
{comments_json}

请输出 JSON 数组，每条评论一个元素。只输出 JSON，不要其他文字。"""


# ═══════════════════════════════════════════════════════════
# 评论分类器
# ═══════════════════════════════════════════════════════════

class CommentClassifier:
    """LLM 驱动的评论意图分类器

    将阶段 2 筛选出的视频的深度评论批量送入 LLM 进行分类,
    输出结构化意图标注和视频级聚合指标。

    用法:
        cfg = CommentClassifierConfig()
        classifier = CommentClassifier(api_key, cfg, base_url, proxy)
        results = await classifier.classify_videos(intent_videos)
        # intent_videos 中每条视频需含: id, comments (深度抓取的评论列表)
        # 返回: BatchClassifyResult
    """

    def __init__(
        self,
        api_key: str,
        config: CommentClassifierConfig = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        proxy: Optional[str] = None,
    ):
        self.config = config or CommentClassifierConfig()
        self.model = model or self.config.model

        # 构建 httpx 客户端
        self._http_client = None
        if proxy:
            self._http_client = httpx.AsyncClient(proxy=proxy)
            logger.info("CommentClassifier 使用代理: %s", proxy)

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=self._http_client,
        )

        # 构建意图类别描述
        self._intent_desc = "\n".join(
            f"- {c['key']}: {c['label']} ({c['description']})"
            for c in self.config.intent_categories
        )

    async def close(self):
        """关闭 HTTP 客户端"""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    # ── 后分类轻量淘汰 (阶段 3.5) ──

    def filter_low_intent(
        self,
        deep_videos: list[dict],
        classify_map: dict,
    ) -> tuple[list[dict], list[dict]]:
        """在 CommentClassifier 之后淘汰意图信号过弱的视频

        淘汰条件 (AND, 两条同时满足才淘汰):
          1. intent_ratio < intent_filter_min_ratio
          2. actionable_intent_count < intent_filter_min_actionable

        这比 is_weak_reference (仅检查 actionable=0) 更精准:
        is_weak_reference 只看 actionable, 但有些视频 intent_ratio
        很高即使没有 actionable 也有对标价值。
        此过滤器仅淘汰"意图率极低 + 无可转化商机"的视频。

        Returns:
            (passed_videos, eliminated_videos)
        """
        if not self.config.intent_filter_enabled:
            return deep_videos, []

        passed, eliminated = [], []
        for v in deep_videos:
            vid = v.get("id", "")
            vr = classify_map.get(vid)
            if vr is None:
                # 无分类结果 → 保留 (降级安全)
                passed.append(v)
                continue

            ratio = vr.intent_ratio
            actionable = vr.actionable_intent_count

            if (
                ratio < self.config.intent_filter_min_ratio
                and actionable < self.config.intent_filter_min_actionable
            ):
                v["_intent_filtered_out"] = True
                v["_intent_filter_reason"] = (
                    f"intent_ratio({ratio:.4f}) < {self.config.intent_filter_min_ratio} "
                    f"AND actionable({actionable}) < {self.config.intent_filter_min_actionable}"
                )
                eliminated.append(v)
                logger.debug(
                    "阶段 3.5 淘汰: vid=%s ratio=%.4f actionable=%d",
                    vid, ratio, actionable,
                )
            else:
                passed.append(v)

        return passed, eliminated

    # ── 主入口 ──

    async def classify_videos(self, videos: list[dict]) -> BatchClassifyResult:
        """对一批视频的评论进行批量 LLM 分类

        Args:
            videos: 视频列表, 每条需含:
                id, account_username, comments (list[dict] 深度评论)

        Returns:
            BatchClassifyResult
        """
        if not videos:
            return BatchClassifyResult()

        # 分批
        batch_size = self.config.videos_per_batch
        batches = [videos[i:i+batch_size] for i in range(0, len(videos), batch_size)]

        all_video_results = []
        total_llm_calls = 0
        total_tokens = 0

        for batch_idx, batch in enumerate(batches):
            logger.info(
                "LLM 评论分类 批次 %d/%d: %d 条视频",
                batch_idx + 1, len(batches), len(batch)
            )

            try:
                batch_results = await self._classify_batch(batch, batch_idx)
                all_video_results.extend(batch_results)
                total_llm_calls += 1
                # 粗略估算 token
                total_tokens += self._estimate_tokens(batch)
            except Exception as e:
                logger.error("批次 %d 分类失败: %s, 使用空结果降级", batch_idx + 1, e)
                # 降级: 所有视频标记为无意图
                for v in batch:
                    all_video_results.append(self._empty_result(v))

        return BatchClassifyResult(
            video_results=all_video_results,
            total_llm_calls=total_llm_calls,
            total_tokens_estimate=total_tokens,
        )

    # ── 批量调用 LLM ──

    async def _classify_batch(self, videos: list[dict], batch_idx: int) -> list[VideoClassifyResult]:
        """调用 LLM 对一批视频的评论进行分类"""
        # 构建评论列表 (带 video_index 和 comment_index)
        all_comments = []
        for vi, v in enumerate(videos):
            comments = v.get("comments", []) or v.get("_deep_comments", [])
            # 限制每个视频的评论数
            limited = comments[:self.config.max_comments_per_video]
            for ci, c in enumerate(limited):
                all_comments.append({
                    "video_index": vi,
                    "comment_index": ci,
                    "text": (c.get("text") or "")[:300],  # 截断长评论
                })

        if not all_comments:
            return [self._empty_result(v) for v in videos]

        # 构建 prompt
        comments_json = json.dumps(all_comments, ensure_ascii=False, indent=1)
        prompt = CLASSIFY_PROMPT.format(
            intent_categories_desc=self._intent_desc,
            comments_json=comments_json,
        )

        # 调用 LLM
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        classified = self._parse_llm_response(content, len(videos))

        # 聚合为 VideoClassifyResult
        return self._aggregate_by_video(videos, classified)

    # ── 解析 LLM 响应 ──

    @staticmethod
    def _parse_llm_response(content: str, video_count: int) -> list[ClassifiedComment]:
        """解析 LLM 返回的 JSON → ClassifiedComment 列表"""
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("LLM 响应 JSON 解析失败, 返回空结果")
            return []

        # 支持多种格式: 直接数组 / {"classifications": [...]} / {"results": [...]}
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("classifications", data.get("results", []))
        else:
            items = []

        results = []
        for item in items:
            if not isinstance(item, dict):
                continue
            results.append(ClassifiedComment(
                comment_index=item.get("comment_index", 0),
                text=item.get("text", ""),
                has_intent=item.get("has_intent", False),
                category=item.get("category"),
                intensity=float(item.get("intensity", 0) or 0),
                actionable=item.get("actionable", False),
                extracted_info=item.get("extracted_info") or {},
            ))
        return results

    # ── 聚合 ──

    def _aggregate_by_video(
        self, videos: list[dict], classified: list[ClassifiedComment]
    ) -> list[VideoClassifyResult]:
        """将评论级分类结果聚合为视频级指标"""
        # 按 video_index 分组
        groups: dict[int, list[ClassifiedComment]] = {i: [] for i in range(len(videos))}
        for c in classified:
            # video_index 从 LLM 响应中提取不到时, 用 comment_index 推算
            vi = getattr(c, 'video_index', 0)
            if vi < len(videos):
                groups[vi].append(c)

        results = []
        for vi, v in enumerate(videos):
            vid = v.get("id", "")
            comments = groups.get(vi, [])

            if not comments:
                results.append(self._empty_result(v))
                continue

            total = len(comments)
            intent_comments = [c for c in comments if c.has_intent]
            intent_count = len(intent_comments)
            intent_ratio = intent_count / total if total > 0 else 0.0

            # 意图质量: 平均强度
            if intent_comments:
                avg_intensity = sum(c.intensity for c in intent_comments) / len(intent_comments)
                intent_quality = min(100.0, avg_intensity * 100)
            else:
                intent_quality = 0.0

            # 意图多样性: 命中了多少个不同类别
            categories_hit = list(set(
                c.category for c in intent_comments if c.category
            ))
            intent_diversity = len(categories_hit)

            # 可转化商机数
            actionable_count = sum(1 for c in intent_comments if c.actionable)

            # 弱对标判定
            is_weak = (actionable_count == 0)

            results.append(VideoClassifyResult(
                video_id=vid,
                total_comments=total,
                intent_comments=intent_count,
                intent_ratio=round(intent_ratio, 4),
                intent_quality_score=round(intent_quality, 1),
                intent_diversity=intent_diversity,
                actionable_intent_count=actionable_count,
                categories_hit=categories_hit,
                classified_comments=comments,
                is_weak_reference=is_weak,
            ))

        return results

    @staticmethod
    def _empty_result(video: dict) -> VideoClassifyResult:
        """生成空结果 (LLM 失败或无法分析时降级)"""
        return VideoClassifyResult(
            video_id=video.get("id", ""),
            total_comments=len(video.get("comments", []) or video.get("_deep_comments", [])),
            is_weak_reference=True,
        )

    # ── Token 估算 ──

    def _estimate_tokens(self, videos: list[dict]) -> int:
        """粗略估算一次 LLM 调用的 token 消耗"""
        total_chars = len(CLASSIFY_PROMPT)
        total_chars += len(self._intent_desc)
        for v in videos:
            comments = v.get("comments", []) or v.get("_deep_comments", [])
            for c in comments[:self.config.max_comments_per_video]:
                total_chars += len(c.get("text", ""))
        # 粗略: 1 token ≈ 4 字符 (中英文混合)
        return total_chars // 4
