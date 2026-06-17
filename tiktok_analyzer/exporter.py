"""
TikTok 竞争对手分析系统 - CSV / Markdown 报告导出
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
        writer.writerow(["用户名", "昵称", "粉丝数", "点赞数", "关注数", "视频数", "认证", "简介", "地区", "链接"])
        for a in scraped_data.get("accounts", []):
            writer.writerow([
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
            ])
    files["accounts"] = str(acc_file)

    # 2. 视频 CSV
    vid_file = out_dir / "videos.csv"
    with open(vid_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["账号", "视频ID", "标题/文案", "标签", "播放量", "点赞数", "评论数", "分享数", "时长(秒)", "音乐", "链接"])
        for v in scraped_data.get("videos", []):
            writer.writerow([
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
            ])
    files["videos"] = str(vid_file)

    # 3. 评论 CSV
    cmt_file = out_dir / "comments.csv"
    with open(cmt_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["账号", "视频ID", "用户名", "评论内容", "点赞数"])
        for c in scraped_data.get("comments", []):
            writer.writerow([
                c.get("account_username", ""),
                c.get("video_id", ""),
                c.get("username", ""),
                c.get("text", ""),
                c.get("likes", 0),
            ])
    files["comments"] = str(cmt_file)

    # 4. 分析结果 CSV
    if analysis_data:
        ana_file = out_dir / "analysis.csv"
        with open(ana_file, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["维度", "发现", "证据", "优先级", "建议"])
            for dim in ["business_needs", "purchase_needs", "pain_points"]:
                dim_label = {"business_needs": "商业需求", "purchase_needs": "采购需求", "pain_points": "用户痛点"}.get(dim, dim)
                for item in analysis_data.get(dim, []):
                    writer.writerow([
                        dim_label,
                        item.get("finding", ""),
                        item.get("evidence", ""),
                        item.get("priority", 0),
                        item.get("suggestion", ""),
                    ])
        files["analysis"] = str(ana_file)

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

    lines.append(f"# TikTok 竞争对手分析报告")
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
    lines.append(f"| 排名 | 账号 | 昵称 | 粉丝 | 点赞 | 简介 |")
    lines.append(f"|------|------|------|------|------|------|")
    accounts_sorted = sorted(
        scraped_data.get("accounts", []),
        key=lambda x: x.get("follower_count", 0) or 0,
        reverse=True
    )
    for i, a in enumerate(accounts_sorted[:20], 1):
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
        lines.append(f"")

        for dim, label in [("business_needs", "💼 商业需求"), ("purchase_needs", "🛒 采购需求"), ("pain_points", "😤 用户痛点")]:
            items = analysis_data.get(dim, [])
            if items:
                lines.append(f"### {label}")
                lines.append(f"")
                for item in items:
                    lines.append(f"#### {item.get('finding', '')}")
                    lines.append(f"")
                    lines.append(f"- **优先级**: {'⭐' * item.get('priority', 1)} ({item.get('priority', 0)}/10)")
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
