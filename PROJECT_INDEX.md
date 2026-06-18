# TikTok 工厂获客分析系统 — 项目索引

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
| `tiktok_analyzer/intent_detector.py` | 评论意图识别(P5) | comments[] | +intents/has_intent |
| `tiktok_analyzer/exporter.py` | CSV/MD导出 | scraped_data | accounts/videos/comments/viral CSV + report.md |
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
  → AccountFilter → AccountScorer → VideoFilter → VideoScorer
  → ViralDetector → IntentDetector
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
7. IntentDetector (P5)
8. TikTokAnalyzer (P6)
9. Exporter
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
```

### comment
```
video_id, text, username, likes, time, account_username
+ intents[], has_intent, top_intent, top_intent_confidence (from IntentDetector)
```

## 遗留问题

1. 评论DOM提取在CDP模式下为0 → 需网络拦截方案
2. 视频互动数据需访问详情页(enrich_top)
3. 非CDP模式反爬检测严格

## 配置热区

- `config.yaml → account_filter.enabled` — 开启账号过滤
- `config.yaml → video_scraping.enrich_top` — 视频详情深度抓取数量
- `config.yaml → account_scorer.follower_benchmark` — B2B评分基准
