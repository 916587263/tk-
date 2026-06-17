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
