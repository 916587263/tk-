"""
P0 验证: ClassifiedComment video_index 全链路保留测试

构造 3 个视频、6 条评论的测试数据,
模拟 LLM 响应 → _parse_llm_response → _aggregate_by_video,
验证 video_index 在分类前后完全一致。

用法:
  py tests/test_video_index_fix.py
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from tiktok_analyzer.comment_classifier import (
    CommentClassifier,
    CommentClassifierConfig,
    ClassifiedComment,
    VideoClassifyResult,
    BatchClassifyResult,
)

# ═══════════════════════════════════════════════════════════
# 测试数据: 3 个视频, 各 2 条深度评论
# ═══════════════════════════════════════════════════════════

VIDEO_0 = {
    "id": "v000",
    "account_username": "factory_a",
    "_deep_comments": [
        {"text": "What's your MOQ for non woven bags?", "likes": 10},
        {"text": "Nice video!", "likes": 5},
    ],
}

VIDEO_1 = {
    "id": "v111",
    "account_username": "factory_b",
    "_deep_comments": [
        {"text": "Can I get a sample first?", "likes": 20},
        {"text": "Great quality 👍", "likes": 15},
    ],
}

VIDEO_2 = {
    "id": "v222",
    "account_username": "factory_c",
    "_deep_comments": [
        {"text": "Price for bulk order to USA?", "likes": 30},
        {"text": "Love this content", "likes": 8},
    ],
}

TEST_VIDEOS = [VIDEO_0, VIDEO_1, VIDEO_2]


def simulate_llm_response(videos: list[dict]) -> list[dict]:
    """模拟 LLM 返回的分类结果 (含 video_index + comment_index)"""
    items = []
    for vi, v in enumerate(videos):
        deep = v.get("_deep_comments", [])
        for ci, c in enumerate(deep):
            text = c["text"]
            # 模拟分类逻辑: 含 ? 或 price/moq/sample/bulk 的评论判定为有意图
            has_intent = any(kw in text.lower() for kw in
                           ["moq", "price", "sample", "bulk", "cost", "?"])
            items.append({
                "video_index": vi,
                "comment_index": ci,
                "has_intent": has_intent,
                "category": "price_inquiry" if has_intent else None,
                "intensity": 0.8 if has_intent else 0.0,
                "actionable": has_intent,
                "extracted_info": {},
                "text": text,
            })
    return items


# ═══════════════════════════════════════════════════════════
# 测试 1: _parse_llm_response 正确提取 video_index
# ═══════════════════════════════════════════════════════════

def test_parse_llm_response():
    """验证 LLM 响应解析后 video_index 字段存在且正确"""
    import json

    llm_output = simulate_llm_response(TEST_VIDEOS)
    content = json.dumps(llm_output)

    classified = CommentClassifier._parse_llm_response(content, len(TEST_VIDEOS))

    # 检查数量
    total_comments = sum(len(v.get("_deep_comments", [])) for v in TEST_VIDEOS)
    assert len(classified) == total_comments, \
        f"分类结果数量不匹配: {len(classified)} != {total_comments}"

    # 检查每条结果的 video_index
    for c in classified:
        assert hasattr(c, 'video_index'), "ClassifiedComment 缺少 video_index 字段!"
        assert isinstance(c.video_index, int), \
            f"video_index 类型错误: {type(c.video_index)}"
        assert 0 <= c.video_index < len(TEST_VIDEOS), \
            f"video_index 越界: {c.video_index}"

    # 检查按 video_index 分布
    from collections import Counter
    dist = Counter(c.video_index for c in classified)
    for vi in range(len(TEST_VIDEOS)):
        expected = len(TEST_VIDEOS[vi].get("_deep_comments", []))
        actual = dist.get(vi, 0)
        assert actual == expected, \
            f"视频 {vi}: 期望 {expected} 条分类评论, 实际 {actual} 条"

    print("[PASS] Test 1: _parse_llm_response correctly extracts video_index")
    return classified


# ═══════════════════════════════════════════════════════════
# 测试 2: _aggregate_by_video 正确按 video_index 分组
# ═══════════════════════════════════════════════════════════

def test_aggregate_by_video():
    """验证聚合逻辑中 video_index 分组正确"""
    import json

    llm_output = simulate_llm_response(TEST_VIDEOS)
    content = json.dumps(llm_output)
    classified = CommentClassifier._parse_llm_response(content, len(TEST_VIDEOS))

    # 创建临时 classifier 实例用于调用 _aggregate_by_video
    # (仅调用静态方法，不需要 API key)
    cfg = CommentClassifierConfig()
    classifier = CommentClassifier.__new__(CommentClassifier)
    classifier.config = cfg

    results = classifier._aggregate_by_video(TEST_VIDEOS, classified)

    assert len(results) == len(TEST_VIDEOS), \
        f"聚合结果数量: {len(results)} != {len(TEST_VIDEOS)}"

    # 验证每个视频的聚合指标
    for vi, (v, vr) in enumerate(zip(TEST_VIDEOS, results)):
        deep = v.get("_deep_comments", [])
        expected_total = len(deep)

        # 检查是否是空结果 (修复前 bug: 视频 1,2 会是空结果)
        assert vr.video_id == v["id"], \
            f"视频 {vi}: video_id 不匹配 {vr.video_id} != {v['id']}"

        assert vr.total_comments == expected_total, \
            f"视频 {vi} ({v['id']}): total_comments {vr.total_comments} != {expected_total} " \
            f"(可能被错误分配到其他视频!)"

        # 验证意图统计
        expected_intent = sum(
            1 for c in deep
            if any(kw in c["text"].lower() for kw in
                   ["moq", "price", "sample", "bulk", "cost", "?"])
        )
        assert vr.intent_comments == expected_intent, \
            f"视频 {vi} ({v['id']}): intent_comments {vr.intent_comments} != {expected_intent}"

        expected_ratio = expected_intent / expected_total if expected_total > 0 else 0
        assert abs(vr.intent_ratio - expected_ratio) < 0.001, \
            f"视频 {vi} ({v['id']}): intent_ratio {vr.intent_ratio} != {expected_ratio}"

        print(f"  视频 {vi} ({v['id']}): "
              f"total={vr.total_comments}, intent={vr.intent_comments}, "
              f"ratio={vr.intent_ratio:.0%}, actionable={vr.actionable_intent_count}")

    print("[PASS] 测试 2 通过: _aggregate_by_video 正确按 video_index 分组")
    return results


# ═══════════════════════════════════════════════════════════
# 测试 3: 验证修复前 Bug 的具体场景
# ═══════════════════════════════════════════════════════════

def test_bug_fix_verification():
    """验证修复前 video_index=0 的 bug 已被消除:
    - 修复前: 所有评论分配给视频 0, 视频 1,2 收到空结果
    - 修复后: 每个视频只收到自己的评论
    """
    import json

    llm_output = simulate_llm_response(TEST_VIDEOS)
    content = json.dumps(llm_output)
    classified = CommentClassifier._parse_llm_response(content, len(TEST_VIDEOS))

    # 模拟修复前的行为: 直接覆盖 video_index=0
    # (这会导致视频 1,2 的数据归属到视频 0)
    buggy_results = []
    for c in classified:
        buggy = ClassifiedComment(
            video_index=0,  # ← 修复前的 bug: 始终=0
            comment_index=c.comment_index,
            text=c.text,
            has_intent=c.has_intent,
            category=c.category,
            intensity=c.intensity,
            actionable=c.actionable,
            extracted_info=c.extracted_info,
        )
        buggy_results.append(buggy)

    # 用 buggy 数据执行聚合
    cfg = CommentClassifierConfig()
    classifier = CommentClassifier.__new__(CommentClassifier)
    classifier.config = cfg
    buggy_aggregated = classifier._aggregate_by_video(TEST_VIDEOS, buggy_results)

    # 用正确数据执行聚合
    correct_aggregated = classifier._aggregate_by_video(TEST_VIDEOS, classified)

    # 对比: 修复后视频 #1 和 #2 不应是空结果
    for vi in range(1, len(TEST_VIDEOS)):
        buggy_vr = buggy_aggregated[vi]
        correct_vr = correct_aggregated[vi]

        # 修复前: is_weak_reference 大概率是 True (因为空结果)
        # 修复后: 应该有正确的分类数据
        print(f"\n  视频 {vi} ({TEST_VIDEOS[vi]['id']}):")
        print(f"    修复前 (buggy): total={buggy_vr.total_comments}, "
              f"intent={buggy_vr.intent_comments}, "
              f"is_weak={buggy_vr.is_weak_reference}")
        print(f"    修复后 (正确): total={correct_vr.total_comments}, "
              f"intent={correct_vr.intent_comments}, "
              f"is_weak={correct_vr.is_weak_reference}")

        # 关键断言: 修复后视频 #1, #2 的评论数不应为 0
        assert correct_vr.total_comments > 0, \
            f"视频 {vi}: 修复后仍为空! 可能有其他问题。"
        # 修复前视频应有自己的数据, 不应全部归零
        assert correct_vr.total_comments == buggy_vr.total_comments or \
               buggy_vr.total_comments == 0, \
            f"视频 {vi}: 修复前后 total_comments 异常差异"

    print("\n[PASS] 测试 3 通过: Bug 场景验证完成, 修复后 video_index 正确分发")
    return buggy_aggregated, correct_aggregated


# ═══════════════════════════════════════════════════════════
# 测试 4: 端到端 — classify_videos() 的完整流程
# ═══════════════════════════════════════════════════════════

def test_e2e_parse_and_aggregate():
    """端到端测试: 模拟完整分类流程 (不调 LLM, 用模拟响应)"""
    import json

    # 构造更多样化的测试数据: 5 视频 × 3 评论 = 15 条
    videos = []
    for i in range(5):
        comments = []
        for j in range(3):
            # 视频 0,2,4: 有商业意图; 视频 1,3: 纯互动
            if i % 2 == 0 and j == 0:
                text = f"How much for bulk order? vid={i}"
            elif i % 2 == 0 and j == 1:
                text = f"Nice product! vid={i}"
            else:
                text = f"Great video! vid={i} c{j}"
            comments.append({"text": text, "likes": 10 - j})
        videos.append({
            "id": f"vid_{i:03d}",
            "account_username": f"user_{i}",
            "_deep_comments": comments,
        })

    # 先用 _classify_batch 的逻辑构建输入 (但跳过 LLM 调用)
    # 直接模拟 LLM 响应
    all_comments_flat = []
    for vi, v in enumerate(videos):
        deep = v.get("_deep_comments", [])
        for ci, c in enumerate(deep):
            all_comments_flat.append({
                "video_index": vi,
                "comment_index": ci,
                "text": c["text"][:300],
            })

    # 模拟 LLM 分类
    simulated = []
    for item in all_comments_flat:
        text = item["text"]
        has_intent = any(kw in text.lower() for kw in
                        ["moq", "price", "sample", "bulk", "cost", "how much", "?"])
        simulated.append({
            "video_index": item["video_index"],
            "comment_index": item["comment_index"],
            "has_intent": has_intent,
            "category": "price_inquiry" if has_intent else None,
            "intensity": 0.8 if has_intent else 0.0,
            "actionable": has_intent,
            "extracted_info": {},
            "text": item["text"],
        })

    # 解析
    content = json.dumps(simulated)
    classified = CommentClassifier._parse_llm_response(content, len(videos))

    # 聚合
    cfg = CommentClassifierConfig()
    classifier = CommentClassifier.__new__(CommentClassifier)
    classifier.config = cfg
    results = classifier._aggregate_by_video(videos, classified)

    # 验证
    assert len(results) == 5
    for vi, vr in enumerate(results):
        expected_total = 3
        # 视频 0,2,4: 有 1 条有意图; 视频 1,3: 0 条
        expected_intent = 1 if vi % 2 == 0 else 0
        assert vr.total_comments == expected_total, \
            f"vid_{vi}: total mismatch {vr.total_comments} != {expected_total}"
        assert vr.intent_comments == expected_intent, \
            f"vid_{vi}: intent mismatch {vr.intent_comments} != {expected_intent}"

        print(f"  vid_{vi:03d}: total={vr.total_comments}, intent={vr.intent_comments}, "
              f"ratio={vr.intent_ratio:.0%}, diversity={vr.intent_diversity}, "
              f"actionable={vr.actionable_intent_count}")

    print("[PASS] 测试 4 通过: 端到端 5 视频 × 3 评论, video_index 全链路正确")
    return results


# ═══════════════════════════════════════════════════════════
# 运行
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 65)
    print("P0 验证: ClassifiedComment video_index 全链路保留测试")
    print("=" * 65)

    try:
        test_parse_llm_response()
        test_aggregate_by_video()
        test_bug_fix_verification()
        test_e2e_parse_and_aggregate()

        print("\n" + "=" * 65)
        print("SUCCESS: 所有测试通过! video_index 全链路保留正确。")
        print("=" * 65)

    except AssertionError as e:
        print(f"\n[FAIL] 测试失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: 异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
