# TikTok 外贸行业对标视频发现系统

从 TikTok 搜索行业关键词 → 抓取视频和评论 → 自动识别外贸采购意向 → 输出 Top 20 最有价值的对标参考视频。

**核心能力**：5 阶段漏斗管道（QuickScore 快速淘汰 → 评论采样 → LLM 意图分类 → FinalScore 终极评分 → AI 分析总结），在保证召回的前提下将 LLM 调用量压缩到 3–5 次/任务。

---

## 环境要求

| 依赖 | 说明 |
|------|------|
| **Python 3.9+** | 需要 asyncio + dataclasses |
| **Microsoft Edge** 或 **Google Chrome** | CDP 远程调试模式（推荐 Edge） |
| **Playwright Chromium** | `playwright install chromium` |
| **TikTok 账号** | 在浏览器中手动登录 |

> **注意**：本项目针对 Windows 平台开发。macOS/Linux 需调整浏览器路径。

---

## 安装依赖

```bash
# 1. 克隆项目
cd =tk

# 2. 创建虚拟环境（推荐）
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # macOS/Linux

# 3. 安装 Python 依赖
pip install -r requirements.txt

# 4. 安装 Playwright 浏览器
playwright install chromium
```

---

## Chrome CDP 启动方式

> **使用 Launcher（推荐）**：`python launcher.py` 会自动处理以下所有步骤，无需手动操作。

如果你需要手动控制浏览器，使用以下步骤：

### 步骤
1. **完全关闭**所有 Edge / Chrome 窗口
2. 在终端运行 Edge 或 Chrome 并开启 CDP 调试端口：
   - **Edge**: `msedge --remote-debugging-port=9222`
   - **Chrome**: `chrome --remote-debugging-port=9222`
3. 在弹出的浏览器窗口中，打开 [https://www.tiktok.com](https://www.tiktok.com) 并**手动登录**
4. 启动本项目，在 Web UI 中选择 **"CDP 连接 (端口 9222) — 推荐"**

### 原理

CDP 模式复用你真实浏览器的完整环境（Cookie、LocalStorage、扩展、历史记录、WebGL 指纹），TikTok 看到的和你日常使用一模一样，无法区分是人在操作还是程序在采集。

---

## 环境变量配置（.env）

复制 `.env.example` 为 `.env`：

```bash
copy .env.example .env   # Windows
cp .env.example .env     # macOS/Linux
```

```ini
# LLM API Key（至少设置一个 — 不设置也能运行，但仅使用规则引擎）
DEEPSEEK_API_KEY=sk-your-deepseek-key-here     # 推荐，成本 ~$0.14/1M input
OPENAI_API_KEY=sk-your-openai-key-here         # 备选

# Flask 安全配置
SECRET_KEY=change-me-to-a-random-string        # python -c "import secrets; print(secrets.token_hex(32))"

# Flask 运行模式
FLASK_ENV=development                          # development=debug 模式 | production=关闭 debug
```

> API Key 也可以在 Web UI 表单中直接填写，无需设置环境变量。

---

## 配置文件说明（config.yaml）

复制 `config.example.yaml` 为 `config.yaml`，所有参数在此调整，**修改后下次分析自动生效**：

| 配置节 | 作用 | 关键参数 |
|--------|------|----------|
| `locale` / `timezone_id` | 浏览器区域 | zh-CN / en-US 等 |
| `enable_llm` | LLM 总开关 | false = 纯规则引擎，零 API 成本 |
| `llm_provider` | LLM 提供商 | deepseek / openai |
| `quick_scorer` | 阶段 1 快速评分 | 权重、阈值、降权系数 |
| `comment_sampling` | 阶段 2 评论采样 | 策略（first_n 等）、采样数 |
| `comment_classifier` | 阶段 3 LLM 分类 | 批量大小、模型、温度 |
| `final_scorer` | 阶段 4 终极评分 | 三权重（30/30/40）、S/A/B/C 分级 |
| `keyword_expansion` | 关键词扩展 | compact / balanced / full |
| `video_scraping.enrich_top` | 视频详情抓取量 | Top N 进入深度分析 |

> 配置文件底部的 `DEPRECATED` 段落是旧版管道参数，已不再生效，可以忽略。

---

## 启动方式

### 一键启动（推荐 ✨）

**Windows** — 双击 `start.bat` 或在终端运行:

```bash
python launcher.py
```

**macOS / Linux**:

```bash
chmod +x start.sh
./start.sh
```

Launcher 会自动完成:
1. 检查 Python 版本 + 依赖 + 浏览器
2. 自动启动/复用 Edge CDP (端口 9222)
3. 启动 Flask Web 应用
4. 打开浏览器访问界面

> **无需手动操作浏览器！** Launcher 会创建独立的浏览器 Profile (`launcher_profile/`)，不干扰你的日常浏览器数据。

**Launcher 配置** (可选):

编辑 `config/launcher.yaml` 自定义启动行为（修改后下次启动生效）:
```yaml
browser:
  preferred: edge        # edge | chrome | auto
  debug_port: 9222       # CDP 端口
  reuse_existing: true   # 复用已有 CDP 浏览器
app:
  port: 5000
  open_browser: true     # 启动后自动打开浏览器
logging:
  console_level: INFO    # DEBUG | INFO | WARNING | ERROR
```

或使用环境变量: `LAUNCHER_DEBUG_PORT=9223 python launcher.py`

### Web 界面

浏览器访问 [http://127.0.0.1:5000](http://127.0.0.1:5000)

1. 输入关键词（如 `non woven bag factory`）
2. 选择扩展深度（紧凑/标准/全面）
3. 选择 CDP 端口（9222 = 推荐）
4. 点击"开始分析"
5. 右侧实时查看进度，完成后下载 CSV/Markdown 报告

### 命令行

```bash
python tiktok_analyzer.py --keyword "non woven bag" --region US --cdp-port 9222 --ai
```

```
选项:
  -k, --keyword     搜索关键词，逗号分隔
  -r, --region      目标地区，如 US/UK
  -a, --accounts    每关键词账号数（默认 10）
  -v, --videos      每账号视频数（默认 10）
  -c, --comments    每视频评论数（默认 50）
  --browser         浏览器 msedge/chrome（默认 msedge）
  --cdp-port        CDP 端口（0=普通模式，9222=推荐）
  --ai              启用 AI 分析
  --openai-key      API Key（也可用环境变量）
  --expand          扩展级别 compact/balanced/full（默认 compact）
```

---

## 项目目录结构

```
=tk/
├── launcher.py                     # 一键启动器 ✨
├── start.bat                       # Windows 双击启动
├── start.sh                        # macOS/Linux 一键启动
├── launcher/                       # 启动器子模块
│   ├── env_check.py                # 环境检查
│   ├── browser_manager.py          # CDP 浏览器管理
│   └── app_runner.py               # Flask 进程管理
├── app.py                         # Flask Web 入口
├── tiktok_analyzer.py             # CLI 入口
├── config.yaml                    # 全局配置（不提交到 Git）
├── config.example.yaml            # 配置模板
├── requirements.txt               # Python 依赖
├── .env.example                   # 环境变量模板
├── README.md                      # 本文件
├── PROJECT_INDEX.md               # 架构文档 + 设计决策
├── TODO.md                        # 待办事项
├── tiktok_analyzer/               # 核心模块
│   ├── scraper.py                 # Playwright 爬虫（2689 行）
│   ├── unified_scorer.py          # QuickScore + FinalScore
│   ├── comment_classifier.py      # LLM 评论意图分类
│   ├── intent_detector.py         # 规则引擎意图扫描
│   ├── analyzer.py                # OpenAI 分析总结
│   ├── keyword_expander.py        # 关键词三级扩展
│   ├── network_collector.py       # API 响应拦截层
│   ├── exporter.py                # CSV/Markdown 导出
│   ├── checkpoint.py              # SQLite 断点续爬
│   ├── proxy_pool.py              # 代理池
│   ├── captcha.py                 # 验证码检测
│   └── logger.py                  # 日志模块
├── tests/                         # 测试（集成 + 单元）
├── templates/                     # Web UI 模板
├── static/                        # CSS
├── data/                          # 分析结果输出（不提交）
├── checkpoints/                   # 断点续爬 SQLite（不提交）
├── logs/                          # 运行日志（不提交）
├── cookies/                       # Cookie 持久化（不提交）
├── browser_profile/               # 浏览器持久化 Profile（不提交）
└── cache/                         # 关键词缓存（不提交）
```

---

## 常见错误

### `ModuleNotFoundError: No module named 'yaml'`
```bash
pip install pyyaml
```

### TikTok 显示 "访问繁忙" / "Too many requests"
- 确认使用 CDP 模式（不要用普通模式）
- 切换代理节点
- 降低每关键词账号数
- 等待 10–30 分钟后重试

### CDP 连接失败
- 确认 Edge/Chrome 已完全关闭后重新从命令行启动
- 确认端口号正确（默认 9222）
- 系统会自动降级为普通模式，但反爬效果差

### Launcher 启动失败
- `未找到浏览器`: 安装 Microsoft Edge (https://www.microsoft.com/edge)
- `DevTools 启动超时`: 检查是否有其他程序占用 9222 端口 (`netstat -ano | findstr 9222`)
- `依赖缺失`: 执行 `pip install -r requirements.txt`
- `找不到 Python`: 安装 Python 3.9+ (https://www.python.org/downloads/)
- 如需手动启动: `python app.py` (需先手动启动 CDP 浏览器)

### 未设置 API Key 提示
- 不影响核心功能。LLM 增强关闭时，系统使用规则引擎完成评分，只是阶段 3 和阶段 5 不执行 LLM 调用
- 如需开启，设置 `DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY` 环境变量，或在 Web UI 填写

### 爬取结果为空
- 检查 VPN/代理是否正常工作
- 检查 config.yaml 中 `quick_scorer.min_score` 是否过高
- 尝试更通用的关键词
- 查看 `logs/` 目录的详细日志

---

## 设计决策

详见 `PROJECT_INDEX.md`，关键决策：

- **DD-1**: 漏斗式管道 — 规则引擎在前，LLM 在后，保护预算
- **DD-3**: 低互动降权而非硬淘汰 — B2B 工厂账号天然低互动但高价值
- **DD-5**: 商业白名单优先于降权 — 商业关键词命中可跳过降权

---

## License

Internal use. Contact the project owner for permissions.
