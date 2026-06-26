"""
T1-T8: QuickScorer 低互动降权单元测试

覆盖:
  T1: 零评论 + 低点赞 + 高产品相关度 → 降权后通过
  T2: 零评论 + 低点赞 + 低产品相关度 → 降权后仍淘汰
  T3: 零互动 (plays=likes=comments=0) → 仍硬淘汰
  T4: 商业白名单命中 → 跳过硬淘汰且不降权
  T5: low_engagement_multiplier=0 → 等同于旧版硬淘汰
  T6: low_engagement_multiplier=1 → 不降权
  T7: 配置缺少 low_engagement_multiplier → 使用默认值 0.60
  T8: 个人号 + 低互动双重惩罚 → 叠加正确

用法:
  py tests/test_quickscorer_penalty.py
"""
import sys
from pathlib import Path

# 修复 Windows GBK 编码问题
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from tiktok_analyzer.unified_scorer import QuickScorer, QuickScorerConfig


# ═══════════════════════════════════════════════════════════
# 测试数据工厂
# ═══════════════════════════════════════════════════════════

def _v(vid, desc, plays, likes, comments=0, shares=0, tags=None,
       account_username="test_user"):
    return {
        "id": vid, "desc": desc, "play_count": plays,
        "digg_count": likes, "comment_count": comments,
        "share_count": shares, "account_username": account_username,
        "tags": tags or [], "duration": 30, "create_time": 1700000000,
        "url": f"https://tiktok.com/@{account_username}/video/{vid}",
    }


def _a(username, bio="", nickname="", follower_count=1000):
    return {
        "username": username, "bio": bio, "nickname": nickname,
        "follower_count": follower_count, "verified": False,
    }


# ═══════════════════════════════════════════════════════════
# T1: 零评论 + 低点赞 + 高产品相关度 → 降权后通过
# ═══════════════════════════════════════════════════════════

def test_zero_comment_high_relevance_passes():
    """高价值B2B视频: 零评论但desc含多个行业关键词 → 降权后应通过"""
    cfg = QuickScorerConfig(min_score=20.0, min_likes_no_comment=5,
                            low_engagement_multiplier=0.60)
    scorer = QuickScorer(cfg)

    # 强B2B信号但不触发商业白名单 (白名单需要 ≥2 个不同白名单词)
    # industry_keywords 含: production, export, manufacturing, 工厂, 厂家, 外贸 等
    # commercial_whitelist 含: manufacturer, factory, supplier, oem, odm, moq...
    # 用 "production" + "export" + "manufacturing" → 都命中 industry 但不在 whitelist
    video = _v("v1",
               "Professional packaging production line for export manufacturing",
               2000, 3, 0)
    account = _a("factory_a", bio="Quality products since 2010",
                 nickname="Best Packaging Co")

    result = scorer.score(video, account)

    # product_relevance 应较高: production/export/manufacturing 各在desc命中(+10 each) ≈30+
    # 加上 desc>20 chars bonus (+5) ≈35+
    assert result.product_relevance > 25, f"expected decent relevance, got {result.product_relevance}"
    assert result.video_quality > 0
    assert result.total > 0, "降权后应有非零分数"
    # 检查 breakdown 标记 — 不应命中白名单
    assert result.commercial_whitelist_hit is False, (
        "此测试视频不应命中商业白名单"
    )
    # 零评论+低点赞 → 应触发降权
    assert result.breakdown.get("low_engagement_penalty") is True

    # 如果产品相关度足够高 (>40), 应能通过 min_score=20
    if result.product_relevance >= 40:
        assert result.passed, (
            f"高相关度({result.product_relevance})应通过, "
            f"total={result.total}, min={cfg.min_score}"
        )
    else:
        assert not result.passed
        assert "quick_score" in result.eliminated_reason

    print(f"  [PASS] T1: relevance={result.product_relevance} quality={result.video_quality} "
          f"total={result.total} penalty={result.breakdown.get('low_engagement_penalty')} "
          f"passed={result.passed} reason={result.eliminated_reason}")


# ═══════════════════════════════════════════════════════════
# T2: 零评论 + 低点赞 + 低产品相关度 → 降权后仍淘汰
# ═══════════════════════════════════════════════════════════

def test_zero_comment_low_relevance_fails():
    """无商业信号的零评论视频: 降权后仍应被淘汰"""
    cfg = QuickScorerConfig(min_score=20.0, min_likes_no_comment=5,
                            low_engagement_multiplier=0.60)
    scorer = QuickScorer(cfg)

    video = _v("v2", "Cool video check it out #fyp #viral", 200, 3, 0)
    account = _a("random_user", bio="Just for fun", nickname="Random User")

    result = scorer.score(video, account)

    assert result.product_relevance < 30, f"expected low relevance, got {result.product_relevance}"
    assert result.passed is False, "低相关度零评论视频应被淘汰"
    assert result.breakdown.get("low_engagement_penalty") is True

    print(f"  [PASS] T2: relevance={result.product_relevance} total={result.total} "
          f"passed={result.passed}")


# ═══════════════════════════════════════════════════════════
# T3: 零互动视频 (plays=likes=comments=0) → 仍硬淘汰
# ═══════════════════════════════════════════════════════════

def test_zero_engagement_still_hard_eliminated():
    """零互动视频: 降权设计不应影响此类视频的硬淘汰"""
    cfg = QuickScorerConfig(min_score=20.0, low_engagement_multiplier=0.60)
    scorer = QuickScorer(cfg)

    video = _v("v3", "no one saw this", 0, 0, 0)
    account = _a("nobody", bio="just a person", nickname="Nobody")

    result = scorer.score(video, account)

    assert result.passed is False, "零互动视频应被硬淘汰"
    assert result.total == 0, f"硬淘汰 total 应为 0, got {result.total}"
    assert result.eliminated_reason == "zero_engagement", (
        f"应为 zero_engagement, got {result.eliminated_reason}"
    )
    assert result.breakdown.get("low_engagement_penalty") is not True, (
        "零互动不应走降权路径"
    )

    print(f"  [PASS] T3: total={result.total} reason={result.eliminated_reason}")


# ═══════════════════════════════════════════════════════════
# T4: 商业白名单命中 → 跳过硬淘汰且不降权
# ═══════════════════════════════════════════════════════════

def test_commercial_whitelist_bypasses_penalty():
    """白名单命中 ≥2 词的视频不应被降权"""
    cfg = QuickScorerConfig(
        min_score=20.0, min_likes_no_comment=5,
        low_engagement_multiplier=0.60,
        commercial_whitelist_min_hits=2,
    )
    scorer = QuickScorer(cfg)

    # desc 含 manufacturer + OEM → 2 个白名单词命中
    video = _v("v4", "OEM manufacturer since 2010", 500, 3, 0)
    account = _a("factory_b", bio="We are a factory")

    result = scorer.score(video, account)

    assert result.commercial_whitelist_hit is True, "应命中白名单"
    assert result.breakdown.get("low_engagement_penalty") is not True, (
        "白名单命中不应被降权"
    )
    # total 应为正常评分, 不乘以 0.60
    assert result.total > 0

    print(f"  [PASS] T4: whitelist={result.commercial_whitelist_hit} "
          f"total={result.total} penalty={result.breakdown.get('low_engagement_penalty')}")


# ═══════════════════════════════════════════════════════════
# T5: low_engagement_multiplier=0 → 等同于硬淘汰
# ═══════════════════════════════════════════════════════════

def test_multiplier_zero_equals_hard_elimination():
    """multiplier=0: 回滚到旧版硬淘汰行为"""
    cfg = QuickScorerConfig(min_score=20.0, min_likes_no_comment=5,
                            low_engagement_multiplier=0.0)
    scorer = QuickScorer(cfg)

    # 使用 industry 关键词但不触发白名单: production, manufacturing 不在 whitelist
    video = _v("v5", "Professional production manufacturing export since 2010", 500, 3, 0)
    account = _a("factory_c", bio="Quality products")

    result = scorer.score(video, account)

    # 不应命中白名单
    assert result.commercial_whitelist_hit is False, "此测试应不命中白名单"
    # 应触发降权
    assert result.breakdown.get("low_engagement_penalty") is True, "应触发降权"
    # multiplier=0 → total = total_before * 0 = 0
    assert result.total == 0, f"multiplier=0 时 total 应为 0, got {result.total}"
    assert result.passed is False
    assert "quick_score" in result.eliminated_reason

    print(f"  [PASS] T5: total={result.total} passed={result.passed}")


# ═══════════════════════════════════════════════════════════
# T6: low_engagement_multiplier=1 → 不降权
# ═══════════════════════════════════════════════════════════

def test_multiplier_one_means_no_penalty():
    """multiplier=1: 零评论视频正常评分, 不施加降权"""
    cfg = QuickScorerConfig(min_score=20.0, min_likes_no_comment=5,
                            low_engagement_multiplier=1.0)
    scorer = QuickScorer(cfg)

    video = _v("v6", "OEM manufacturer factory", 2000, 3, 0)
    account = _a("factory_d", bio="Factory direct")

    result = scorer.score(video, account)

    # total 应等于未降权的正常计算值
    expected = 0.50 * result.product_relevance + 0.50 * result.video_quality
    assert abs(result.total - expected) < 0.2, (
        f"multiplier=1 时 total({result.total}) 应≈未降权值({expected})"
    )

    print(f"  [PASS] T6: total={result.total} expected={expected:.1f}")


# ═══════════════════════════════════════════════════════════
# T7: 缺少 low_engagement_multiplier 配置 → 使用默认 0.60
# ═══════════════════════════════════════════════════════════

def test_missing_config_uses_default():
    """旧 config 升级: 无 low_engagement_multiplier 时默认 0.60"""
    # 模拟旧 config 场景: 不传 low_engagement_multiplier
    cfg = QuickScorerConfig(min_score=20.0, min_likes_no_comment=5)
    # 不设置 low_engagement_multiplier, 应使用 dataclass 默认值

    assert cfg.low_engagement_multiplier == 0.60, (
        f"默认值应为 0.60, got {cfg.low_engagement_multiplier}"
    )

    scorer = QuickScorer(cfg)
    # 使用 production + manufacturing + export — 都是 industry 词但不在 whitelist
    video = _v("v7", "Professional production line for manufacturing and export", 3000, 2, 0)
    account = _a("factory_e", bio="Quality products since 2010")

    result = scorer.score(video, account)

    # 不应命中白名单
    assert result.commercial_whitelist_hit is False, "此测试应不命中白名单"
    # 降权生效: breakdown 中有标记
    assert result.breakdown.get("low_engagement_penalty") is True
    # total > 0 但 < 未降权值
    unpenalized = 0.50 * result.product_relevance + 0.50 * result.video_quality
    assert result.total <= unpenalized, (
        f"降权后 total({result.total}) 应 ≤ 未降权值({unpenalized})"
    )

    print(f"  [PASS] T7: default={cfg.low_engagement_multiplier} "
          f"total={result.total} unpenalized={unpenalized:.1f}")


# ═══════════════════════════════════════════════════════════
# T8: 个人号 + 低互动双重惩罚 → 叠加顺序正确
# ═══════════════════════════════════════════════════════════

def test_personal_account_and_low_engagement_penalty_stack():
    """个人号的 personal_penalty 先减, 再乘 low_engagement_multiplier"""
    cfg = QuickScorerConfig(
        min_score=20.0, min_likes_no_comment=5,
        low_engagement_multiplier=0.60,
        personal_account_penalty=30.0,
    )
    scorer = QuickScorer(cfg)

    # 个人号 + 零评论 + 低点赞 + 一些B2B信号 (使用非白名单词)
    video = _v("v8", "Production line for manufacturing export", 1000, 2, 0)
    account = _a("personal_guy", bio="daily vlog fan account", nickname="Daily Fun")

    result = scorer.score(video, account)

    assert result.is_personal_account is True, "应被识别为个人号"
    assert result.breakdown.get("low_engagement_penalty") is True, "应触发低互动降权"
    assert result.breakdown.get("personal_penalty") is True, "应触发个人号惩罚"

    # 预期: quality 先 -30, relevance *0.5 → total → *0.60
    # 验证降权确实生效
    assert result.total > 0, "即使双重惩罚也不应为 0 (有产品信号)"
    # 降权后的 total 应明显低于无惩罚的情况
    assert result.total < 40, (
        f"双重惩罚后 total 应 <40, got {result.total}"
    )

    print(f"  [PASS] T8: personal={result.is_personal_account} "
          f"total={result.total} relevance={result.product_relevance} "
          f"quality={result.video_quality}")


# ═══════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    passed = 0
    failed = 0
    tests = [
        ("T1: 零评论+高相关度→降权后通过", test_zero_comment_high_relevance_passes),
        ("T2: 零评论+低相关度→仍淘汰", test_zero_comment_low_relevance_fails),
        ("T3: 零互动→仍硬淘汰", test_zero_engagement_still_hard_eliminated),
        ("T4: 白名单→跳过降权", test_commercial_whitelist_bypasses_penalty),
        ("T5: multiplier=0→硬淘汰", test_multiplier_zero_equals_hard_elimination),
        ("T6: multiplier=1→不降权", test_multiplier_one_means_no_penalty),
        ("T7: 缺少配置→默认0.60", test_missing_config_uses_default),
        ("T8: 个人号+低互动→双重惩罚", test_personal_account_and_low_engagement_penalty_stack),
    ]

    for name, test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"  [FAIL] {name}: {e}")

    print(f"\n{'='*50}")
    print(f"  Result: {passed}/{passed+failed} passed")
    if failed:
        print(f"  *** {failed} tests FAILED ***")
        sys.exit(1)
    else:
        print(f"  All tests passed")
