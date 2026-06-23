"""
TikTok 外贸行业对标视频发现系统 - CSV / Markdown 报告导出
"""
import csv
import json
from pathlib import Path
from datetime import datetime
from typing import Optional

from .logger import setup_logger

logger = setup_logger("exporter")

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)


def export_csv(scraped_data: dict, analysis_data: Optional[dict] = None,
               output_dir: Optional[str] = None) -> dict[str, str]:
    """导出 CSV 文件"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(output_dir) if output_dir else DATA_DIR / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    files = {}

    # 1. 账号 CSV
    acc_file = out_dir / "accounts.csv"
    with open(acc_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        # 检查是否有新管道评分数据 (reference_value) 或旧管道 (score)
        has_scores = any(
            a.get("reference_value") is not None or a.get("score") is not None
            for a in scraped_data.get("accounts", [])
        )
        header = ["用户名", "昵称", "粉丝数", "点赞数", "关注数", "视频数", "认证", "简介", "地区", "链接"]
        has_viral = any(a.get("viral_profile") is not None for a in scraped_data.get("accounts", []))
        if has_scores:
            header.extend(["商业价值分", "参考视频数", "高等级视频数"])
        if has_viral:
            header.extend(["平均点赞", "平均评论", "平均分享", "平均互动率", "爆款占比", "互动率基准"])
        writer.writerow(header)
        for a in scraped_data.get("accounts", []):
            row = [
                a.get("username", ""),
                a.get("nickname", ""),
                a.get("follower_count", 0),
                a.get("like_count", 0),
                a.get("following_count", 0),
                a.get("video_count", 0),
                "是" if a.get("verified") else "否",
                a.get("bio", ""),
                a.get("region", ""),
                a.get("url", ""),
            ]
            if has_scores:
                row.extend([
                    a.get("reference_value", a.get("score", "")),
                    a.get("reference_video_count", a.get("tier", "")),
                    a.get("high_tier_video_count", ""),
                ])
            if has_viral:
                vp = a.get("viral_profile") or {}
                row.extend([
                    vp.get("avg_likes", ""),
                    vp.get("avg_comments", ""),
                    vp.get("avg_shares", ""),
                    vp.get("avg_engagement_rate", ""),
                    vp.get("viral_ratio", ""),
                    (vp.get("vs_global_benchmark") or {}).get("engagement", ""),
                ])
            writer.writerow(row)
    files["accounts"] = str(acc_file)

    # 2. 视频 CSV
    vid_file = out_dir / "videos.csv"
    with open(vid_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        all_vids = scraped_data.get("videos", [])
        # 检测新管道字段
        has_quick_score = any(v.get("quick_score") is not None for v in all_vids)
        has_final_score = any(v.get("final_score") is not None for v in all_vids)
        has_old_scores = any(v.get("score") is not None for v in all_vids)
        has_reference = any(v.get("is_top_reference") for v in all_vids)

        header = ["账号", "视频ID", "标题/文案", "标签", "播放量", "点赞数", "评论数", "分享数", "时长(秒)", "音乐", "链接"]
        if has_quick_score:
            header.extend(["QuickScore", "产品相关度", "视频质量"])
        if has_final_score:
            header.extend(["FinalScore", "商业意图", "等级", "是否对标参考", "参考排名"])
        if has_old_scores and not (has_quick_score or has_final_score):
            header.extend(["综合评分", "等级", "病毒系数"])
        if has_reference:
            header.extend(["采购意向评论数", "采购意向占比", "对标参考评分", "对标等级"])
        writer.writerow(header)

        for v in all_vids:
            row = [
                v.get("account_username", ""),
                v.get("id", ""),
                v.get("desc", ""),
                ",".join(v.get("tags", [])),
                v.get("play_count", 0),
                v.get("digg_count", 0),
                v.get("comment_count", 0),
                v.get("share_count", 0),
                v.get("duration", 0),
                v.get("music", ""),
                v.get("url", ""),
            ]
            if has_quick_score:
                row.extend([
                    v.get("quick_score", ""),
                    v.get("product_relevance", ""),
                    v.get("video_quality_score", ""),
                ])
            if has_final_score:
                row.extend([
                    v.get("final_score", ""),
                    v.get("commercial_intent", ""),
                    v.get("tier", ""),
                    "是" if v.get("is_top_reference") else "否",
                    v.get("reference_rank", ""),
                ])
            if has_old_scores and not (has_quick_score or has_final_score):
                row.extend([
                    v.get("score", ""),
                    v.get("tier", ""),
                    v.get("virality", ""),
                ])
            if has_reference:
                row.extend([
                    v.get("purchase_intent_comments", 0),
                    f"{v.get('purchase_intent_ratio', 0):.2%}" if v.get("purchase_intent_ratio") is not None else "",
                    v.get("reference_score", v.get("final_score", "")),
                    v.get("reference_tier", v.get("tier", "")),
                ])
            writer.writerow(row)
    files["videos"] = str(vid_file)

    # 3. 评论 CSV
    cmt_file = out_dir / "comments.csv"
    with open(cmt_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        has_intents = any(c.get("has_intent") is not None for c in scraped_data.get("comments", []))
        header = ["账号", "视频ID", "用户名", "评论内容", "点赞数", "时间"]
        if has_intents:
            header.extend(["含商业意图", "意图类型", "意图置信度"])
        writer.writerow(header)
        for c in scraped_data.get("comments", []):
            row = [
                c.get("account_username", ""),
                c.get("video_id", ""),
                c.get("username", ""),
                c.get("text", ""),
                c.get("likes", 0),
                c.get("time", ""),
            ]
            if has_intents:
                row.extend([
                    "是" if c.get("has_intent") else "否",
                    c.get("top_intent", ""),
                    c.get("top_intent_confidence", ""),
                ])
            writer.writerow(row)
    files["comments"] = str(cmt_file)

    # 4. 分析结果 CSV
    if analysis_data:
        ana_file = out_dir / "analysis.csv"
        with open(ana_file, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            dim_labels = {
                "business_needs": "商业需求",
                "purchase_needs": "采购需求",
                "pain_points": "用户痛点",
                "market_gaps": "市场空白",
                "competitor_insights": "竞品洞察",
                "actionable_strategy": "可执行策略",
            }
            header = ["维度", "发现", "证据", "优先级", "置信度", "建议"]
            writer.writerow(header)
            for dim in dim_labels:
                for item in analysis_data.get(dim, []):
                    writer.writerow([
                        dim_labels.get(dim, dim),
                        item.get("finding", ""),
                        item.get("evidence", ""),
                        item.get("priority", 0),
                        item.get("confidence", ""),
                        item.get("suggestion", ""),
                    ])
        files["analysis"] = str(ana_file)

    # 5. 对标参考视频 CSV (新管道: is_top_reference + final_score)
    all_videos = scraped_data.get("videos", [])
    reference_videos = scraped_data.get("reference_videos", [])
    if not reference_videos:
        reference_videos = [v for v in all_videos if v.get("is_top_reference")]
    if reference_videos:
        ref_file = out_dir / "reference_videos.csv"
        with open(ref_file, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow([
                "排名", "账号", "视频ID", "描述", "播放量", "点赞数", "评论数",
                "分享数", "产品相关度", "视频质量", "商业意图",
                "FinalScore", "等级", "采购意向评论数", "采购意向占比",
                "标签", "链接"
            ])
            for v in reference_videos:
                # 计算互动率
                likes = v.get("digg_count", 0) or 0
                comments = v.get("comment_count", 0) or 0
                shares = v.get("share_count", 0) or 0
                plays = v.get("play_count", 1) or 1
                er = (likes + comments + shares) / plays if plays > 0 else 0

                writer.writerow([
                    v.get("reference_rank", ""),
                    v.get("account_username", ""),
                    v.get("id", ""),
                    (v.get("desc") or "")[:100],
                    v.get("play_count", 0),
                    likes,
                    comments,
                    shares,
                    v.get("product_relevance", ""),
                    v.get("video_quality_score", ""),
                    v.get("commercial_intent", ""),
                    v.get("final_score", v.get("reference_score", "")),
                    v.get("tier", v.get("reference_tier", "")),
                    v.get("purchase_intent_comments", 0),
                    f"{v.get('purchase_intent_ratio', 0):.2%}" if v.get("purchase_intent_ratio") is not None else "",
                    ",".join(v.get("tags", [])),
                    v.get("url", ""),
                ])
        files["reference_videos"] = str(ref_file)

    logger.info("CSV 文件已导出到: %s", out_dir)
    return files


def export_markdown(scraped_data: dict, analysis_data: Optional[dict] = None,
                    keywords: list = None, region: str = "",
                    output_dir: Optional[str] = None) -> str:
    """导出 Markdown 报告，返回文件路径"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(output_dir) if output_dir else DATA_DIR / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    md_file = out_dir / "report.md"
    lines = []

    lines.append(f"# TikTok 外贸行业对标视频发现报告")
    lines.append(f"")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"")
    lines.append(f"**关键词**: {', '.join(keywords) if keywords else 'N/A'}")
    lines.append(f"**目标地区**: {region or '不限'}")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")

    # 概览
    lines.append(f"## 📊 数据概览")
    lines.append(f"")
    lines.append(f"| 指标 | 数量 |")
    lines.append(f"|------|------|")
    lines.append(f"| 分析账号 | {scraped_data.get('total_accounts', 0)} |")
    lines.append(f"| 抓取视频 | {scraped_data.get('total_videos', 0)} |")
    lines.append(f"| 抓取评论 | {scraped_data.get('total_comments', 0)} |")
    lines.append(f"")

    # 账号排名
    lines.append(f"## 🏆 账号排行榜（按粉丝数）")
    lines.append(f"")
    has_scores = any(
        a.get("reference_value") is not None or a.get("score") is not None
        for a in scraped_data.get("accounts", [])
    )
    if has_scores:
        lines.append(f"| 排名 | 账号 | 昵称 | 粉丝 | 点赞 | 商业价值分 | 参考视频数 | 简介 |")
        lines.append(f"|------|------|------|------|------|------------|------------|------|")
    else:
        lines.append(f"| 排名 | 账号 | 昵称 | 粉丝 | 点赞 | 简介 |")
        lines.append(f"|------|------|------|------|------|------|")
    accounts_sorted = sorted(
        scraped_data.get("accounts", []),
        key=lambda x: x.get("reference_value", x.get("follower_count", 0)) or 0,
        reverse=True
    )
    for i, a in enumerate(accounts_sorted[:20], 1):
        if has_scores:
            rv = a.get("reference_value", a.get("score", "-"))
            lines.append(
                f"| {i} | @{a.get('username', '')} | {a.get('nickname', '')} | "
                f"{_fmt_count(a.get('follower_count', 0))} | {_fmt_count(a.get('like_count', 0))} | "
                f"{rv}{'⭐' if rv > 60 else ''} | "
                f"{a.get('reference_video_count', a.get('tier', ''))} | "
                f"{a.get('bio', '')[:50]} |"
            )
        else:
            lines.append(
                f"| {i} | @{a.get('username', '')} | {a.get('nickname', '')} | "
                f"{_fmt_count(a.get('follower_count', 0))} | {_fmt_count(a.get('like_count', 0))} | "
                f"{a.get('bio', '')[:50]} |"
            )
    lines.append(f"")

    # 热门视频
    lines.append(f"## 🔥 热门视频 TOP 20")
    lines.append(f"")
    lines.append(f"| 排名 | 账号 | 标题 | 点赞 | 评论 | 分享 |")
    lines.append(f"|------|------|------|------|------|------|")
    videos_sorted = sorted(
        scraped_data.get("videos", []),
        key=lambda x: x.get("digg_count", 0) or 0,
        reverse=True
    )
    for i, v in enumerate(videos_sorted[:20], 1):
        desc = (v.get("desc", "") or "")[:50].replace("|", "/")
        lines.append(
            f"| {i} | @{v.get('account_username', '')} | {desc} | "
            f"{_fmt_count(v.get('digg_count', 0))} | {_fmt_count(v.get('comment_count', 0))} | "
            f"{_fmt_count(v.get('share_count', 0))} |"
        )
    lines.append(f"")

    # ── 对标参考视频 TOP N ──
    all_videos = scraped_data.get("videos", [])
    reference_videos = scraped_data.get("reference_videos", [])
    if not reference_videos:
        reference_videos = sorted(
            [v for v in all_videos if v.get("is_top_reference")],
            key=lambda x: x.get("final_score", x.get("reference_score", 0)), reverse=True
        )
    if reference_videos:
        lines.append(f"## 🎯 对标参考视频 TOP {len(reference_videos)}")
        lines.append(f"")
        lines.append(f"> 通过 QuickScore + 商业意图验证的高参考价值视频，适合运营团队对标模仿。")
        lines.append(f"")
        lines.append(f"| 排名 | 账号 | 描述 | 播放 | 点赞 | 评论 | FinalScore | 商业意图 | 采购意向占比 | 等级 |")
        lines.append(f"|------|------|------|------|------|------|-----------|----------|------------|------|")
        for v in reference_videos:
            desc = (v.get("desc") or "")[:50].replace("|", "/")
            lines.append(
                f"| {v.get('reference_rank', '')} | @{v.get('account_username', '')} | {desc} | "
                f"{_fmt_count(v.get('play_count', 0))} | {_fmt_count(v.get('digg_count', 0))} | "
                f"{_fmt_count(v.get('comment_count', 0))} | "
                f"{v.get('final_score', v.get('reference_score', ''))} | "
                f"{v.get('commercial_intent', '')} | "
                f"{v.get('purchase_intent_ratio', 0):.1%} | "
                f"{v.get('tier', v.get('reference_tier', ''))} |"
            )
        lines.append(f"")

    # ── 爆款视频 TOP N ──
    viral_videos = [v for v in all_videos if v.get("is_global_top10")]
    if viral_videos:
        lines.append(f"## 🔥 爆款视频 TOP {len(viral_videos)}")
        lines.append(f"")
        lines.append(f"| 排名 | 账号 | 描述 | 播放 | 点赞 | 评论 | 分享 | 互动率 | 评分 | 等级 |")
        lines.append(f"|------|------|------|------|------|------|------|--------|------|------|")
        for v in viral_videos:
            desc = (v.get("desc") or "")[:50].replace("|", "/")
            er = v.get("_engagement_rate", 0)
            lines.append(
                f"| {v.get('global_rank', '')} | @{v.get('account_username', '')} | {desc} | "
                f"{_fmt_count(v.get('play_count', 0))} | {_fmt_count(v.get('digg_count', 0))} | "
                f"{_fmt_count(v.get('comment_count', 0))} | {_fmt_count(v.get('share_count', 0))} | "
                f"{er:.1%} | {v.get('score', '')} | {v.get('tier', '')} |"
            )
        lines.append(f"")

    # ── 账号爆款特征 ──
    accounts_with_viral = [
        a for a in scraped_data.get("accounts", [])
        if a.get("viral_profile")
    ]
    if accounts_with_viral:
        lines.append(f"## 📊 账号爆款特征")
        lines.append(f"")
        for a in accounts_with_viral[:10]:  # Top 10 账号
            vp = a.get("viral_profile", {})
            nickname = a.get("nickname", "") or vp.get("nickname", "")
            lines.append(f"### @{a.get('username', '')} — {nickname}")
            lines.append(f"")
            lines.append(f"| 指标 | 数值 | vs 全局 |")
            lines.append(f"|------|------|---------|")
            vs = vp.get("vs_global_benchmark", {})
            lines.append(f"| 平均点赞 | {_fmt_count(vp.get('avg_likes', 0))} | {vs.get('likes', 'N/A')} |")
            lines.append(f"| 平均评论 | {_fmt_count(vp.get('avg_comments', 0))} | {vs.get('comments', 'N/A')} |")
            lines.append(f"| 平均分享 | {_fmt_count(vp.get('avg_shares', 0))} | {vs.get('shares', 'N/A')} |")
            lines.append(f"| 平均互动率 | {vp.get('avg_engagement_rate', 0):.1%} | {vs.get('engagement', 'N/A')} |")
            lines.append(f"| 爆款占比 | {vp.get('viral_ratio', 0):.0%} ({vp.get('viral_count', 0)}/{vp.get('total_videos', 0)}) | - |")
            lines.append(f"")

            # 最高互动率视频
            h = vp.get("highest_engagement_video", {})
            if h:
                lines.append(f"**🔥 最高互动率视频**: [{h.get('desc', '')[:60]}]({h.get('url', '')}) "
                           f"({h.get('engagement_rate', 0):.1%}, 播放{_fmt_count(h.get('play_count', 0))})")
                lines.append(f"")

            # Top 5 视频列表
            top5 = vp.get("top_videos", [])[:5]
            if top5:
                lines.append(f"**Top 5 互动率视频**:")
                lines.append(f"")
                for j, tv in enumerate(top5, 1):
                    lines.append(f"{j}. [{tv.get('desc', '')[:60]}]({tv.get('url', '')}) — "
                               f"互动率 {tv.get('engagement_rate', 0):.1%}, "
                               f"👍{_fmt_count(tv.get('digg_count', 0))} "
                               f"💬{_fmt_count(tv.get('comment_count', 0))} "
                               f"🔄{_fmt_count(tv.get('share_count', 0))}")
                lines.append(f"")

    # ── 爆款共性特征 ──
    viral_chars = scraped_data.get("viral_characteristics")
    if not viral_chars:
        # 尝试从 scraped_data 的扩展属性获取（由 app.py 注入）
        pass
    if viral_chars and viral_chars.get("common_tags"):
        lines.append(f"## 🧬 爆款共性特征")
        lines.append(f"")
        lines.append(f"**分析样本**: {viral_chars.get('sample_size', 0)} 条爆款视频")
        lines.append(f"")

        # 高频标签
        tags = viral_chars.get("common_tags", [])
        if tags:
            lines.append(f"### 🏷️ 爆款高频标签")
            lines.append(f"")
            tag_items = [f"`#{t['tag']}`({t['count']})" for t in tags[:15]]
            lines.append(" · ".join(tag_items))
            lines.append(f"")

        # 最优时长
        dr = viral_chars.get("optimal_duration_range", [])
        avg_dur = viral_chars.get("avg_duration", 0)
        if dr:
            lines.append(f"### ⏱ 最优时长")
            lines.append(f"")
            lines.append(f"- 爆款视频平均时长: **{avg_dur}s**")
            lines.append(f"- 最优时长区间: **{dr[0]}s - {dr[1]}s**")
            vs_dur = viral_chars.get("vs_global_avg_duration", 0)
            if vs_dur:
                direction = "长于" if vs_dur > 0 else "短于"
                lines.append(f"- vs 全局平均: {direction} {abs(vs_dur)}s")
            lines.append(f"")

        # 描述特征
        dp = viral_chars.get("description_patterns", {})
        if dp:
            lines.append(f"### 📝 描述特征")
            lines.append(f"")
            lines.append(f"| 特征 | 数值 |")
            lines.append(f"|------|------|")
            lines.append(f"| 平均描述长度 | {dp.get('avg_length', 0)} 字符 |")
            lines.append(f"| Emoji 使用率 | {dp.get('emoji_usage_rate', 0):.0%} |")
            lines.append(f"| CTA 使用率 | {dp.get('cta_usage_rate', 0):.0%} |")
            lines.append(f"| 问句使用率 | {dp.get('question_usage_rate', 0):.0%} |")
            lines.append(f"")

    # 高频标签
    lines.append(f"## 🏷️ 高频标签")
    lines.append(f"")
    tag_counter = {}
    for v in scraped_data.get("videos", []):
        for t in v.get("tags", []):
            tag_counter[t] = tag_counter.get(t, 0) + 1
    top_tags = sorted(tag_counter.items(), key=lambda x: x[1], reverse=True)[:30]
    for tag, cnt in top_tags:
        lines.append(f"- `#{tag}` ({cnt} 次)")
    lines.append(f"")

    # ── 采购意向分析 ──
    reference_vids = [v for v in all_videos if v.get("is_top_reference") or v.get("reference_score")]
    if reference_vids:
        lines.append(f"## 🧠 采购意向分析")
        lines.append(f"")
        intent_total = sum(v.get("purchase_intent_comments", 0) for v in reference_vids)
        comment_total = sum(v.get("comment_count", 0) for v in reference_vids)
        lines.append(f"- **对标参考视频数**: {len(reference_vids)} 条")
        lines.append(f"- **含采购意向评论数**: {intent_total} 条")
        lines.append(f"- **采购意向占比**: {intent_total / max(comment_total, 1):.1%}")
        lines.append(f"")
        by_intent = sorted(reference_vids, key=lambda x: x.get("purchase_intent_ratio", 0), reverse=True)[:5]
        lines.append(f"### 采购意向最高的视频")
        lines.append(f"")
        lines.append(f"| # | 账号 | 描述 | 采购意向评论 | 采购意向占比 | 评分 |")
        lines.append(f"|---|------|------|------------|------------|------|")
        for i, v in enumerate(by_intent, 1):
            desc = (v.get("desc") or "")[:40].replace("|", "/")
            lines.append(
                f"| {i} | @{v.get('account_username', '')} | {desc} | "
                f"{v.get('purchase_intent_comments', 0)} | {v.get('purchase_intent_ratio', 0):.1%} | "
                f"{v.get('final_score', v.get('reference_score', ''))} |"
            )
        lines.append(f"")

    # 评论热词
    lines.append(f"## 💬 评论关键词")
    lines.append(f"")
    all_comments_text = " ".join([c.get("text", "") for c in scraped_data.get("comments", [])])
    # 简单词频统计
    import re
    words = re.findall(r'[\w\u4e00-\u9fff]+', all_comments_text.lower())
    word_freq = {}
    stopwords = {"the", "a", "an", "is", "are", "was", "were", "i", "you", "he", "she",
                 "it", "we", "they", "to", "of", "in", "for", "on", "and", "or", "but",
                 "not", "this", "that", "with", "as", "at", "be", "my", "me", "so",
                 "do", "no", "if", "all", "just", "like", "have", "has", "from", "very",
                 "your", "can", "will", "what", "get", "about", "been", "one", "would",
                 "there", "their", "more", "when", "which", "who", "them", "some", "也",
                 "的", "了", "是", "我", "你", "他", "她", "它", "们", "这", "那", "不",
                 "和", "在", "有", "会", "就", "都", "还", "要", "能", "个", "说", "人",
                 "很", "去", "来", "到", "看", "对", "着", "想", "好", "知道", "吗", "吧"}
    for w in words:
        if len(w) < 2 or w in stopwords:
            continue
        word_freq[w] = word_freq.get(w, 0) + 1
    top_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:30]
    for word, cnt in top_words:
        lines.append(f"- `{word}` ({cnt} 次)")
    lines.append(f"")

    # AI 分析
    if analysis_data and "error" not in analysis_data:
        lines.append(f"## 🤖 AI 分析")
        lines.append(f"")
        lines.append(f"**总结**: {analysis_data.get('summary', 'N/A')}")
        mos = analysis_data.get('market_opportunity_score')
        if mos is not None:
            lines.append(f"**市场机会评分**: {mos}/100")
        lines.append(f"")

        for dim, label in [
            ("business_needs", "💼 商业需求"),
            ("purchase_needs", "🛒 采购需求"),
            ("pain_points", "😤 用户痛点"),
            ("market_gaps", "🎯 市场空白"),
            ("competitor_insights", "🔍 竞品洞察"),
            ("actionable_strategy", "🚀 可执行策略"),
        ]:
            items = analysis_data.get(dim, [])
            if items:
                lines.append(f"### {label}")
                lines.append(f"")
                for item in items:
                    lines.append(f"#### {item.get('finding', '')}")
                    lines.append(f"")
                    lines.append(f"- **优先级**: {'⭐' * item.get('priority', 1)} ({item.get('priority', 0)}/10)")
                    if item.get('confidence') is not None:
                        lines.append(f"- **置信度**: {item.get('confidence', 0):.0%}")
                    lines.append(f"- **证据**: {item.get('evidence', '')}")
                    lines.append(f"- **建议**: {item.get('suggestion', '')}")
                    lines.append(f"")
    elif analysis_data:
        lines.append(f"## 🤖 AI 分析")
        lines.append(f"")
        lines.append(f"⚠ 分析出错: {analysis_data.get('error', '未知错误')}")
        lines.append(f"")

    md_content = "\n".join(lines)
    with open(md_file, "w", encoding="utf-8") as f:
        f.write(md_content)

    logger.info("Markdown 报告已导出: %s", md_file)
    return str(md_file)


def _fmt_count(n) -> str:
    """格式化数字"""
    try:
        n = int(n)
    except (ValueError, TypeError):
        return str(n)
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)
