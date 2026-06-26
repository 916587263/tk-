"""
TikTok 外贸行业对标视频发现系统 - Flask Web 应用
"""
import json
import os
import queue
import threading
import asyncio
import time
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, render_template, request, jsonify, Response, send_file, session

from tiktok_analyzer.scraper import TikTokScraper
from tiktok_analyzer.analyzer import TikTokAnalyzer
from tiktok_analyzer.exporter import export_csv, export_markdown, _safe_num
from tiktok_analyzer.checkpoint import CheckpointManager
from tiktok_analyzer.proxy_pool import ProxyPool
from tiktok_analyzer.logger import setup_logger
from tiktok_analyzer.keyword_expander import KeywordExpander, KeywordCache

# 新管道模块
from tiktok_analyzer.unified_scorer import FinalScorer, FinalScorerConfig
from tiktok_analyzer.comment_classifier import CommentClassifier, CommentClassifierConfig
from tiktok_analyzer.intent_detector import QuickIntentScanner

# DEPRECATED — 旧管道模块 (保留导入避免启动报错, 不再使用)
# from tiktok_analyzer.account_filter import AccountFilter, AccountFilterConfig
# from tiktok_analyzer.account_scorer import AccountScorer, AccountScorerConfig
# from tiktok_analyzer.video_scorer import VideoScorer, VideoFilter, VideoScorerConfig, VideoFilterConfig
# from tiktok_analyzer.intent_detector import IntentDetector, IntentDetectorConfig
# from tiktok_analyzer.viral_detector import ViralDetector, ViralDetectorConfig
# from tiktok_analyzer.reference_video_scorer import ReferenceVideoScorer, ReferenceVideoScorerConfig

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "tiktok-analyzer-dev-key-change-in-production")
logger = setup_logger("webapp")

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
USER_DATA_DIR = BASE_DIR / "browser_profile"
CONFIG_FILE = BASE_DIR / "config.yaml"


def _load_config() -> dict:
    """加载 YAML 配置，不存在则返回空 dict（使用各模块默认值）"""
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML 未安装 (pip install pyyaml)，使用默认配置")
        return {}
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            logger.info("已加载配置: %s", CONFIG_FILE)
            return data
    logger.info("配置文件不存在 (%s)，使用默认配置", CONFIG_FILE)
    return {}

APP_CONFIG = _load_config()

# 活跃任务
_active_tasks: dict[str, dict] = {}
_progress_queues: dict[str, queue.Queue] = {}


def _get_task_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ═══════════════════════════════════════════════════════════
# Web 页面
# ═══════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")


# ═══════════════════════════════════════════════════════════
# API: 启动分析任务
# ═══════════════════════════════════════════════════════════

@app.route("/api/start", methods=["POST"])
def start_analysis():
    data = request.json or {}
    keywords_str = data.get("keywords", "")
    region = data.get("region", "")
    accounts_per_keyword = int(data.get("accounts_per_keyword", 10))
    videos_per_account = int(data.get("videos_per_account", 30))
    comments_per_video = int(data.get("comments_per_video", 200))
    browser = data.get("browser", "msedge")
    headless = data.get("headless", False)
    cdp_port = data.get("cdp_port", 0)  # CDP 连接模式端口（0=禁用）
    openai_key = data.get("openai_key", "")
    openai_base_url = data.get("openai_base_url", "")
    openai_model = data.get("openai_model", "gpt-4o")
    use_ai = data.get("use_ai", True)
    expand_tier = data.get("expand_tier", "balanced")

    keywords = [k.strip() for k in keywords_str.replace("\n", ",").split(",") if k.strip()]
    if not keywords:
        return jsonify({"error": "请至少输入一个关键词"}), 400

    task_id = _get_task_id()
    pq = queue.Queue()
    _progress_queues[task_id] = pq

    _active_tasks[task_id] = {
        "status": "starting",
        "keywords": keywords,
        "region": region,
        "expand_tier": expand_tier,
        "started_at": datetime.now().isoformat(),
    }

    params = (task_id, keywords, region, accounts_per_keyword,
              videos_per_account, comments_per_video,
              browser, headless, cdp_port,
              openai_key, openai_base_url, openai_model, use_ai,
              expand_tier, APP_CONFIG)

    thread = threading.Thread(target=_run_analysis, args=params, daemon=True)
    thread.start()

    return jsonify({"task_id": task_id, "status": "started"})


# ═══════════════════════════════════════════════════════════
# API: 停止分析任务
# ═══════════════════════════════════════════════════════════

@app.route("/api/stop/<task_id>", methods=["POST"])
def stop_analysis(task_id):
    task = _active_tasks.get(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    task["cancelled"] = True
    # 跨线程通知 scraper 停止
    scraper = task.get("scraper")
    if scraper:
        scraper.cancel()
    logger.info("任务 %s 收到取消请求", task_id)
    return jsonify({"status": "cancelling"})


# ═══════════════════════════════════════════════════════════
# API: 关键词扩展
# ═══════════════════════════════════════════════════════════

@app.route("/api/expand", methods=["POST"])
def expand_keywords():
    """预览关键词扩展结果"""
    data = request.json or {}
    keywords_str = data.get("keywords", "")
    tier = data.get("tier", "balanced")

    keywords = [k.strip() for k in keywords_str.replace("\n", ",").split(",") if k.strip()]
    if not keywords:
        return jsonify({"error": "请至少输入一个关键词"}), 400

    if tier not in ("compact", "balanced", "full"):
        tier = "balanced"

    cfg = APP_CONFIG.get("keyword_expansion", {})
    cache_ttl = cfg.get("cache_ttl_days", 7)
    limits = cfg.get("max_keywords_per_tier", None)

    cache = KeywordCache(ttl_days=cache_ttl)
    expander = KeywordExpander(cache=cache, tier_limits=limits)
    result = expander.expand(keywords, tier=tier)

    return jsonify(result)


def _run_analysis(task_id, keywords, region, accounts_per_keyword,
                  videos_per_account, comments_per_video,
                  browser, headless, cdp_port,
                  openai_key, openai_base_url, openai_model, use_ai,
                  expand_tier, config=None):
    cfg = config or {}
    """后台分析任务"""
    pq = _progress_queues.get(task_id)
    if not pq:
        return

    def emit(msg, data=None):
        pq.put({"msg": msg, "data": data or {}, "timestamp": datetime.now().isoformat()})

    async def progress_cb(msg, data=None):
        emit(msg, data)

    # ── 关键词扩展 ──
    if expand_tier and expand_tier in ("compact", "balanced", "full") and len(keywords) <= 5:
        exp_cfg = cfg.get("keyword_expansion", {})
        cache_ttl = exp_cfg.get("cache_ttl_days", 7)
        limits = exp_cfg.get("max_keywords_per_tier", None)

        cache = KeywordCache(ttl_days=cache_ttl)
        expander = KeywordExpander(cache=cache, tier_limits=limits)
        exp_result = expander.expand(keywords, tier=expand_tier)

        if exp_result.get("added", 0) > 0:
            emit(
                f"🔑 关键词已扩展 ({expand_tier}): {len(keywords)} → {exp_result['count']} 词 "
                f"(新增 {exp_result['added']} 词)"
                + (f" [缓存命中]" if exp_result.get("from_cache") else "")
            )
            keywords = exp_result["keywords"]
            # ── 硬上限: 最多 10 个关键词 ──
            if len(keywords) > 10:
                emit(f"⚠️ 扩展后关键词过多 ({len(keywords)} 个)，截断为前 10 个")
                keywords = keywords[:10]
            _active_tasks[task_id]["expanded_keywords"] = keywords
            _active_tasks[task_id]["expand_tier"] = expand_tier
    elif expand_tier and len(keywords) > 5:
        emit(f"⚠️ 输入关键词过多 ({len(keywords)} 个)，跳过扩展（最多5个）")
    # ── 硬上限: 未扩展的关键词也限制最多 10 个 ──
    if len(keywords) > 10:
        emit(f"⚠️ 关键词过多 ({len(keywords)} 个)，截断为前 10 个")
        keywords = keywords[:10]

    checkpoint = CheckpointManager(task_id)
    proxy_pool = ProxyPool(check_reachable=False)

    # 自动加载本地代理配置
    proxy_file = BASE_DIR / "proxies.json"
    if proxy_file.exists():
        proxy_pool.load_from_file(str(proxy_file))
        logger.info("已加载代理配置: %s", proxy_file)

    scraper = TikTokScraper(
        browser_channel=browser,
        headless=headless,
        proxy_pool=proxy_pool,
        checkpoint=checkpoint,
        progress_callback=progress_cb,
        locale=cfg.get("locale", "zh-CN"),
        timezone_id=cfg.get("timezone_id", "Asia/Shanghai"),
    )

    # 存储 scraper 引用，供 /api/stop 跨线程调用 cancel()
    _active_tasks[task_id]["scraper"] = scraper

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        _active_tasks[task_id]["status"] = "running"
        emit("任务开始，正在启动浏览器...")

        # 模式选择：CDP 连接 vs 独立启动（CDP 失败自动降级）
        if cdp_port and cdp_port > 0:
            emit(f"使用 CDP 连接模式 (端口 {cdp_port})，连接已有浏览器...")
            try:
                loop.run_until_complete(scraper.connect_over_cdp(cdp_port))
            except Exception as cdp_err:
                logger.warning("CDP 连接失败 (%s)，降级为独立启动", cdp_err)
                emit(f"⚠️ CDP 连接失败 ({cdp_err})，降级为独立启动模式...")
                loop.run_until_complete(scraper.start_browser(
                    user_data_dir=str(USER_DATA_DIR)
                ))
        else:
            loop.run_until_complete(scraper.start_browser(
                user_data_dir=str(USER_DATA_DIR)
            ))

        logged_in = loop.run_until_complete(scraper.ensure_logged_in())
        if not logged_in:
            emit("登录失败，任务终止", {"error": "login_required"})
            _active_tasks[task_id]["status"] = "failed"
            return

        emit("开始采集数据...")
        enrich_top = cfg.get("video_scraping", {}).get("enrich_top", 0)

        # ── 阶段 2 评论采样配置 ──
        sampling_cfg = cfg.get("comment_sampling", {})
        sample_comment_count = sampling_cfg.get("sample_count", 8)
        comment_sampling_strategy = sampling_cfg.get("strategy", "first_n")

        scraped_data = loop.run_until_complete(scraper.run_analysis(
            keywords=keywords, region=region,
            accounts_per_keyword=accounts_per_keyword,
            videos_per_account=videos_per_account,
            comments_per_video=comments_per_video,
            comments_video_count=3,
            enrich_top=enrich_top,
            sample_comment_count=sample_comment_count,
            comment_sampling_strategy=comment_sampling_strategy,
        ))

        _active_tasks[task_id]["scraped_data"] = scraped_data

        # ═══════════════════════════════════════════════════════════
        # 新管道: CommentClassifier (阶段 3 LLM) → FinalScorer (阶段 4) → Analyzer (阶段 5)
        # QuickScore (阶段 1) + 评论采样/深度抓取 (阶段 2-3 爬取) 已在 scraper.run_analysis() 完成
        # ═══════════════════════════════════════════════════════════

        all_accounts = scraped_data.get("accounts", [])
        all_videos = scraped_data.get("videos", [])
        all_comments = list(scraped_data.get("comments", []))
        deep_analyzed_videos = scraped_data.get("deep_analyzed_videos", [])

        emit(
            f"📊 管道数据: {len(all_videos)} 条通过 QuickScore, "
            f"{len(deep_analyzed_videos)} 条深度分析 ({len(all_comments)} 条评论)"
        )

        # ── 阶段 3: 商业意图评分 (规则引擎, 零成本默认) ──
        classify_map = {}       # LLM 结果
        quick_intent_map = {}   # 规则引擎结果 (始终构建)
        intent_data = None

        enable_llm = cfg.get("enable_llm", False)
        llm_provider = cfg.get("llm_provider", "openai")
        llm_model = cfg.get("llm_model", "gpt-4o-mini")
        llm_base_url = cfg.get("llm_base_url", "")

        # ── 始终构建规则引擎分数 ──
        quick_scanner = QuickIntentScanner(min_hits=1)
        for v in deep_analyzed_videos:
            deep_c = v.get("_deep_comments", [])
            score_data = quick_scanner.score_comments(deep_c)
            quick_intent_map[v.get("id", "")] = score_data
            v["intent_ratio"] = score_data["intent_ratio"]
            v["intent_quality_score"] = score_data["intent_quality_score"]
            v["intent_diversity"] = score_data["intent_diversity"]
            v["actionable_intent_count"] = score_data["actionable_intent_count"]
            v["categories_hit"] = score_data["categories_hit"]
            v["is_weak_reference"] = (score_data["actionable_intent_count"] == 0)

        emit(
            f"📊 阶段 3 (规则引擎): {len(deep_analyzed_videos)} 条视频完成意图评分 "
            f"(命中类别: {sum(1 for s in quick_intent_map.values() if s['intent_diversity'] > 0)})"
        )

        # ── 可选 LLM 增强层 ──
        api_key = openai_key or os.environ.get("DEEPSEEK_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
        if enable_llm and deep_analyzed_videos and api_key:
            cc_cfg_dict = cfg.get("comment_classifier", {})
            cc_cfg = CommentClassifierConfig(**{
                k: v for k, v in cc_cfg_dict.items()
                if k in CommentClassifierConfig.__dataclass_fields__
            })

            candidates = sorted(
                deep_analyzed_videos,
                key=lambda v: v.get("actionable_intent_count", 0),
                reverse=True
            )[:20]
            emit(f"🧠 阶段 3+ (LLM 增强): {llm_provider}/{llm_model} 分析 Top {len(candidates)} 候选视频...")

            openai_proxy = scraper._current_proxy
            base_url = llm_base_url or openai_base_url or None
            model = llm_model if llm_provider != "openai" else (openai_model or cc_cfg.model)

            classifier = CommentClassifier(
                api_key=api_key,
                config=cc_cfg,
                base_url=base_url,
                model=model,
                proxy=openai_proxy,
            )

            try:
                batch_result = loop.run_until_complete(
                    classifier.classify_videos(candidates)
                )
                llm_classify_map = {}
                for vr in batch_result.video_results:
                    llm_classify_map[vr.video_id] = vr

                for v in candidates:
                    vid = v.get("id", "")
                    vr = llm_classify_map.get(vid)
                    if vr:
                        classify_map[vid] = vr
                        v["intent_ratio"] = vr.intent_ratio
                        v["intent_quality_score"] = vr.intent_quality_score
                        v["intent_diversity"] = vr.intent_diversity
                        v["actionable_intent_count"] = vr.actionable_intent_count
                        v["categories_hit"] = vr.categories_hit
                        v["is_weak_reference"] = vr.is_weak_reference
                        deep_c = v.get("_deep_comments", [])
                        classified_map = {cc.comment_index: cc for cc in vr.classified_comments}
                        for ci, c in enumerate(deep_c):
                            cc = classified_map.get(ci)
                            if cc:
                                c["has_intent"] = cc.has_intent
                                c["top_intent"] = cc.category
                                c["top_intent_confidence"] = cc.intensity
                                c["actionable"] = cc.actionable

                emit(
                    f"✅ LLM 增强完成: {len(llm_classify_map)} 条视频精确分类 "
                    f"(LLM 调用 {batch_result.total_llm_calls} 次)"
                )
            except Exception as e:
                logger.warning("LLM 分类失败: %s, 降级使用规则引擎分数", e)
                emit(f"⚠️ LLM 分类失败: {e}, 降级使用规则引擎分数")
            finally:
                loop.run_until_complete(classifier.close())

        elif enable_llm and not api_key and deep_analyzed_videos:
            emit("⚠️ 未设置 API Key (DEEPSEEK_API_KEY / OPENAI_API_KEY), 使用规则引擎")
        elif not enable_llm and deep_analyzed_videos:
            emit("🔧 LLM 增强已关闭 (enable_llm=false), 使用规则引擎")

        # 将深度评论加入 all_comments
        for v in deep_analyzed_videos:
            deep_c = v.get("_deep_comments", [])
            for c in deep_c:
                c["account_username"] = v.get("account_username", "")
            all_comments.extend(deep_c)

        # ── 阶段 4: FinalScore 终极统一评分 ──
        fs_cfg_dict = cfg.get("final_scorer", {})
        fs_cfg = FinalScorerConfig(**{
            k: v for k, v in fs_cfg_dict.items()
            if k in FinalScorerConfig.__dataclass_fields__
        })

        if fs_cfg.enabled and deep_analyzed_videos:
            emit("🎯 阶段 4: FinalScore 终极评分 (30/30/40)...")

            final_scorer = FinalScorer(fs_cfg)
            all_scored, top_references = final_scorer.score_all(
                deep_analyzed_videos, classify_map,
                quick_intent_map=quick_intent_map
            )

            all_accounts = final_scorer.aggregate_accounts(top_references, all_accounts)

            tiers = {}
            for v in all_scored:
                t = v.get("tier", "D")
                tiers[t] = tiers.get(t, 0) + 1
            emit(
                f"✅ FinalScore: {len(all_scored)} 条评分, {len(top_references)} 条对标参考, "
                f"等级分布: {tiers}"
            )

            if top_references:
                summaries = []
                for i, v in enumerate(top_references[:5]):
                    summaries.append(
                        f"#{i+1} @{v.get('account_username','')}: "
                        f"final={v.get('final_score',0):.0f} "
                        f"(意图={v.get('commercial_intent',0):.0f})"
                    )
                emit(f"  对标 Top 5: {' | '.join(summaries)}")

            for v in top_references:
                vid = v.get("id", "")
                vr = classify_map.get(vid)
                qi = quick_intent_map.get(vid, {})
                if vr:
                    intent_comments = sorted(
                        [c for c in vr.classified_comments if c.has_intent],
                        key=lambda c: c.intensity, reverse=True
                    )
                    v["intent_summary"] = {
                        "intent_ratio": vr.intent_ratio,
                        "top_categories": vr.categories_hit[:5],
                        "sample_comments": [
                            {"username": "", "text": c.text[:150]}
                            for c in intent_comments[:5]
                        ],
                    }
                    v["purchase_intent_ratio"] = vr.intent_ratio
                    v["purchase_intent_comments"] = vr.intent_comments
                else:
                    v["intent_summary"] = {
                        "intent_ratio": qi.get("intent_ratio", 0),
                        "top_categories": qi.get("categories_hit", [])[:5],
                        "sample_comments": [],
                    }
                    v["purchase_intent_ratio"] = qi.get("intent_ratio", 0)
                    v["purchase_intent_comments"] = int(
                        qi.get("intent_ratio", 0) * len(v.get("_deep_comments", []))
                    )

            _active_tasks[task_id]["reference_data"] = {
                "top_reference_videos": top_references,
                "all_scored": all_scored,
            }

        else:
            top_references = deep_analyzed_videos[:20]
            emit(f"⚠️ FinalScore 未启用/无深度视频, Top {len(top_references)} 条直接作为参考")

        # 更新 scraped_data (供导出)
        tiers_summary = tiers if 'tiers' in locals() else {}
        scraped_data["accounts"] = all_accounts
        scraped_data["videos"] = all_videos
        scraped_data["comments"] = all_comments
        scraped_data["total_accounts"] = len(all_accounts)
        scraped_data["total_videos"] = len(all_videos)
        scraped_data["total_comments"] = len(all_comments)
        scraped_data["reference_videos"] = top_references
        scraped_data["reference_benchmarks"] = {
            "top_n": len(top_references),
            "tiers": tiers_summary,
        }

        # ── 阶段 5: AI 分析总结 (精简, 仅 Top 20) ──
        analysis_data = None
        if use_ai and openai_key and top_references:
            emit(f"🤖 阶段 5: AI 分析总结 (仅 Top {min(20, len(top_references))} 对标视频)...")
            try:
                openai_proxy = scraper._current_proxy

                analyzer = TikTokAnalyzer(
                    api_key=openai_key,
                    base_url=openai_base_url or None,
                    model=openai_model,
                    proxy=openai_proxy,
                )

                analysis_data = loop.run_until_complete(
                    analyzer.analyze_top_references(top_references, all_accounts)
                )
                _active_tasks[task_id]["analysis_data"] = analysis_data
                emit(
                    f"✅ AI 分析完成 (市场机会评分: {analysis_data.get('market_opportunity_score', 'N/A')})",
                    {"analysis": analysis_data}
                )
            except Exception as e:
                logger.error("AI 分析失败: %s", e)
                emit(f"AI 分析失败: {e}", {"error": str(e)})
            finally:
                loop.run_until_complete(analyzer.close())

        elif use_ai and not openai_key:
            emit("未设置 OpenAI API Key，跳过 AI 分析")

        # 导出
        emit("正在导出报告...")
        output_dir = DATA_DIR / task_id
        csv_files = export_csv(scraped_data, analysis_data, str(output_dir))
        md_file = export_markdown(scraped_data, analysis_data, keywords, region, str(output_dir))

        cancelled = _active_tasks[task_id].get("cancelled", False)
        _active_tasks[task_id].update({
            "status": "cancelled" if cancelled else "completed",
            "csv_files": csv_files,
            "md_file": md_file,
        })

        done_msg = "⏹ 任务已停止（部分结果）" if cancelled else "任务完成！"
        emit(done_msg, {
            "done": True,
            "cancelled_early": cancelled,
            "csv_files": {k: str(v) for k, v in csv_files.items()},
            "md_file": str(md_file),
            "summary": {
                "accounts": scraped_data.get("total_accounts", 0),
                "videos": scraped_data.get("total_videos", 0),
                "comments": scraped_data.get("total_comments", 0),
            }
        })

    except Exception as e:
        logger.exception("任务异常")
        emit(f"任务出错: {e}", {"error": str(e)})
        _active_tasks[task_id]["status"] = "failed"

    finally:
        try:
            loop.run_until_complete(scraper.close())
        except Exception:
            pass
        loop.close()


# ═══════════════════════════════════════════════════════════
# API: SSE 进度推送
# ═══════════════════════════════════════════════════════════

@app.route("/api/progress/<task_id>")
def progress_stream(task_id):
    pq = _progress_queues.get(task_id)
    if not pq:
        return Response("data: {}\n\n", mimetype="text/event-stream")

    def generate():
        while True:
            try:
                msg = pq.get(timeout=30)
                yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                if msg.get("data", {}).get("done"):
                    break
                if msg.get("data", {}).get("error"):
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'msg': 'heartbeat'}, ensure_ascii=False)}\n\n"

    return Response(generate(), mimetype="text/event-stream")


# ═══════════════════════════════════════════════════════════
# API: 任务状态
# ═══════════════════════════════════════════════════════════

@app.route("/api/status/<task_id>")
def task_status(task_id):
    task = _active_tasks.get(task_id, {})
    return jsonify({
        "task_id": task_id,
        "status": task.get("status", "unknown"),
        "keywords": task.get("keywords", []),
        "region": task.get("region", ""),
    })


# ═══════════════════════════════════════════════════════════
# API: 下载文件
# ═══════════════════════════════════════════════════════════

@app.route("/api/download/<task_id>/<file_type>")
def download_file(task_id, file_type):
    task = _active_tasks.get(task_id, {})

    if file_type == "report":
        path = task.get("md_file", "")
    else:
        csv_files = task.get("csv_files", {})
        path = csv_files.get(file_type, "")

    if path and Path(path).exists():
        return send_file(path, as_attachment=True)
    return jsonify({"error": "文件不存在"}), 404


# ═══════════════════════════════════════════════════════════
# API: 获取结果摘要
# ═══════════════════════════════════════════════════════════

@app.route("/api/results/<task_id>")
def task_results(task_id):
    task = _active_tasks.get(task_id, {})
    if task.get("status") not in ("completed", "cancelled"):
        return jsonify({"error": "任务尚未完成"}), 400

    scraped = task.get("scraped_data", {})
    analysis = task.get("analysis_data", {})
    ref_data = task.get("reference_data", {})
    intent_data = task.get("intent_data", {})

    # 构建意图摘要 (兼容新旧格式)
    intent_summary = {}
    if intent_data:
        s = intent_data.get("summary", {})
        intent_summary = {
            "videos_analyzed": s.get("total_videos_analyzed", 0),
            "videos_with_intent": s.get("videos_with_intent", 0),
            "avg_intent_ratio": s.get("avg_intent_ratio", 0),
            "total_actionable": s.get("total_actionable", 0),
            # 向后兼容
            "total_comments": s.get("total_comments",
                                     scraped.get("total_comments", 0)),
            "comments_with_intent": s.get("comments_with_intent",
                                          s.get("videos_with_intent", 0)),
            "intent_rate": s.get("intent_rate",
                                 s.get("avg_intent_ratio", 0)),
            "by_category": s.get("by_category", {}),
        }

    return jsonify({
        "summary": {
            "accounts": scraped.get("total_accounts", 0),
            "videos": scraped.get("total_videos", 0),
            "comments": scraped.get("total_comments", 0),
            "deep_analyzed": scraped.get("intent_signaled_count",
                                         len(scraped.get("deep_analyzed_videos", []))),
            "quick_score_stats": scraped.get("quick_score_stats", {}),
        },
        "reference_videos": ref_data.get("top_reference_videos", []),
        "reference_benchmarks": ref_data.get("reference_benchmarks", {}),
        "intent_summary": intent_summary,
        "accounts": scraped.get("accounts", [])[:10],
        "videos": sorted(
            scraped.get("deep_analyzed_videos", scraped.get("videos", [])),
            key=lambda x: _safe_num(x.get("final_score", x.get("quick_score", 0)) or 0),
            reverse=True
        )[:20],
        "analysis": analysis,
    })


# ═══════════════════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_ENV", "development") == "development"
    print("=" * 60)
    print("  TikTok 竞争对手分析系统 v1.0")
    print("  打开浏览器访问: http://127.0.0.1:5000")
    proxy_file = BASE_DIR / "proxies.json"
    if proxy_file.exists():
        print(f"  代理配置已加载: {proxy_file}")
    else:
        print("  未检测到代理配置（可创建 proxies.json 启用代理）")
    print("=" * 60)
    app.run(host="127.0.0.1", port=5000, debug=debug_mode, threaded=True)
