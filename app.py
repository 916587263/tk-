"""
TikTok 竞争对手分析系统 - Flask Web 应用
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

app = Flask(__name__)
app.secret_key = "tiktok-analyzer-secret-key-2024"
logger = setup_logger("webapp")

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
USER_DATA_DIR = BASE_DIR / "browser_profile"

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
              openai_key, openai_base_url, openai_model, use_ai)

    thread = threading.Thread(target=_run_analysis, args=params, daemon=True)
    thread.start()

    return jsonify({"task_id": task_id, "status": "started"})


def _run_analysis(task_id, keywords, region, accounts_per_keyword,
                  videos_per_account, comments_per_video,
                  browser, headless, cdp_port,
                  openai_key, openai_base_url, openai_model, use_ai):
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
        scraped_data = loop.run_until_complete(scraper.run_analysis(
            keywords=keywords, region=region,
            accounts_per_keyword=accounts_per_keyword,
            videos_per_account=videos_per_account,
            comments_per_video=comments_per_video,
            comments_video_count=3,
        ))

        _active_tasks[task_id]["scraped_data"] = scraped_data

        # AI 分析（支持代理）
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
