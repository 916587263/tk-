"""
TikTok 外贸行业对标视频发现系统 - Flask Web 应用
"""
import json
import queue
import threading
import asyncio
import time
from pathlib import Path
from datetime import datetime

from flask import Flask, render_template, request, jsonify, Response, send_file, session

from tiktok_analyzer.scraper import TikTokScraper
from tiktok_analyzer.analyzer import TikTokAnalyzer
from tiktok_analyzer.exporter import export_csv, export_markdown
from tiktok_analyzer.checkpoint import CheckpointManager
from tiktok_analyzer.proxy_pool import ProxyPool
from tiktok_analyzer.logger import setup_logger
from tiktok_analyzer.account_filter import AccountFilter, AccountFilterConfig
from tiktok_analyzer.account_scorer import AccountScorer, AccountScorerConfig
from tiktok_analyzer.video_scorer import VideoScorer, VideoFilter, VideoScorerConfig, VideoFilterConfig
from tiktok_analyzer.intent_detector import IntentDetector, IntentDetectorConfig
from tiktok_analyzer.viral_detector import ViralDetector, ViralDetectorConfig
from tiktok_analyzer.reference_video_scorer import ReferenceVideoScorer, ReferenceVideoScorerConfig

app = Flask(__name__)
app.secret_key = "tiktok-analyzer-secret-key-2024"
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
        "started_at": datetime.now().isoformat(),
    }

    params = (task_id, keywords, region, accounts_per_keyword,
              videos_per_account, comments_per_video,
              browser, headless, cdp_port,
              openai_key, openai_base_url, openai_model, use_ai,
              APP_CONFIG)

    thread = threading.Thread(target=_run_analysis, args=params, daemon=True)
    thread.start()

    return jsonify({"task_id": task_id, "status": "started"})


def _run_analysis(task_id, keywords, region, accounts_per_keyword,
                  videos_per_account, comments_per_video,
                  browser, headless, cdp_port,
                  openai_key, openai_base_url, openai_model, use_ai,
                  config=None):
    cfg = config or {}
    """后台分析任务"""
    pq = _progress_queues.get(task_id)
    if not pq:
        return

    def emit(msg, data=None):
        pq.put({"msg": msg, "data": data or {}, "timestamp": datetime.now().isoformat()})

    async def progress_cb(msg, data=None):
        emit(msg, data)

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
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        _active_tasks[task_id]["status"] = "running"
        emit("任务开始，正在启动浏览器...")

        # 模式选择：CDP 连接 vs 独立启动
        if cdp_port and cdp_port > 0:
            emit(f"使用 CDP 连接模式 (端口 {cdp_port})，连接已有浏览器...")
            loop.run_until_complete(scraper.connect_over_cdp(cdp_port))
        else:
            loop.run_until_complete(scraper.start_browser(
                user_data_dir=str(USER_DATA_DIR) if not headless else None
            ))

        logged_in = loop.run_until_complete(scraper.ensure_logged_in())
        if not logged_in:
            emit("登录失败，任务终止", {"error": "login_required"})
            _active_tasks[task_id]["status"] = "failed"
            return

        emit("开始采集数据...")
        enrich_top = cfg.get("video_scraping", {}).get("enrich_top", 0)
        scraped_data = loop.run_until_complete(scraper.run_analysis(
            keywords=keywords, region=region,
            accounts_per_keyword=accounts_per_keyword,
            videos_per_account=videos_per_account,
            comments_per_video=comments_per_video,
            comments_video_count=3,
            enrich_top=enrich_top,
        ))

        _active_tasks[task_id]["scraped_data"] = scraped_data

        # ═══════════════════════════════════════════════════════════
        # P0-P5: 过滤 + 评分 + 意图识别
        # ═══════════════════════════════════════════════════════════

        all_accounts = scraped_data.get("accounts", [])
        all_videos = scraped_data.get("videos", [])
        all_comments = scraped_data.get("comments", [])

        # ── P0: 账号过滤 ──
        af_cfg_dict = cfg.get("account_filter", {})
        af_cfg = AccountFilterConfig(**af_cfg_dict)
        if af_cfg.enabled:
            emit(f"🔍 账号过滤启动 (条件: min_followers={af_cfg.min_followers}, "
                 f"verified={af_cfg.require_verified}, region_wl={af_cfg.region_whitelist})...")
            account_filter = AccountFilter(af_cfg)
            before_af = len(all_accounts)
            all_accounts, filtered_accounts = account_filter.filter(all_accounts)
            if filtered_accounts:
                emit(f"账号过滤: {before_af} → {len(all_accounts)} "
                     f"(过滤 {len(filtered_accounts)} 个)")
                logger.info("过滤原因分布: %s", account_filter.stats.get("reasons", {}))
                # 同步过滤视频和评论
                kept_usernames = {a.get("username", "") for a in all_accounts}
                all_videos = [v for v in all_videos
                              if v.get("account_username", "") in kept_usernames]
                all_comments = [c for c in all_comments
                                if c.get("account_username", "") in kept_usernames]

        # ── P1: 账号评分 ──
        as_cfg_dict = cfg.get("account_scorer", {})
        as_cfg = AccountScorerConfig(**as_cfg_dict)
        if as_cfg.enabled and all_accounts:
            account_scorer = AccountScorer(as_cfg)
            all_accounts = account_scorer.score_all(all_accounts)
            tiers = {}
            for a in all_accounts:
                tiers[a.get("tier", "?")] = tiers.get(a.get("tier", "?"), 0) + 1
            top3 = [(a.get("username"), a.get("score")) for a in all_accounts[:3]]
            emit(f"📊 账号评分完成: {tiers} | Top3: {top3}")

        # ── P3: 视频过滤 ──
        vf_cfg_dict = cfg.get("video_filter", {})
        vf_cfg = VideoFilterConfig(**vf_cfg_dict)
        if vf_cfg.enabled and all_videos:
            video_filter = VideoFilter(vf_cfg)
            before_vf = len(all_videos)
            all_videos, _ = video_filter.filter(all_videos)
            emit(f"🔍 视频过滤: {before_vf} → {len(all_videos)}")

        # ── P3: 视频评分 ──
        vs_cfg_dict = cfg.get("video_scorer", {})
        vs_cfg = VideoScorerConfig(**vs_cfg_dict)
        if vs_cfg.enabled and all_videos:
            video_scorer = VideoScorer(vs_cfg)
            all_videos = video_scorer.score_all(all_videos)
            tiers = {}
            for v in all_videos:
                tiers[v.get("tier", "?")] = tiers.get(v.get("tier", "?"), 0) + 1
            emit(f"📊 视频评分完成: {tiers}")

            # 按账号聚合视频统计
            account_video_stats = video_scorer.aggregate_by_account(all_videos)
            # 将聚合统计合并到对应 account
            for acc in all_accounts:
                uname = acc.get("username", "")
                if uname in account_video_stats:
                    acc["video_stats"] = account_video_stats[uname]

        # ── 爆款视频发现 ──
        vd_cfg_dict = cfg.get("viral_detector", {})
        vd_cfg = ViralDetectorConfig(**vd_cfg_dict)
        if vd_cfg.enabled and all_videos:
            emit("🔥 正在识别爆款视频...")
            viral_detector = ViralDetector(vd_cfg)
            viral_data = viral_detector.analyze(all_videos, all_accounts)

            # 将 viral_profile 合并到对应 account
            for acc in all_accounts:
                uname = acc.get("username", "")
                if uname in viral_data["per_account"]:
                    acc["viral_profile"] = viral_data["per_account"][uname]

            _active_tasks[task_id]["viral_data"] = viral_data

            # 输出 Top 摘要
            top_list = viral_data["top_viral_videos"]
            if top_list:
                summaries = []
                for i, v in enumerate(top_list[:5]):
                    er = v.get("_engagement_rate", 0)
                    summaries.append(
                        f"#{i+1} @{v.get('account_username','')}: "
                        f"互动率{er:.1%}"
                    )
                emit(f"🔥 爆款 Top {len(top_list)}: {' | '.join(summaries)}")
            emit(f"📊 全局基准: 平均互动率 {viral_data['global_benchmarks'].get('avg_engagement_rate', 0):.1%}, "
                 f"爆款占比 {viral_data['global_benchmarks'].get('viral_ratio', 0):.0%}")

            # 特征摘要
            chars = viral_data.get("viral_characteristics", {})
            if chars.get("common_tags"):
                top_tags = [t["tag"] for t in chars["common_tags"][:5]]
                emit(f"🧬 爆款高频标签: {', '.join(['#'+t for t in top_tags])}")
            if chars.get("optimal_duration_range"):
                dr = chars["optimal_duration_range"]
                emit(f"⏱ 爆款最优时长: {dr[0]}s - {dr[1]}s")

        # ── P5: 商业意图识别 ──
        intent_data = None
        intent_cfg_dict = cfg.get("intent_detector", {})
        intent_cfg = IntentDetectorConfig(**intent_cfg_dict)
        if intent_cfg.enabled and all_comments:
            emit("🧠 正在识别评论商业意图...")
            intent_detector = IntentDetector(intent_cfg)
            intent_data = intent_detector.analyze_comments(all_comments)
            all_comments = intent_data["comments"]  # 已被原地修改
            insights = intent_detector.get_insights(intent_data)
            emit(f"意图识别: {intent_data['summary']['comments_with_intent']}/"
                 f"{intent_data['summary']['total_comments']} 条评论含商业意图 "
                 f"({intent_data['summary']['intent_rate']:.0%})")
            if insights:
                for insight in insights:
                    emit(f"  {insight}")
            _active_tasks[task_id]["intent_data"] = intent_data

        # ── 对标参考视频评分 ──
        ref_data = None
        rvs_cfg_dict = cfg.get("reference_video_scorer", {})
        rvs_cfg = ReferenceVideoScorerConfig(**rvs_cfg_dict)
        if rvs_cfg.enabled and all_videos and all_comments and all_accounts:
            emit("🎯 正在对标参考视频评分...")
            ref_scorer = ReferenceVideoScorer(rvs_cfg)
            ref_data = ref_scorer.score_all(all_videos, all_comments, all_accounts)
            top_list = ref_data.get("top_reference_videos", [])
            emit(f"🎯 对标参考视频: {len(top_list)} 条通过门控 (共 {len(all_videos)} 条视频)")
            if top_list:
                summaries = []
                for i, v in enumerate(top_list[:5]):
                    summaries.append(
                        f"#{i+1} @{v.get('account_username','')}: "
                        f"参考分{v.get('reference_score',0)} "
                        f"(采购意向{v.get('purchase_intent_ratio',0):.0%})"
                    )
                emit(f"  对标 Top 5: {' | '.join(summaries)}")
            _active_tasks[task_id]["reference_data"] = ref_data

        # 更新 scraped_data
        scraped_data["accounts"] = all_accounts
        scraped_data["videos"] = all_videos
        scraped_data["comments"] = all_comments
        scraped_data["total_accounts"] = len(all_accounts)
        scraped_data["total_videos"] = len(all_videos)
        scraped_data["total_comments"] = len(all_comments)

        # 注入爆款特征到 scraped_data（供导出使用）
        if _active_tasks[task_id].get("viral_data"):
            vd = _active_tasks[task_id]["viral_data"]
            scraped_data["viral_characteristics"] = vd.get("viral_characteristics", {})
            scraped_data["global_benchmarks"] = vd.get("global_benchmarks", {})

        # 注入对标参考视频数据（供导出使用）
        if _active_tasks[task_id].get("reference_data"):
            rd = _active_tasks[task_id]["reference_data"]
            scraped_data["reference_videos"] = rd.get("top_reference_videos", [])
            scraped_data["reference_benchmarks"] = rd.get("reference_benchmarks", {})

        # ═══════════════════════════════════════════════════════════
        # P6: AI 分析（传入评分和意图数据增强分析质量）
        # ═══════════════════════════════════════════════════════════
        analysis_data = None
        if use_ai and openai_key:
            emit("正在进行 AI 分析...")
            try:
                # 获取代理传给 OpenAI
                openai_proxy = scraper._current_proxy

                analyzer = TikTokAnalyzer(
                    api_key=openai_key,
                    base_url=openai_base_url or None,
                    model=openai_model,
                    proxy=openai_proxy,
                )

                # P6: 使用增强分析（如果启用了评分/意图识别则传入补充数据）
                ai_cfg = cfg.get("ai_analysis", {})
                use_scored = ai_cfg.get("use_scored_data", True)
                use_intent = ai_cfg.get("use_intent_data", True)

                if use_scored and (any(a.get("score") is not None for a in all_accounts)):
                    analysis_data = loop.run_until_complete(
                        analyzer.analyze_enhanced(
                            scraped_data,
                            intent_data=intent_data if use_intent else None,
                            config=ai_cfg,
                        )
                    )
                else:
                    analysis_data = loop.run_until_complete(analyzer.analyze(scraped_data))
                _active_tasks[task_id]["analysis_data"] = analysis_data
                emit("AI 分析完成", {"analysis": analysis_data})
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

        _active_tasks[task_id].update({
            "status": "completed",
            "csv_files": csv_files,
            "md_file": md_file,
        })

        emit("任务完成！", {
            "done": True,
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
    if task.get("status") != "completed":
        return jsonify({"error": "任务尚未完成"}), 400

    scraped = task.get("scraped_data", {})
    analysis = task.get("analysis_data", {})

    return jsonify({
        "summary": {
            "accounts": scraped.get("total_accounts", 0),
            "videos": scraped.get("total_videos", 0),
            "comments": scraped.get("total_comments", 0),
        },
        "accounts": scraped.get("accounts", [])[:10],
        "videos": sorted(scraped.get("videos", []), key=lambda x: x.get("digg_count", 0) or 0, reverse=True)[:20],
        "analysis": analysis,
    })


# ═══════════════════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("  TikTok 竞争对手分析系统 v1.0")
    print("  打开浏览器访问: http://127.0.0.1:5000")
    proxy_file = BASE_DIR / "proxies.json"
    if proxy_file.exists():
        print(f"  代理配置已加载: {proxy_file}")
    else:
        print("  未检测到代理配置（可创建 proxies.json 启用代理）")
    print("=" * 60)
    app.run(host="127.0.0.1", port=5000, debug=True, threaded=True)
