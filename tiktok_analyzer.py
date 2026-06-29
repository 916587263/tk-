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
import os
import sys
import threading
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from tiktok_analyzer.scraper import TikTokScraper
from tiktok_analyzer.analyzer import TikTokAnalyzer
from tiktok_analyzer.exporter import export_csv, export_markdown
from tiktok_analyzer.checkpoint import CheckpointManager
from tiktok_analyzer.proxy_pool import ProxyPool
from tiktok_analyzer.logger import setup_logger
from tiktok_analyzer.keyword_expander import KeywordExpander, KeywordCache

# ── 新管道 v2.0 模块 ──
from tiktok_analyzer.unified_scorer import FinalScorer, FinalScorerConfig
from tiktok_analyzer.comment_classifier import CommentClassifier, CommentClassifierConfig
from tiktok_analyzer.intent_detector import QuickIntentScanner

# ── DEPRECATED 旧管道模块 (保留导入避免 import error, 不再使用) ──
# from tiktok_analyzer.account_filter import AccountFilter, AccountFilterConfig
# from tiktok_analyzer.account_scorer import AccountScorer, AccountScorerConfig
# from tiktok_analyzer.video_scorer import VideoScorer, VideoFilter, VideoScorerConfig, VideoFilterConfig
# from tiktok_analyzer.viral_detector import ViralDetector, ViralDetectorConfig
# from tiktok_analyzer.intent_detector import IntentDetector, IntentDetectorConfig
# from tiktok_analyzer.reference_video_scorer import ReferenceVideoScorer, ReferenceVideoScorerConfig

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
        locale=cfg.get("locale", "zh-CN"),
        timezone_id=cfg.get("timezone_id", "Asia/Shanghai"),
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
        emit(f"采集完成: {scraped_data['total_videos']} 视频 "
             f"(来自 {scraped_data['total_accounts']} 个账号), "
             f"{scraped_data['total_comments']} 评论")

        all_accounts = scraped_data.get("accounts", [])
        all_videos = scraped_data.get("videos", [])
        all_comments = list(scraped_data.get("comments", []))
        deep_analyzed_videos = scraped_data.get("deep_analyzed_videos", [])

        emit(
            f"管道数据: {len(all_videos)} 条通过 QuickScore, "
            f"{len(deep_analyzed_videos)} 条深度分析 ({len(all_comments)} 条评论)"
        )

        # ═══════════════════════════════════════════════════════════
        # 阶段 3: 商业意图评分
        #   enable_llm=true  → CommentClassifier (LLM) 仅对 Top 20 候选
        #   enable_llm=false → QuickIntentScanner (规则引擎, 零成本)
        # ═══════════════════════════════════════════════════════════
        classify_map = {}       # {video_id: VideoClassifyResult} LLM 结果
        quick_intent_map = {}   # {video_id: dict} 规则引擎结果 (始终构建)
        intent_data = None

        enable_llm = cfg.get("enable_llm", False)
        llm_provider = cfg.get("llm_provider", "openai")
        llm_model = cfg.get("llm_model", "gpt-4o-mini")
        llm_base_url = cfg.get("llm_base_url", "")

        # ── 始终构建规则引擎分数 (零成本, Fallback) ──
        quick_scanner = QuickIntentScanner(min_hits=1)
        for v in deep_analyzed_videos:
            deep_c = v.get("_deep_comments", [])
            score_data = quick_scanner.score_comments(deep_c)
            quick_intent_map[v.get("id", "")] = score_data
            # 写入视频字段
            v["intent_ratio"] = score_data["intent_ratio"]
            v["intent_quality_score"] = score_data["intent_quality_score"]
            v["intent_diversity"] = score_data["intent_diversity"]
            v["actionable_intent_count"] = score_data["actionable_intent_count"]
            v["categories_hit"] = score_data["categories_hit"]
            v["is_weak_reference"] = (score_data["actionable_intent_count"] == 0)

        emit(
            f"阶段 3 (规则引擎): {len(deep_analyzed_videos)} 条视频完成意图评分 "
            f"(命中类别: {sum(1 for s in quick_intent_map.values() if s['intent_diversity'] > 0)})"
        )

        # ── 可选 LLM 增强层 ──
        api_key = args.openai_key or os.environ.get("DEEPSEEK_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
        if enable_llm and deep_analyzed_videos and api_key:
            cc_cfg_dict = cfg.get("comment_classifier", {})
            cc_cfg = CommentClassifierConfig(**{
                k: v for k, v in cc_cfg_dict.items()
                if k in CommentClassifierConfig.__dataclass_fields__
            })

            # LLM 仅分类 Top 20 候选视频 (不是全量)
            candidates = sorted(
                deep_analyzed_videos,
                key=lambda v: v.get("actionable_intent_count", 0),
                reverse=True
            )[:20]
            emit(f"阶段 3+ (LLM 增强): {llm_provider}/{llm_model} 分析 Top {len(candidates)} 候选视频...")

            openai_proxy = scraper._current_proxy
            base_url = llm_base_url or args.openai_base or None
            model = llm_model if llm_provider != "openai" else (args.openai_model or cc_cfg.model)

            classifier = CommentClassifier(
                api_key=api_key,
                config=cc_cfg,
                base_url=base_url,
                model=model,
                proxy=openai_proxy,
            )

            try:
                batch_result = await classifier.classify_videos(candidates)
                llm_classify_map = {}
                for vr in batch_result.video_results:
                    llm_classify_map[vr.video_id] = vr

                # LLM 结果覆盖规则引擎结果 (仅对候选视频)
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
                        # 标注评论的 has_intent/top_intent (仅深度评论)
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
                    f"LLM 增强完成: {len(llm_classify_map)} 条视频精确分类 "
                    f"(LLM 调用 {batch_result.total_llm_calls} 次)"
                )
            except Exception as e:
                logger.warning("LLM 分类失败: %s, 降级使用规则引擎分数", e)
                emit(f"LLM 分类失败: {e}, 降级使用规则引擎分数")
            finally:
                await classifier.close()

        elif enable_llm and not api_key and deep_analyzed_videos:
            emit("未设置 API Key (DEEPSEEK_API_KEY / OPENAI_API_KEY), 使用规则引擎")
        elif not enable_llm and deep_analyzed_videos:
            emit("LLM 增强已关闭 (enable_llm=false), 使用规则引擎")

        # 将深度评论加入 all_comments
        for v in deep_analyzed_videos:
            deep_c = v.get("_deep_comments", [])
            for c in deep_c:
                c["account_username"] = v.get("account_username", "")
            all_comments.extend(deep_c)

        # ═══════════════════════════════════════════════════════════
        # 阶段 4: FinalScore 终极统一评分 (三权重 30/30/40)
        # 同时接受 LLM 和规则引擎的意图分数
        # ═══════════════════════════════════════════════════════════
        fs_cfg_dict = cfg.get("final_scorer", {})
        fs_cfg = FinalScorerConfig(**{
            k: v for k, v in fs_cfg_dict.items()
            if k in FinalScorerConfig.__dataclass_fields__
        })

        top_references = []
        tiers = {}
        if fs_cfg.enabled and deep_analyzed_videos:
            emit("阶段 4: FinalScore 终极评分 (30/30/40)...")

            final_scorer = FinalScorer(fs_cfg)
            all_scored, top_references = final_scorer.score_all(
                deep_analyzed_videos, classify_map,
                quick_intent_map=quick_intent_map  # 规则引擎 fallback
            )

            # 账号级聚合
            all_accounts = final_scorer.aggregate_accounts(top_references, all_accounts)

            for v in all_scored:
                t = v.get("tier", "D")
                tiers[t] = tiers.get(t, 0) + 1
            emit(
                f"FinalScore: {len(all_scored)} 条评分, {len(top_references)} 条对标参考, "
                f"等级分布: {tiers}"
            )

            if top_references:
                summaries = []
                for i, v in enumerate(top_references[:5]):
                    desc = (v.get('desc') or '')[:60]
                    summaries.append(
                        f"#{i+1} \"{desc}\" "
                        f"(FinalScore={v.get('final_score',0):.0f}, "
                        f"意图={v.get('commercial_intent',0):.0f}, "
                        f"@{v.get('account_username','')})"
                    )
                emit(f"  对标视频 Top 5: {' | '.join(summaries)}")

            # 构建意图摘要 (兼容 LLM 和规则引擎数据源)
            for v in top_references:
                vid = v.get("id", "")
                vr = classify_map.get(vid)
                qi = quick_intent_map.get(vid, {})
                if vr:
                    # LLM 模式: 精确分类的意图评论
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
                    # 规则引擎模式: 从 QuickIntent 构建摘要
                    v["intent_summary"] = {
                        "intent_ratio": qi.get("intent_ratio", 0),
                        "top_categories": qi.get("categories_hit", [])[:5],
                        "sample_comments": [],
                    }
                    v["purchase_intent_ratio"] = qi.get("intent_ratio", 0)
                    v["purchase_intent_comments"] = int(
                        qi.get("intent_ratio", 0) * len(v.get("_deep_comments", []))
                    )
        else:
            top_references = deep_analyzed_videos[:20]
            emit(f"FinalScore 未启用/无深度视频, Top {len(top_references)} 条直接作为参考")

        # 更新 scraped_data
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

        # ═══════════════════════════════════════════════════════════
        # 阶段 5: AI 分析总结 (精简, 仅 Top 20 对标视频)
        # ═══════════════════════════════════════════════════════════
        analysis_data = None
        if args.ai and args.openai_key and top_references:
            emit(f"阶段 5: AI 分析总结 (仅 Top {min(20, len(top_references))} 对标视频)...")
            try:
                openai_proxy = scraper._current_proxy
                analyzer = TikTokAnalyzer(
                    api_key=args.openai_key,
                    base_url=args.openai_base or None,
                    model=args.openai_model,
                    proxy=openai_proxy,
                )
                analysis_data = await analyzer.analyze_top_references(
                    top_references, all_accounts
                )
                emit(
                    f"AI 分析完成 (市场机会评分: {analysis_data.get('market_opportunity_score', 'N/A')})"
                )
            except Exception as e:
                logger.error("AI 分析失败: %s", e)
                emit(f"AI 分析失败: {e}")
            finally:
                await analyzer.close()

        elif args.ai and not args.openai_key:
            emit("未设置 OpenAI API Key, 跳过 AI 分析")

        # ── 导出 ──
        output_dir = args.output or str(BASE_DIR / "data" / task_id)
        csv_files = export_csv(scraped_data, analysis_data, output_dir)
        md_file = export_markdown(scraped_data, analysis_data, keywords, args.region, output_dir)
        emit(f"报告已导出: {output_dir}")

        print(f"\n  完成!")
        print(f"    视频: {len(all_videos)} 条 (其中 {len(top_references)} 条对标参考)")
        print(f"    账号: {len(all_accounts)} 个 | 评论: {len(all_comments)} 条")
        if top_references:
            print(f"    对标参考视频: {len(top_references)} 条 (FinalScore)")
            for i, v in enumerate(top_references[:5]):
                desc = (v.get('desc') or '')[:60]
                print(f"      #{i+1} \"{desc}\" (FinalScore={v.get('final_score',0):.0f}) @{v.get('account_username','')}")
        if analysis_data:
            print(f"   市场机会评分: {analysis_data.get('market_opportunity_score', 'N/A')}")
        print(f"   报告: {md_file}")

    except Exception as e:
        logger.exception("任务异常")
        print(f"❌ 错误: {e}")
    finally:
        await scraper.close()


if __name__ == "__main__":
    asyncio.run(main())
