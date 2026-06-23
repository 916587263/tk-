"""
TikTok 竞争对手分析系统 - OpenAI 分析模块
分析商业需求、采购需求、用户痛点
"""
import json
import asyncio
from typing import Optional
from openai import AsyncOpenAI
import httpx

from .logger import setup_logger

logger = setup_logger("analyzer")


ANALYSIS_PROMPT = """你是一位专业的 TikTok 市场分析专家。请分析以下 TikTok 竞争对手数据。

## 账号信息
{accounts}

## 视频数据（最近30条）
{videos}

## 热门评论（前200条）
{comments}

请从以下三个维度进行分析，输出 JSON 格式结果：

1. **business_needs**（商业需求）：目标受众在寻找什么产品或服务？他们的消费动机是什么？
2. **purchase_needs**（采购需求）：从评论和视频内容中，能看出哪些具体的购买意向？
3. **pain_points**（用户痛点）：用户遇到了哪些问题、抱怨、不满？

对于每个维度，请提供：
- 具体发现（带引用证据）
- 优先级评分（1-10）
- 可操作的商业建议

输出格式：
{{
  "analysis": {{
    "business_needs": [
      {{"finding": "...", "evidence": "...", "priority": 8, "suggestion": "..."}}
    ],
    "purchase_needs": [
      {{"finding": "...", "evidence": "...", "priority": 7, "suggestion": "..."}}
    ],
    "pain_points": [
      {{"finding": "...", "evidence": "...", "priority": 9, "suggestion": "..."}}
    ]
  }},
  "summary": "一句话总结核心发现"
}}
"""

# P6: 增强版 Prompt — 包含系统评分和意图识别结果
ANALYSIS_PROMPT_ENHANCED = """你是一位专业的 TikTok 市场分析专家。请分析以下 TikTok 竞争对手数据（已附系统自动评分和商业意图识别结果）。

## 🏆 账号排行（按综合评分降序，前{max_accounts}名）
{accounts_scored}

## 🔥 视频排行（按综合评分降序，前{max_videos}条）
{videos_scored}

## 💬 精选评论
{comments}

## 🧠 商业意图自动识别结果
{intent_summary}

## 📊 数据统计
- 总账号: {total_accounts}, 总视频: {total_videos}, 总评论: {total_comments}
- 评分等级分布（账号）: {account_tiers}
- 评分等级分布（视频）: {video_tiers}

请从以下六个维度进行分析，输出 JSON：

1. **business_needs**（商业需求）：目标受众在寻找什么产品或服务？他们的消费动机是什么？
2. **purchase_needs**（采购需求）：从评论和视频内容中，能看出哪些具体的购买意向？
3. **pain_points**（用户痛点）：用户遇到了哪些问题、抱怨、不满？
4. **market_gaps**（市场空白）：未被充分满足的需求、缺少的产品类型、内容空白
5. **competitor_insights**（竞品洞察）：头部竞争对手的策略、差异化优势、可模仿的成功模式
6. **actionable_strategy**（可执行策略）：基于以上分析的具体行动建议（内容策略+产品策略+定价策略）

每个发现请包含：
- finding: 发现
- evidence: 引用证据（可以引用系统的评分和意图识别数据）
- priority: 优先级 1-10
- confidence: 置信度 0.0-1.0
- suggestion: 可操作建议

输出格式：
{{
  "analysis": {{
    "business_needs": [
      {{"finding": "...", "evidence": "...", "priority": 8, "confidence": 0.9, "suggestion": "..."}}
    ],
    "purchase_needs": [...],
    "pain_points": [...],
    "market_gaps": [...],
    "competitor_insights": [...],
    "actionable_strategy": [...]
  }},
  "summary": "一句话总结核心发现",
  "market_opportunity_score": 85
}}
"""


class TikTokAnalyzer:
    """使用 OpenAI API 分析 TikTok 数据"""

    def __init__(self, api_key: str, base_url: Optional[str] = None, model: str = "gpt-4o",
                 proxy: Optional[str] = None):
        # 构建 httpx 客户端（支持代理）
        self._http_client = None
        if proxy:
            self._http_client = httpx.AsyncClient(proxy=proxy)
            logger.info("OpenAI 客户端使用代理: %s", proxy)

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=self._http_client,
        )
        self.model = model

    async def close(self):
        """关闭 HTTP 客户端，释放资源"""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    async def analyze(self, scraped_data: dict) -> dict:
        """分析抓取的 TikTok 数据"""
        logger.info("开始 OpenAI 分析...")

        accounts_str = json.dumps(
            [{k: a.get(k, "") for k in ["username", "nickname", "follower_count", "like_count", "bio"]}
             for a in scraped_data.get("accounts", [])[:20]],
            ensure_ascii=False, indent=2
        )

        videos_str = json.dumps(
            [{k: v.get(k, "") for k in ["desc", "tags", "digg_count", "comment_count", "share_count", "play_count"]}
             for v in scraped_data.get("videos", [])[:30]],
            ensure_ascii=False, indent=2
        )

        comments_sample = scraped_data.get("comments", [])[:200]
        comments_str = "\n".join([
            f"- [{c.get('username', '')}] {c.get('text', '')} (like:{c.get('likes', 0)})"
            for c in comments_sample[:200]
        ])

        prompt = ANALYSIS_PROMPT.format(
            accounts=accounts_str[:3000],
            videos=videos_str[:3000],
            comments=comments_str[:4000],
        )

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是一位 TikTok 市场分析专家。请始终以 JSON 格式输出。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=4000,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            result = json.loads(content)
            logger.info("OpenAI 分析完成")

            return result.get("analysis", result)

        except Exception as e:
            logger.error("OpenAI 分析失败: %s", e)
            return {
                "error": str(e),
                "business_needs": [{"finding": "分析失败", "evidence": str(e), "priority": 0, "suggestion": "请重试"}],
                "purchase_needs": [],
                "pain_points": [],
                "summary": f"分析出错: {e}"
            }

    async def analyze_enhanced(self, scraped_data: dict, intent_data: dict = None,
                                config: dict = None) -> dict:
        """P6: 增强版分析 — 使用系统评分和意图识别数据，产出6维度商业分析"""
        logger.info("开始增强版 OpenAI 分析...")

        cfg = config or {}
        max_accounts = cfg.get("max_accounts_in_prompt", 20)
        max_videos = cfg.get("max_videos_in_prompt", 30)
        max_comments = cfg.get("max_comments_in_prompt", 200)

        accounts = scraped_data.get("accounts", [])
        videos = scraped_data.get("videos", [])
        comments = scraped_data.get("comments", [])

        # 构建评分账号摘要（包含 score/tier）
        def _fmt_account(a: dict, i: int) -> str:
            score = a.get("score", "-")
            tier = a.get("tier", "?")
            return (
                f"  #{i+1} @{a.get('username','?')} | 粉丝:{a.get('follower_count',0):,} | "
                f"点赞:{a.get('like_count',0):,} | 评分:{score}({tier}级) | "
                f"简介:{ (a.get('bio','') or '')[:60] }"
            )
        accounts_scored = "\n".join([
            _fmt_account(a, i) for i, a in enumerate(accounts[:max_accounts])
        ])

        # 构建评分视频摘要
        def _fmt_video(v: dict, i: int) -> str:
            score = v.get("score", "-")
            tier = v.get("tier", "?")
            virality = v.get("virality", "-")
            return (
                f"  #{i+1} @{v.get('account_username','?')} | 播放:{v.get('play_count',0):,} | "
                f"点赞:{v.get('digg_count',0):,} | 评论:{v.get('comment_count',0):,} | "
                f"评分:{score}({tier}级) | 病毒系数:{virality} | "
                f"{ (v.get('desc','') or '')[:80] }"
            )
        videos_scored = "\n".join([
            _fmt_video(v, i) for i, v in enumerate(videos[:max_videos])
        ])

        # 精选评论（优先高意图评论）
        intent_comments = []
        other_comments = []
        for c in comments:
            if c.get("has_intent"):
                intent_comments.append(c)
            else:
                other_comments.append(c)
        selected_comments = intent_comments[:150] + other_comments[:50]
        comments_str = "\n".join([
            f"- [{c.get('username','')}] {c.get('text','')[:150]} "
            f"(like:{c.get('likes',0)}, 意图:{c.get('top_intent','无')})"
            for c in selected_comments[:max_comments]
        ])

        # 意图识别摘要
        intent_summary = "未启用"
        if intent_data:
            s = intent_data.get("summary", {})
            intent_summary = (
                f"含商业意图的评论占比: {s.get('intent_rate',0):.1%}\n"
                f"意图分布: {s.get('category_counts',{})}\n"
                f"各类占比: {s.get('category_percentages',{})}"
            )

        # 等级分布
        account_tiers = {}
        for a in accounts:
            t = a.get("tier", "?")
            account_tiers[t] = account_tiers.get(t, 0) + 1
        video_tiers = {}
        for v in videos:
            t = v.get("tier", "?")
            video_tiers[t] = video_tiers.get(t, 0) + 1

        prompt = ANALYSIS_PROMPT_ENHANCED.format(
            accounts_scored=accounts_scored[:4000],
            videos_scored=videos_scored[:4000],
            comments=comments_str[:5000],
            intent_summary=intent_summary[:1000],
            total_accounts=scraped_data.get("total_accounts", 0),
            total_videos=scraped_data.get("total_videos", 0),
            total_comments=scraped_data.get("total_comments", 0),
            account_tiers=str(account_tiers),
            video_tiers=str(video_tiers),
            max_accounts=max_accounts,
            max_videos=max_videos,
        )

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是一位 TikTok 市场分析专家，擅长从数据中提取商业洞察。请始终以 JSON 格式输出。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=4000,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            result = json.loads(content)
            logger.info("增强版 OpenAI 分析完成")

            analysis = result.get("analysis", result)
            analysis["summary"] = result.get("summary", "")
            analysis["market_opportunity_score"] = result.get("market_opportunity_score", None)
            return analysis

        except Exception as e:
            logger.error("增强版 OpenAI 分析失败: %s", e)
            return {
                "error": str(e),
                "business_needs": [],
                "purchase_needs": [],
                "pain_points": [],
                "market_gaps": [],
                "competitor_insights": [],
                "actionable_strategy": [],
                "summary": f"分析出错: {e}",
                "market_opportunity_score": None,
            }

    async def analyze_top_references(
        self, top_videos: list[dict], accounts: list[dict] = None
    ) -> dict:
        """阶段 5: 精简 AI 分析 — 仅处理 Top 20 对标参考视频

        相比 analyze_enhanced (6000+ tokens), 此方法仅传入 Top 视频的摘要数据,
        预计 input tokens ~1500-2500, 降低 60-70%。

        Args:
            top_videos: Top N 对标参考视频, 每条件含:
                desc, account_username, final_score, tier, tier_label,
                commercial_intent, product_relevance, video_quality_score,
                digg_count, comment_count, play_count,
                intent_summary (dict: {intent_ratio, top_categories, sample_comments})
            accounts: 账号列表 (用于补充行业信息)

        Returns:
            analysis dict
        """
        logger.info("开始精简 AI 分析 (Top %d 对标视频)...", len(top_videos))

        accounts = accounts or []
        acc_map = {a.get("username", ""): a for a in accounts}

        # 构建精简视频摘要
        lines = []
        for i, v in enumerate(top_videos[:20]):
            uname = v.get("account_username", "")
            acc = acc_map.get(uname, {})
            score = v.get("final_score", 0)
            tier = v.get("tier", "?")
            tier_label = v.get("tier_label", "")
            intent = v.get("commercial_intent", 0)

            line = (
                f"#{i+1} @{uname} | 评分:{score}({tier}级-{tier_label}) | "
                f"商业意图:{intent} | "
                f"播放:{v.get('play_count',0):,} | 点赞:{v.get('digg_count',0):,} | "
                f"评论:{v.get('comment_count',0):,}"
            )
            desc = (v.get("desc") or "")[:100]
            if desc:
                line += f"\n    描述: {desc}"
            bio = (acc.get("bio") or "")[:80]
            if bio:
                line += f"\n    账号简介: {bio}"

            # 意图摘要
            isum = v.get("intent_summary") or {}
            if isum:
                ratio = isum.get("intent_ratio", 0)
                cats = isum.get("top_categories", [])
                line += f"\n    采购意向: {ratio:.0%} | 类型: {', '.join(cats[:3])}"

            # 代表性评论 (最多 3 条)
            samples = isum.get("sample_comments", []) if isum else []
            if samples:
                line += "\n    代表评论:"
                for sc in samples[:3]:
                    line += f"\n      - [{sc.get('username','')}] {sc.get('text','')[:120]}"

            lines.append(line)

        videos_str = "\n\n".join(lines)

        # 账号行业概览
        account_summary = ""
        biz_accounts = [a for a in accounts if any(
            kw in (a.get("bio", "") + a.get("nickname", "")).lower()
            for kw in ["factory", "manufacturer", "supplier", "wholesale", "export",
                        "工厂", "厂家", "供应商"]
        )]
        if biz_accounts:
            account_summary = f"行业账号 {len(biz_accounts)} 个 (含工厂/供应商/批发商标识)"

        prompt = f"""你是一位 TikTok 外贸市场分析专家。以下是 TikTok 上最具商业参考价值的对标视频。

## 对标参考视频 (Top {len(top_videos[:20])})
{videos_str[:3000]}

## 账号概览
{account_summary}

请从以下维度分析，输出 JSON:
1. **business_needs**: 买家在寻找什么产品/服务? 消费动机是什么?
2. **purchase_needs**: 具体的采购意向? (价格/起订量/供应商/定制等)
3. **market_gaps**: 未被满足的需求? 缺少的产品/内容?
4. **competitor_insights**: 头部账号的成功策略? 可模仿的模式?
5. **actionable_strategy**: 内容策略 + 产品策略 + 定价策略建议

每个发现: {{"finding": "...", "evidence": "...", "priority": 1-10, "confidence": 0.0-1.0, "suggestion": "..."}}

输出格式:
{{{{"analysis": {{"business_needs": [...], "purchase_needs": [...], "market_gaps": [...], "competitor_insights": [...], "actionable_strategy": [...]}}, "summary": "...", "market_opportunity_score": 0-100}}}}
"""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是一位 TikTok 外贸市场分析专家。始终以 JSON 格式输出。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=3000,  # 精简输出
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            result = json.loads(content)
            logger.info("精简 AI 分析完成 (tokens: ~%d)", response.usage.total_tokens if response.usage else 0)

            analysis = result.get("analysis", result)
            analysis["summary"] = result.get("summary", "")
            analysis["market_opportunity_score"] = result.get("market_opportunity_score", None)
            return analysis

        except Exception as e:
            logger.error("精简 AI 分析失败: %s", e)
            return {
                "error": str(e),
                "business_needs": [],
                "purchase_needs": [],
                "market_gaps": [],
                "competitor_insights": [],
                "actionable_strategy": [],
                "summary": f"分析出错: {e}",
            }

    async def analyze_batch(self, scraped_data: dict, chunk_size: int = 50) -> dict:
        """分批分析大规模数据"""
        comments = scraped_data.get("comments", [])

        if len(comments) <= chunk_size:
            return await self.analyze(scraped_data)

        all_results = []
        for i in range(0, len(comments), chunk_size):
            chunk_data = {**scraped_data, "comments": comments[i:i + chunk_size]}
            result = await self.analyze(chunk_data)
            all_results.append(result)

        merged = {"business_needs": [], "purchase_needs": [], "pain_points": [], "summary": ""}
        for r in all_results:
            for key in ["business_needs", "purchase_needs", "pain_points"]:
                merged[key].extend(r.get(key, []))
        merged["summary"] = all_results[0].get("summary", "") if all_results else ""

        return merged
