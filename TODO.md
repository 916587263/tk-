# TODO — TikTok 工厂获客分析系统

## 当前状态 (2026-06-18)

### ✅ 已完成

- [x] **P0-1** 评论抓取修复 — `_extract_comments_from_js()` + wait_for_selector + 扩展selector
- [x] **P0-2** B2B商业意图识别 — 6类意图 + ~90个中英文B2B关键词
- [x] **P0-3** 地区筛选增强 — language_whitelist + require_contact + region+location联合匹配
- [x] **P0-4** B2B账号评分校准 — follower_benchmark=10K, like_benchmark=100K
- [x] **P1** 账号评分器 account_scorer.py
- [x] **P3** 视频评分器 video_scorer.py + VideoFilter
- [x] **爆款发现** viral_detector.py
- [x] **P5** 评论区意图识别 intent_detector.py
- [x] **P6** AI增强分析 analyzer.py (6维度)
- [x] **导出增强** exporter.py (评分列/爆款列/意图列/新MD章节)
- [x] **CLI入口** tiktok_analyzer.py
- [x] **项目索引** PROJECT_INDEX.md
- [x] **评论网络拦截修复** — `page.on("response")` 被动拦截 `comment/list` XHR (2026-06-18)
- [x] **视频详情抓取** — `enrich_top: 20`，SIGI_STATE精确互动数据 (已验证OK)

### 🔴 遗留 (P0 级别)

(所有 P0 项已完成 ✅)

### 🟡 待做 (P1 级别)

- [ ] **数据持久化** — SQLite leads/accounts/videos/comments 表
- [ ] **线索提取器** lead_extractor.py — 邮箱/WhatsApp/微信/电话自动提取
- [ ] **评论→联系人转化** — 高意向评论 → 线索库
- [ ] **Dashboard重设计** — 数据看板+线索列表+图表
- [ ] **工厂场景Prompt定制** — analyzer.py B2B专用prompt
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

1. 优先: **lead_extractor.py** — 评论→线索转化（邮箱/WhatsApp/微信/电话自动提取）
2. 其次: **数据持久化** — SQLite leads/accounts/videos/comments 表
3. 然后: **Dashboard重设计** — 数据看板+线索列表+图表
4. 可选: **工厂场景Prompt定制** — analyzer.py B2B专用prompt
