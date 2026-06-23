#!/usr/bin/env py
"""
TikTok 外贸行业对标视频发现系统 — CLI 主入口

用法:
  py tiktok_analyzer.py --keyword "non woven bag" --region US
  py tiktok_analyzer.py --keyword "makeup,skincare" --accounts 5 --videos 10 --comments 50
  py tiktok_analyzer.py --keyword "pp woven" --region US --ai --openai-key sk-xxx
"""
import argparse
import asyncio
import json
import sys
import threading
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from tiktok_analyzer.scraper import TikTokScraper
from tiktok_analyzer.analyzer import TikTokAnalyzer
from tiktok_analyzer.exporter import export_csv, export_markdown
from tiktok_analyzer.checkpoint import CheckpointManager
from tiktok_analyzer.proxy_pool import ProxyPool
from tiktok_analyzer.logger import setup_logger
from tiktok_analyzer.account_filter import AccountFilter, AccountFilterConfig
from tiktok_analyzer.account_scorer import AccountScorer, AccountScorerConfig
from tiktok_analyzer.video_scorer import VideoScorer, VideoFilter, VideoScorerConfig, VideoFilterConfig
from tiktok_analyzer.viral_detector import ViralDetector, ViralDetectorConfig
from tiktok_analyzer.intent_detector import IntentDetector, IntentDetectorConfig
from tiktok_analyzer.reference_video_scorer import ReferenceVideoScorer, ReferenceVideoScorerConfig
from tiktok_analyzer.keyword_expander import KeywordExpander, KeywordCache

logger = setup_logger("cli")


def load_config() -> dict:
    cfg_file = BASE_DIR / "config.yaml"
    if cfg_file.exists():
        import yaml
        with open(cfg_file, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def parse_args():
    p = argparse.ArgumentParser(description="TikTok 工厂获客分析系统")
    p.add_argument("--keyword", "-k", required=True, help="搜索关键词，逗号分隔")
    p.add_argument("--region", "-r", default="", help="目标地区，如 US/UK")
    p.add_argument("--accounts", "-a", type=int, default=10, help="每关键词账号数")
    p.add_argument("--videos", "-v", type=int, default=10, help="每账号视频数")
    p.add_argument("--comments", "-c", type=int, default=50, help="每视频评论数")
    p.add_argument("--browser", default="msedge", help="浏览器 (msedge/chrome)")
    p.add_argument("--cdp-port", type=int, default=0, help="CDP端口 (0=普通模式)")
    p.add_argument("--ai", action="store_true", help="启用AI分析")
    p.add_argument("--openai-key", default="", help="OpenAI API Key")
    p.add_argument("--openai-base", default="", help="OpenAI Base URL")
    p.add_argument("--openai-model", default="gpt-4o", help="OpenAI Model")
    p.add_argument("--output", "-o", default="", help="输出目录（默认 data/task_id）")
    p.add_argument("--expand", default="compact", choices=["compact", "balanced", "full"],
                   help="关键词扩展级别 (compact/balanced/full, 默认compact)")
    return p.parse_args()


async def main():
    args = parse_args()
    cfg = load_config()
    keywords = [k.strip() for k in args.keyword.replace("\n", ",").split(",") if k.strip()]
    task_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"🚀 TikTok 外贸行业对标视频发现系统 v2.0")
    print(f"   关键词: {keywords}")
    print(f"   地区: {args.region or '不限'}")
    print(f"   配置: {args.accounts}账号/关键词, {args.videos}视频/账号, {args.comments}评论/视频")
    print(f"   扩展: {args.expand}")
    print(f"   模式: {'CDP:{}'.format(args.cdp_port) if args.cdp_port else '普通'}")
    print(f"   AI: {'启用' if args.ai else '关闭'}")
    print()

    # ── 关键词扩展 ──
    exp_cfg = cfg.get("keyword_expansion", {})
    if args.expand and len(keywords) <= 5:
        cache_ttl = exp_cfg.get("cache_ttl_days", 7)
        limits = exp_cfg.get("max_keywords_per_tier", None)
        cache = KeywordCache(ttl_days=cache_ttl)
        expander = KeywordExpander(cache=cache, tier_limits=limits)
        exp_result = expander.expand(keywords, tier=args.expand)
        if exp_result.get("added", 0) > 0:
            print(f"  🔑 关键词已扩展 ({args.expand}): {len(keywords)} → {exp_result['count']} 词 "
                  f"(+{exp_result['added']})" + (" [缓存命中]" if exp_result.get("from_cache") else ""))
            keywords = exp_result["keywords"]
            # ── 硬上限: 最多 10 个关键词 ──
            if len(keywords) > 10:
                print(f"  ⚠️ 扩展后关键词过多 ({len(keywords)} 个)，截断为前 10 个")
                keywords = keywords[:10]
    elif args.expand and len(keywords) > 5:
        print(f"  ⚠️ 输入关键词过多 ({len(keywords)} 个)，跳过扩展")
    # ── 硬上限: 未扩展的关键词也限制最多 10 个 ──
    if len(keywords) > 10:
        print(f"  ⚠️ 关键词过多 ({len(keywords)} 个)，截断为前 10 个")
        keywords = keywords[:10]

    # Init
    checkpoint = CheckpointManager(task_id)
    proxy_pool = ProxyPool(check_reachable=False)
    proxy_file = BASE_DIR / "proxies.json"
    if proxy_file.exists():
        proxy_pool.load_from_file(str(proxy_file))

    progress_queue = []

    def emit(msg, data=None):
        progress_queue.append((msg, data))
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] {msg}")

    async def progress_cb(msg, data=None):
        emit(msg, data)

    scraper = TikTokScraper(
        browser_channel=args.browser,
        headless=False,
        proxy_pool=proxy_pool,
        checkpoint=checkpoint,
        progress_callback=progress_cb,
    )

    try:
        # ── 启动浏览器（CDP 失败自动降级）──
        USER_DATA_DIR = BASE_DIR / "browser_profile"
        if args.cdp_port and args.cdp_port > 0:
            emit(f"连接CDP浏览器 (端口 {args.cdp_port})...")
            try:
                await scraper.connect_over_cdp(args.cdp_port)
            except Exception as cdp_err:
                logger.warning("CDP 连接失败 (%s)，降级为独立启动", cdp_err)
                emit(f"⚠️ CDP 连接失败 ({cdp_err})，降级为独立启动模式...")
                await scraper.start_browser(user_data_dir=str(USER_DATA_DIR))
        else:
            await scraper.start_browser(user_data_dir=str(USER_DATA_DIR))

        logged_in = await scraper.ensure_logged_in()
        if not logged_in:
            print("❌ 登录失败，请检查浏览器")
            return

        # ── 抓取 ──
        emit("开始采集数据...")
        enrich_top = cfg.get("video_scraping", {}).get("enrich_top", 0)
        scraped_data = await scraper.run_analysis(
            keywords=keywords, region=args.region,
            accounts_per_keyword=args.accounts,
            videos_per_account=args.videos,
            comments_per_video=args.comments,
            comments_video_count=3,
            enrich_top=enrich_top,
        )
        emit(f"采集完成: {scraped_data['total_accounts']}账号, "
             f"{scraped_data['total_videos']}视频, {scraped_data['total_comments']}评论")

        all_accounts = scraped_data.get("accounts", [])
        all_videos = scraped_data.get("videos", [])
        all_comments = scraped_data.get("comments", [])

        # ── P0: 账号过滤 ──
        af_cfg = AccountFilterConfig(**cfg.get("account_filter", {}))
        if af_cfg.enabled:
            af = AccountFilter(af_cfg)
            before = len(all_accounts)
            all_accounts, removed = af.filter(all_accounts)
            if removed:
                emit(f"账号过滤: {before} → {len(all_accounts)}")

        # ── P1: 账号评分 ──
        as_cfg = AccountScorerConfig(**cfg.get("account_scorer", {}))
        if as_cfg.enabled and all_accounts:
            scorer = AccountScorer(as_cfg)
            all_accounts = scorer.score_all(all_accounts)
            emit(f"账号评分: {[(a['username'], a['score']) for a in all_accounts[:3]]}")

        # ── P3: 视频过滤+评分 ──
        vf_cfg = VideoFilterConfig(**cfg.get("video_filter", {}))
        if vf_cfg.enabled and all_videos:
            vf = VideoFilter(vf_cfg)
            all_videos, _ = vf.filter(all_videos)
        vs_cfg = VideoScorerConfig(**cfg.get("video_scorer", {}))
        if vs_cfg.enabled and all_videos:
            vs = VideoScorer(vs_cfg)
            all_videos = vs.score_all(all_videos)

        # ── 爆款发现 ──
        vd_cfg = ViralDetectorConfig(**cfg.get("viral_detector", {}))
        viral_data = None
        if vd_cfg.enabled and all_videos:
            vd = ViralDetector(vd_cfg)
            viral_data = vd.analyze(all_videos, all_accounts)
            for acc in all_accounts:
                uname = acc.get("username", "")
                if uname in viral_data.get("per_account", {}):
                    acc["viral_profile"] = viral_data["per_account"][uname]
            emit(f"爆款Top: {len(viral_data['top_viral_videos'])}条")

        # ── P5: 意图识别 ──
        intent_data = None
        id_cfg = IntentDetectorConfig(**cfg.get("intent_detector", {}))
        if id_cfg.enabled and all_comments:
            detector = IntentDetector(id_cfg)
            intent_data = detector.analyze_comments(all_comments)
            all_comments = intent_data["comments"]
            s = intent_data["summary"]
            emit(f"意图识别: {s['comments_with_intent']}/{s['total_comments']}条 ({s['intent_rate']:.0%})")

        # ── 对标参考视频评分 ──
        ref_data = None
        rvs_cfg = ReferenceVideoScorerConfig(**cfg.get("reference_video_scorer", {}))
        if rvs_cfg.enabled and all_videos and all_comments and all_accounts:
            emit("🎯 正在对标参考视频评分...")
            ref_scorer = ReferenceVideoScorer(rvs_cfg)
            ref_data = ref_scorer.score_all(all_videos, all_comments, all_accounts)
            top_list = ref_data.get("top_reference_videos", [])
            emit(f"🎯 对标参考视频: {len(top_list)} 条通过门控 (共 {len(all_videos)} 条视频)")

        # 更新
        scraped_data["accounts"] = all_accounts
        scraped_data["videos"] = all_videos
        scraped_data["comments"] = all_comments
        scraped_data["total_accounts"] = len(all_accounts)
        scraped_data["total_videos"] = len(all_videos)
        scraped_data["total_comments"] = len(all_comments)

        # 注入对标参考视频数据（供导出使用）
        if ref_data:
            scraped_data["reference_videos"] = ref_data.get("top_reference_videos", [])
            scraped_data["reference_benchmarks"] = ref_data.get("reference_benchmarks", {})

        # ── P6: AI分析 ──
        analysis_data = None
        if args.ai and args.openai_key:
            emit("AI分析中...")
            try:
                analyzer = TikTokAnalyzer(
                    api_key=args.openai_key,
                    base_url=args.openai_base or None,
                    model=args.openai_model,
                )
                ai_cfg = cfg.get("ai_analysis", {})
                if ai_cfg.get("use_scored_data", True):
                    analysis_data = await analyzer.analyze_enhanced(
                        scraped_data, intent_data=intent_data, config=ai_cfg
                    )
                else:
                    analysis_data = await analyzer.analyze(scraped_data)
                emit("AI分析完成")
            except Exception as e:
                logger.error("AI失败: %s", e)
            finally:
                await analyzer.close()

        # ── 导出 ──
        output_dir = args.output or str(BASE_DIR / "data" / task_id)
        csv_files = export_csv(scraped_data, analysis_data, output_dir)
        md_file = export_markdown(scraped_data, analysis_data, keywords, args.region, output_dir)
        emit(f"报告已导出: {output_dir}")

        print(f"\n✅ 完成!")
        print(f"   📊 {len(all_accounts)}账号 | 🎬 {len(all_videos)}视频 | 💬 {len(all_comments)}评论")
        if viral_data:
            print(f"   🔥 {len(viral_data['top_viral_videos'])}条爆款视频")
        if ref_data:
            print(f"   🎯 {len(ref_data['top_reference_videos'])}条对标参考视频")
        if intent_data:
            print(f"   🧠 {intent_data['summary']['comments_with_intent']}条采购意图评论")
        print(f"   📄 报告: {md_file}")

    except Exception as e:
        logger.exception("任务异常")
        print(f"❌ 错误: {e}")
    finally:
        await scraper.close()


if __name__ == "__main__":
    asyncio.run(main())
