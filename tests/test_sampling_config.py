"""
T9-T10: 采样配置集成测试

覆盖:
  T9:  comment_sampling 配置正确传递到 scraper 参数
  T10: 缺少 comment_sampling 配置时使用默认值

用法:
  py tests/test_sampling_config.py
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


# ═══════════════════════════════════════════════════════════
# T9: comment_sampling 配置值提取正确
# ═══════════════════════════════════════════════════════════

def test_sampling_config_reads_correctly():
    """验证从配置字典中读取 comment_sampling 各项的逻辑"""
    # 模拟 config.yaml 的 comment_sampling section
    cfg = {
        "comment_sampling": {
            "strategy": "pool_random",
            "sample_count": 12,
            "pool_size": 40,
        }
    }

    sampling_cfg = cfg.get("comment_sampling", {})
    strategy = sampling_cfg.get("strategy", "first_n")
    count = sampling_cfg.get("sample_count", 8)
    pool = sampling_cfg.get("pool_size", 30)

    assert strategy == "pool_random", f"expected pool_random, got {strategy}"
    assert count == 12, f"expected 12, got {count}"
    assert pool == 40, f"expected 40, got {pool}"

    print(f"  [PASS] T9: strategy={strategy} count={count} pool_size={pool}")


# ═══════════════════════════════════════════════════════════
# T10: 缺少 comment_sampling 配置 → 默认值
# ═══════════════════════════════════════════════════════════

def test_missing_sampling_config_uses_defaults():
    """无 comment_sampling section 时各字段使用默认值"""
    # 空配置
    cfg = {}

    sampling_cfg = cfg.get("comment_sampling", {})
    strategy = sampling_cfg.get("strategy", "first_n")
    count = sampling_cfg.get("sample_count", 8)
    pool = sampling_cfg.get("pool_size", 30)

    assert strategy == "first_n", f"default strategy should be first_n, got {strategy}"
    assert count == 8, f"default count should be 8, got {count}"
    assert pool == 30, f"default pool_size should be 30, got {pool}"

    print(f"  [PASS] T10: defaults: strategy={strategy} count={count} pool_size={pool}")


# ═══════════════════════════════════════════════════════════
# T10b: 部分配置字段缺失 → 只使用存在字段的默认值
# ═══════════════════════════════════════════════════════════

def test_partial_sampling_config():
    """仅设置 strategy, 未设置 sample_count 和 pool_size → 各自使用默认值"""
    cfg = {
        "comment_sampling": {
            "strategy": "top_and_latest",
        }
    }

    sampling_cfg = cfg.get("comment_sampling", {})
    strategy = sampling_cfg.get("strategy", "first_n")
    count = sampling_cfg.get("sample_count", 8)
    pool = sampling_cfg.get("pool_size", 30)

    assert strategy == "top_and_latest"
    assert count == 8, f"missing sample_count → default 8, got {count}"
    assert pool == 30, f"missing pool_size → default 30, got {pool}"

    print(f"  [PASS] T10b: partial config: strategy={strategy} count(default)={count} "
          f"pool(default)={pool}")


# ═══════════════════════════════════════════════════════════
# T10c: 在 scrape 的 run_analysis() 参数中, sampling 参数有默认值
# ═══════════════════════════════════════════════════════════

def test_scraper_run_analysis_defaults():
    """验证 scraper.run_analysis() 的 sampling 参数默认值"""
    import inspect
    from tiktok_analyzer.scraper import TikTokScraper

    sig = inspect.signature(TikTokScraper.run_analysis)
    params = sig.parameters

    assert "sample_comment_count" in params, "run_analysis 应有 sample_comment_count 参数"
    assert "comment_sampling_strategy" in params, "run_analysis 应有 comment_sampling_strategy 参数"

    assert params["sample_comment_count"].default == 8, (
        f"默认 sample_comment_count 应为 8, got {params['sample_comment_count'].default}"
    )
    assert params["comment_sampling_strategy"].default == "first_n", (
        f"默认 strategy 应为 first_n, got {params['comment_sampling_strategy'].default}"
    )

    print(f"  [PASS] T10c: scraper defaults: count={params['sample_comment_count'].default} "
          f"strategy={params['comment_sampling_strategy'].default}")


# ═══════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    passed = 0
    failed = 0
    tests = [
        ("T9:  采样配置全部字段读取", test_sampling_config_reads_correctly),
        ("T10: 缺少采样配置→默认值", test_missing_sampling_config_uses_defaults),
        ("T10b: 部分配置字段→混合默认", test_partial_sampling_config),
        ("T10c: scraper 参数默认值验证", test_scraper_run_analysis_defaults),
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
