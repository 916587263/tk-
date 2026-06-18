# TODO — TikTok 外贸行业对标视频发现系统

## 当前状态 (2026-06-18)

### ✅ 已完成

- [x] **P0-1** 评论抓取修复 — `_extract_comments_from_js()` + wait_for_selector + 扩展selector
- [x] **P0-2** B2B商业意图识别 → **转型为8类外贸采购意图**
- [x] **P0-3** 地区筛选增强 — language_whitelist + require_contact + region+location联合匹配
- [x] **P0-4** B2B账号评分校准 — follower_benchmark=10K, like_benchmark=100K
- [x] **P1** 账号评分器 account_scorer.py
- [x] **P3** 视频评分器 video_scorer.py + VideoFilter
- [x] **爆款发现** viral_detector.py
- [x] **P5** 意图检测转型: 6类B2B意图 → 8类外贸采购意图 (2026-06-18)
- [x] **对标参考评分器** reference_video_scorer.py — 四维加权评分 + 门控过滤 (2026-06-18)
- [x] **P6** AI增强分析 analyzer.py (6维度)
- [x] **导出增强** exporter.py (reference_videos.csv / 对标参考章节 / 采购意向分析)
- [x] **CLI入口** tiktok_analyzer.py (v2.0 外贸行业对标)
- [x] **项目索引** PROJECT_INDEX.md
- [x] **评论网络拦截修复** — `page.on("response")` 被动拦截 `comment/list` XHR (2026-06-18)
- [x] **视频详情抓取** — `enrich_top: 20`，SIGI_STATE精确互动数据 (已验证OK)
- [x] **前置过滤层** — digg<10 或 comment=0 跳过评论抓取 (2026-06-18)

### 🔴 遗留 (P0 级别)

(所有 P0 项已完成 ✅)

### 🟡 待做 (P1 级别)

- [ ] **行业关键词自定义** — 支持不同行业（包装/纺织/五金）自定义 keywords
- [ ] **视频参考价值对比** — 同一关键词下视频评分横向对比可视化
- [ ] **Dashboard重设计** — 对标参考视频看板 + 采购意向分析图表
- [ ] **外贸场景Prompt定制** — analyzer.py 外贸B2B专用prompt
- [ ] **多任务并行** — asyncio.gather 多关键词同时跑

### 🟢 待做 (P2 级别)

- [ ] **前端框架迁移** — Vue/React
- [ ] **数据可视化** — ECharts图表
- [ ] **scraper.py模块拆分** — 1500行→多个文件
- [ ] **自动打码** — 2Captcha集成
- [ ] **定时监控** — 关键词变化告警
- [ ] **多语言意图** — BERT模型替代关键词匹配

---

## 下次任务建议

1. 优先: **行业关键词自定义** — 支持切换不同行业的对标维度
2. 其次: **Dashboard重设计** — 对标参考视频看板
3. 然后: **外贸Prompt定制** — analyzer.py 采购场景专用prompt
