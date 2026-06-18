# TikTok 外贸行业对标视频发现系统 — 项目索引

## 入口

- `tiktok_analyzer.py` — CLI / 主入口
- `app.py` — Flask Web 入口
- `config.yaml` — 全局配置

## 模块速查

| 文件 | 职责 | 输入 | 输出 |
|------|------|------|------|
| `tiktok_analyzer/scraper.py` | Playwright爬虫核心 | 关键词 | accounts/videos/comments |
| `tiktok_analyzer/analyzer.py` | OpenAI分析 | scraped_data | analysis JSON |
| `tiktok_analyzer/account_filter.py` | 账号过滤(P0) | accounts[] | kept[], removed[] |
| `tiktok_analyzer/account_scorer.py` | 账号评分(P1) | account{} | +score/tier/breakdown |
| `tiktok_analyzer/video_scorer.py` | 视频评分+过滤(P3) | video{} | +score/tier/virality |
| `tiktok_analyzer/viral_detector.py` | 爆款发现 | videos[]+accounts[] | top_viral+benchmarks |
| `tiktok_analyzer/intent_detector.py` | 外贸采购意图识别(P5) | comments[] | +intents/has_intent (8类外贸意图) |
| `tiktok_analyzer/reference_video_scorer.py` | 对标参考评分 | videos[]+comments[]+accounts[] | +reference_score/tier/breakdown + top_reference |
| `tiktok_analyzer/exporter.py` | CSV/MD导出 | scraped_data | accounts/videos/comments/reference_videos CSV + report.md |
| `tiktok_analyzer/checkpoint.py` | SQLite断点续爬 | task_id | 进度+已抓取标记 |
| `tiktok_analyzer/proxy_pool.py` | 代理池轮换 | proxies.json | proxy config |
| `tiktok_analyzer/captcha.py` | 验证码检测 | page | True/False |
| `tiktok_analyzer/logger.py` | 日志模块 | name | Logger |

## 数据流

```
关键词 → search_accounts() → accounts[]
  → extract_account_info() → account{}
  → extract_videos() → videos[]
  → extract_comments() → comments[]
  → 前置过滤层(digg<10或comment=0跳过)
  → AccountFilter → AccountScorer → VideoFilter → VideoScorer
  → ViralDetector → IntentDetector → ReferenceVideoScorer
  → TikTokAnalyzer → export_csv/markdown
```

## 管道顺序 (app.py _run_analysis)

```
1. scraper.run_analysis()
2. AccountFilter (P0)
3. AccountScorer (P1)
4. VideoFilter (P3)
5. VideoScorer (P3)
6. ViralDetector
7. IntentDetector (8类外贸采购意图)
8. ReferenceVideoScorer (四维对标参考评分 + 门控过滤)
9. TikTokAnalyzer (P6)
10. Exporter
```

## 数据库

- `checkpoints/{task_id}.db` — 断点续爬 (progress + scraped_items)
- `cookies/tiktok_cookies.json` — 登录态Cookie
- `browser_profile/` — Playwright persistent context

## 关键数据结构

### account
```
username, nickname, avatar, verified, follower_count, following_count,
video_count, like_count, bio, region, language, location, sec_uid, uid, url
+ score, tier, tier_label, score_breakdown (from AccountScorer)
+ video_stats (from VideoScorer.aggregate_by_account)
+ viral_profile (from ViralDetector)
```

### video
```
id, desc, create_time, duration, play_count, digg_count, comment_count,
share_count, url, tags[], account_username, music
+ score, tier, tier_label, virality, score_breakdown (from VideoScorer)
+ engagement_rank, is_viral, is_global_top10, engagement_percentile (from ViralDetector)
+ purchase_intent_comments, purchase_intent_ratio, reference_score,
  reference_tier, reference_tier_label, reference_breakdown,
  is_top_reference, reference_rank (from ReferenceVideoScorer)
```

### comment
```
video_id, text, username, likes, time, account_username
+ intents[], has_intent, top_intent, top_intent_confidence (from IntentDetector)
  意图类别: price_inquiry, moq_inquiry, supplier_search, manufacturer_search,
           customization_request, sample_request, wholesale_request, shipping_request
```

## 对标参考评分公式

```
reference_score = sigmoid_compress(
  0.25 * video_engagement + 0.25 * comment_quality +
  0.35 * intent_ratio + 0.15 * account_relevance
)

硬性门控: digg>=10 AND comment>=5 AND purchase_intent_comments>0
不达标视频不评分，不进入 reference_videos.csv
```

## 遗留问题

1. ✅ 评论网络拦截 — `page.on("response")` 被动拦截 comment/list XHR (2026-06-18)
2. ✅ 视频互动数据 — `enrich_top: 20` SIGI_STATE 精确数据 (已验证)
3. 非CDP模式反爬检测严格

## 配置热区

- `config.yaml → account_filter.enabled` — 开启账号过滤
- `config.yaml → video_scraping.enrich_top` — 视频详情深度抓取数量
- `config.yaml → account_scorer.follower_benchmark` — B2B评分基准
- `config.yaml → reference_video_scorer.intent_ratio_benchmark` — 采购意向基准
- `config.yaml → intent_detector.enabled_categories` — 8类外贸意图开关
