# TikTok 外贸行业对标视频发现系统 — 项目索引

## 入口

- `tiktok_analyzer.py` — CLI / 主入口
- `app.py` — Flask Web 入口
- `config.yaml` — 全局配置

## 模块速查

| 文件 | 职责 | 输入 | 输出 |
|------|------|------|------|
| `tiktok_analyzer/scraper.py` | Playwright爬虫核心 (CDP隔离+新管道) | 关键词 | accounts/videos/comments (含 QuickScore+深度评论) |
| `tiktok_analyzer/unified_scorer.py` | 统一评分: QuickScorer (阶段1) + FinalScorer (阶段4) | videos/accounts + classify_result | quick_score / final_score + tier |
| `tiktok_analyzer/comment_classifier.py` | LLM批量评论意图分类 (阶段3) | deep_analyzed_videos | VideoClassifyResult[] (intent_ratio/quality/diversity) |
| `tiktok_analyzer/analyzer.py` | OpenAI分析 (阶段5, 精简) | top_reference_videos | analysis JSON |
| `tiktok_analyzer/keyword_expander.py` | 三级关键词扩展引擎 + 缓存 | keywords[] + tier | expanded_keywords[] + breakdown |
| `tiktok_analyzer/exporter.py` | CSV/MD导出 | scraped_data | accounts/videos/comments/reference_videos CSV + report.md |
| `tiktok_analyzer/network_collector.py` | API响应拦截采集层 (零DOM依赖) | page.on("response") XHR | 视频/账号/评论/用户视频 结构化数据 |
| `tiktok_analyzer/checkpoint.py` | SQLite断点续爬 | task_id | 进度+已抓取标记 |
| `tiktok_analyzer/proxy_pool.py` | 代理池轮换 | proxies.json | proxy config |
| `tiktok_analyzer/captcha.py` | 验证码检测 | page | True/False |
| `tiktok_analyzer/logger.py` | 日志模块 | name | Logger |

### DEPRECATED 模块 (被新管道取代)
| 文件 | 原职责 | 替代方案 |
|------|--------|----------|
| `account_filter.py` | 账号过滤 | QuickScore 阶段1 淘汰规则 |
| `account_scorer.py` | 账号评分 | FinalScore 账号聚合 |
| `video_scorer.py` | 视频评分+过滤 | QuickScore + FinalScore |
| `viral_detector.py` | 爆款发现 | 统一评分视频质量维度覆盖 |
| `intent_detector.py` | 意图检测(关键词) | QuickIntentScanner(阶段2) + CommentClassifier(阶段3 LLM) |
| `reference_video_scorer.py` | 对标参考评分 | FinalScorer (阶段4) |

## 新管道架构 (v2.0)

```
关键词 → KeywordExpander → expanded_keywords[]
  │
  ▼
阶段 0: 搜索 + 跨关键词视频/账号去重
  │
  ▼
阶段 1: QuickScore 无评论快速评分 (产品相关度 50% + 视频质量 50%)
         → 极低互动直接淘汰 → 个人/娱乐号降30分 → score<20淘汰
         → ~70-80% 视频在此淘汰
  │
  ▼
阶段 2: 轻量评论采样 (_sample_comments 8条, 不导航)
         → QuickIntentScanner → has_intent? 
         → 无: 淘汰 → 有: 进入阶段3
         → ~70% 在此淘汰
  │
  ▼
阶段 3: 深度评论抓取 (30-50条) + LLM批量意图分类 (3-5次调用)
         → 逐条评论: has_intent/category/intensity/actionable
         → 聚合视频指标: intent_ratio/quality/diversity/actionable_count
         → ~50% 在此淘汰 (actionable=0 → 弱对标)
  │
  ▼
阶段 4: FinalScore 终极统一评分 (产品30% + 质量30% + 商业意图40%)
         → Sigmoid压缩 → 分级 S/A/B/C/D → Top 20 对标参考视频
  │
  ▼
阶段 5: AI 分析总结 (仅Top 20, ~2000 tokens)
```

## 数据流 (精简)

```
关键词搜索 → 500-750条原始视频
  → 去重 (~500条)
  → QuickScore (~100-150条通过, ~70-80%淘汰)
  → 评论采样+QuickIntentScanner (~25-40条有意图, ~70%淘汰)
  → 深度抓取+LLM分类 (~10-20条最终候选, ~50%淘汰)
  → FinalScore → Top 20对标参考视频
  → AI总结 (1次LLM, ~2000 tokens)
```

## 数据库

- `checkpoints/{task_id}.db` — 断点续爬 (progress + scraped_items)
- `cookies/tiktok_cookies.json` — 登录态Cookie
- `cache/keyword_cache.json` — 关键词扩展结果缓存 (TTL 7天)

## 管道顺序 (app.py _run_analysis v2.0)

```
0. KeywordExpander (三级扩展 + 缓存, 硬上限10词) — 搜索前扩展关键词
1. scraper.run_analysis() — 内部 阶段0-3:
   阶段0: 搜索 + 跨关键词视频/账号去重
   阶段1: QuickScore 无评论快速评分 + 激进淘汰 (~70-80%)
   阶段2: 轻量评论采样 (8条) + QuickIntentScanner → A/B分流
   阶段3: 深度评论抓取 (30-50条) — 仅 ~10% 视频
2. CommentClassifier — LLM批量评论意图分类 (3-5次调用)
3. FinalScorer — 三权重终极评分 (30/30/40) + Top 20 对标参考
4. TikTokAnalyzer — 精简AI总结 (仅Top 20, ~2000 tokens)
5. Exporter
```

## 关键数据结构

### account
```
username, nickname, avatar, verified, follower_count, following_count,
video_count, like_count, bio, region, language, location, sec_uid, uid, url
+ reference_value, reference_video_count, high_tier_video_count (from FinalScorer)
```

### video
```
id, desc, create_time, duration, play_count, digg_count, comment_count,
share_count, url, tags[], account_username, music, _source_keywords[]
+ quick_score, product_relevance, video_quality_score, industry_hits (from QuickScorer 阶段1)
+ _deep_comments[] (阶段3 深度评论, 仅供 CommentClassifier 消费)
+ final_score, tier, tier_label, commercial_intent, score_breakdown (from FinalScorer 阶段4)
+ intent_ratio, intent_quality_score, intent_diversity, actionable_intent_count,
  categories_hit[], is_weak_reference (from CommentClassifier 阶段3)
+ is_top_reference, reference_rank (from FinalScorer Top 20 选择)
+ intent_summary (dict: intent_ratio/top_categories/sample_comments, for AI analysis)
```

### comment
```
video_id, text, username, likes, time, account_username
+ has_intent, top_intent, top_intent_confidence, actionable (from CommentClassifier LLM)
  意图类别: price_inquiry, moq_inquiry, supplier_search, manufacturer_search,
           customization_request, sample_request, wholesale_request, shipping_request
```

## 统一评分公式

### QuickScore (阶段 1 — 无评论)
```
产品相关度 = 行业关键词在 desc/tags/bio/nickname 中的加权命中 (0-100)
视频质量   = (log10(plays/100K)×50+50 + min(100, 50+25×log10(engagement_rate/5%))) / 2
quick_score = 0.50 × 产品相关度 + 0.50 × 视频质量
淘汰: score < 20 | likes<5 AND comments=0 | 零互动 | 个人号降30分
```

### FinalScore (阶段 4 — 首次引入商业意图)
```
商业意图综合 = 0.4×intent_ratio_norm + 0.4×intent_quality_norm + 0.2×intent_diversity_norm
raw = 0.30 × 产品相关度 + 0.30 × 视频质量 + 0.40 × 商业意图综合
final_score = sigmoid(raw, center=50, steepness=15)
分级: S≥80, A≥60, B≥40, C≥20, D<20
```

## 遗留问题

1. ✅ 评论网络拦截 — `page.on("response")` 被动拦截 comment/list XHR (2026-06-18)
2. ✅ 视频互动数据 — `enrich_top: 20` SIGI_STATE 精确数据 (已验证)
3. ✅ CDP 窗口干扰 — 单页复用，不 new_page() (2026-06-22)
4. ✅ CDP 降级 + Cookie 持久化 — try/except fallback + 统一 browser_profile (2026-06-22)
5. 非CDP模式反爬检测严格
6. ✅ 三级关键词扩展 (compact/balanced/full) + 缓存 (2026-06-22)
7. ✅ 异步可中断 (2026-06-22)
8. ✅ 管道策略重构 v2.0 — QuickScore + 两阶段评论 + FinalScore 统一评分 (2026-06-23)
9. ✅ 管道验证框架 — validate_pipeline.py: 10章对比报告 + 漏斗分析 + Token拆解 + 误杀溯源 (2026-06-23)
10. 关键词扩展优化 — LLM驱动 + 聚类去重 + 搜索价值评分 (暂停)
11. ✅ 管道级集成测试 — tests/test_pipeline_e2e.py: 16 tests/68 assertions, 覆盖 QuickScore→QuickIntent→Classify→FinalScore (2026-06-24)
12. 历史对比/趋势追踪 — 无法对比多次分析结果 (暂停)
13. 🔧 高价值误杀率优化 — 部分修复: industry_keywords 同步白名单 5 词 + personal_patterns 移除 daily, 误杀从 5/8 降至 1/8. 剩余: min_likes_no_comment=5 硬淘汰阈值仍过严 (2026-06-24)

## 修复记录 (2026-06-24)

- **P0** `comment_classifier.py`: ClassifiedComment 新增 video_index 字段，修复阶段 3 LLM 分类结果 83% 错配 bug
- **P1-A** `tiktok_analyzer.py`: CLI 入口统一使用新管道 v2.0 (CommentClassifier + FinalScorer + analyze_top_references)
- **P1-B** `tests/test_pipeline_e2e.py`: 新增管道端到端集成测试 (16 tests / 68 assertions)
- **P2** `config.yaml`: industry_keywords 新增 odm/moq/distributor/importer/exporter; personal_account_patterns 移除 daily

## 实验与验证工具

- `validate_pipeline.py` — 新旧管道对比验证框架
  - `py validate_pipeline.py -k 'kw1, kw2'` — 实时验证 (需浏览器)
  - `py validate_pipeline.py --live -k 'kw1'` — 强制实时
  - `py validate_pipeline.py -d data/xxx` — 离线验证
  - 输出: `data/validate_{ts}/validation_report.md` (10章完整报告)
  - 验证维度: 视频质量 / 意图命中率 / 评论耗时 / Token拆解 / 误杀率+阶段分布 / 漏斗分析 / 综合诊断
- `experiment_sampling.py` — QuickIntent 评论采样策略对比实验 (4策略 × N视频)
  - 比较: first_n / top_and_latest / random_8 / random_12
  - 输出: 高价值误杀率 / 阶段3进入量 / Token预估 / 耗时 / Top视频质量
- `verify_quickintent.py` — QuickIntent 淘汰验证 (采样 vs 全量评论对比)
  - 从淘汰视频随机抽样，对比 8条采样 vs 30-50条全量的商业信号命中
  - 输出: 漏检率 / 漏检视频清单 / 被遗漏的具体商业句子
- `tests/test_video_index_fix.py` — P0 回归: ClassifiedComment video_index 全链路保留 (4 tests / 15 assertions)
- `tests/test_pipeline_e2e.py` — 管道集成: QuickScore → QuickIntent → Classify → FinalScore (16 tests / 68 assertions)
- `tests/diagnose_issue13.py` — Issue #13 诊断: B2B 场景误杀量化分析
- `test_backend.py` — 后端组件单元测试 (Mock数据)
- `test_cdp.py` — CDP端到端测试 (通过 /api/start + /api/progress SSE)

## 配置热区

- `config.yaml → quick_scorer` — QuickScore 权重/阈值 (阶段1)
- `config.yaml → comment_classifier` — LLM评论分类配置 (阶段3)
- `config.yaml → final_scorer` — FinalScore 三权重 (30/30/40) 和分级阈值 (阶段4)
- `config.yaml → keyword_expansion.default_tier` — 扩展级别 (compact/balanced/full)
- `config.yaml → keyword_expansion.cache_ttl_days` — 扩展缓存有效期
- `config.yaml → industry_presets` — 6大行业预设关键词
- `config.yaml → video_scraping.enrich_top` — 视频详情深度抓取数量
