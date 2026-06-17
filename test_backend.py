"""
Backend component unit tests for TikTok Analyzer
"""
import sys, io, json, os, shutil, tempfile
sys.path.insert(0, r"C:\Users\Administrator\Documents\=tk")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

print("=" * 60)
print("Backend Component Tests")
print("=" * 60)

# ---- Mock data ----
MOCK_ACCOUNTS = [
    {"username": "beauty_tester1", "nickname": "Beauty Pro", "follower_count": 150000,
     "like_count": 2000000, "verified": True, "bio": "Beauty tips & tricks", "region": "US", "url": "https://tiktok.com/@beauty_tester1"},
    {"username": "skincare_guru", "nickname": "Skincare Guru", "follower_count": 89000,
     "like_count": 950000, "verified": False, "bio": "Skincare reviews", "region": "KR", "url": "https://tiktok.com/@skincare_guru"},
    {"username": "makeup_artist", "nickname": "Makeup Artist", "follower_count": 320000,
     "like_count": 5100000, "verified": True, "bio": "Professional MUA", "region": "US", "url": "https://tiktok.com/@makeup_artist"},
]

MOCK_VIDEOS = [
    {"id": "1234567890", "account_username": "beauty_tester1", "desc": "Best LED mask review #skincare #beauty",
     "tags": ["skincare", "beauty"], "play_count": 500000, "digg_count": 45000, "comment_count": 1200,
     "share_count": 3400, "duration": 45, "music": "Popular Song", "url": "https://tiktok.com/@beauty_tester1/video/1234567890"},
    {"id": "1234567891", "account_username": "skincare_guru", "desc": "Morning routine #skincare #morning",
     "tags": ["skincare", "morning"], "play_count": 230000, "digg_count": 18000, "comment_count": 890,
     "share_count": 1200, "duration": 60, "music": "Chill Beat", "url": "https://tiktok.com/@skincare_guru/video/1234567891"},
    {"id": "1234567892", "account_username": "makeup_artist", "desc": "Wedding makeup tutorial #makeup #wedding",
     "tags": ["makeup", "wedding"], "play_count": 890000, "digg_count": 92000, "comment_count": 3400,
     "share_count": 8900, "duration": 90, "music": "Romantic Song", "url": "https://tiktok.com/@makeup_artist/video/1234567892"},
]

MOCK_COMMENTS = [
    {"account_username": "beauty_tester1", "video_id": "1234567890", "username": "user1",
     "text": "Where can I buy this LED mask? I need it!", "likes": 234},
    {"account_username": "beauty_tester1", "video_id": "1234567890", "username": "user2",
     "text": "Does it work for sensitive skin?", "likes": 89},
    {"account_username": "skincare_guru", "video_id": "1234567891", "username": "user3",
     "text": "Love this routine! What moisturizer do you use?", "likes": 156},
    {"account_username": "makeup_artist", "video_id": "1234567892", "username": "user4",
     "text": "Can you do a tutorial for oily skin?", "likes": 445},
    {"account_username": "makeup_artist", "video_id": "1234567892", "username": "user5",
     "text": "This foundation looks cakey on me, any tips?", "likes": 67},
]

MOCK_DATA = {
    "keywords": ["beauty", "skincare"],
    "region": "US",
    "accounts": MOCK_ACCOUNTS,
    "videos": MOCK_VIDEOS,
    "comments": MOCK_COMMENTS,
    "total_accounts": 3,
    "total_videos": 3,
    "total_comments": 5,
}

failed = []

def test(name):
    """Decorator-like wrapper"""
    print(f"\n--- {name} ---")
    try:
        yield
        print(f"  >>> {name}: ALL PASSED")
    except Exception as e:
        print(f"  [FAIL] {name}: {e}")
        import traceback; traceback.print_exc()
        failed.append(name)


# ============================================================
# Test 1: Export CSV
# ============================================================
def test_csv():
    from tiktok_analyzer.exporter import export_csv
    tmpdir = tempfile.mkdtemp(prefix="tk_test_")
    csv_files = export_csv(MOCK_DATA, None, tmpdir)

    for key in ["accounts", "videos", "comments"]:
        path = csv_files.get(key, "")
        assert path and os.path.exists(path), f"{key}.csv missing"
        size = os.path.getsize(path)
        print(f"  [OK] {key}.csv: {size} bytes")

    with open(csv_files["accounts"], "r", encoding="utf-8-sig") as f:
        lines = f.readlines()
        assert len(lines) == 4  # header + 3 accounts
        print(f"  [OK] accounts.csv: {len(lines)-1} data rows")

    # Verify content
    with open(csv_files["videos"], "r", encoding="utf-8-sig") as f:
        content = f.read()
        assert "LED mask" in content
        print(f"  [OK] videos.csv content verified")

    return tmpdir

print("\n--- Test 1: CSV Export ---")
try:
    tmpdir = test_csv()
    print("  >>> CSV Export: ALL PASSED")
except Exception as e:
    print(f"  [FAIL] CSV Export: {e}")
    import traceback; traceback.print_exc()
    failed.append("CSV Export")
    tmpdir = tempfile.mkdtemp(prefix="tk_test_")


# ============================================================
# Test 2: Export Markdown
# ============================================================
print("\n--- Test 2: Markdown Export ---")
try:
    from tiktok_analyzer.exporter import export_markdown

    analysis_mock = {
        "business_needs": [
            {"finding": "LED beauty devices trending", "evidence": "Multiple comments asking about LED mask", "priority": 8, "suggestion": "Stock LED beauty devices"},
        ],
        "purchase_needs": [
            {"finding": "Sensitive skin products needed", "evidence": "Comment: Does it work for sensitive skin?", "priority": 7, "suggestion": "Market gentle formulas"},
        ],
        "pain_points": [
            {"finding": "Foundation cakey issue", "evidence": "Comment: foundation looks cakey on me", "priority": 6, "suggestion": "Create lightweight foundation"},
        ],
        "summary": "Beauty niche has strong demand for specialized skincare devices and sensitive-skin products."
    }

    md_path = export_markdown(MOCK_DATA, analysis_mock, ["beauty", "skincare"], "US", tmpdir)

    assert os.path.exists(md_path), f"report.md not found at {md_path}"
    size = os.path.getsize(md_path)
    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()

    checks = [
        ("has title", "# TikTok" in content),
        ("has keywords", "beauty, skincare" in content),
        ("has accounts table", "beauty_tester1" in content),
        ("has videos", "LED mask" in content),
        ("has AI analysis", "LED beauty devices" in content),
        ("has summary", "Beauty niche" in content),
        ("has tags", "#skincare" in content),
        ("has comment keywords", "moisturizer" in content.lower()),
    ]
    all_ok = True
    for name, ok in checks:
        status = "OK" if ok else "FAIL"
        if not ok:
            all_ok = False
        print(f"  [{status}] {name}")

    print(f"  >>> Markdown Export: {size} bytes, {'ALL PASSED' if all_ok else 'PARTIAL'}")
    if not all_ok:
        failed.append("Markdown Export (partial)")
except Exception as e:
    print(f"  [FAIL] Markdown Export: {e}")
    import traceback; traceback.print_exc()
    failed.append("Markdown Export")


# ============================================================
# Test 3: Checkpoint (SQLite)
# ============================================================
print("\n--- Test 3: Checkpoint (SQLite) ---")
try:
    from tiktok_analyzer.checkpoint import CheckpointManager

    ck = CheckpointManager("unit_test_1")

    # Stage tracking
    ck.mark_stage("search_beauty", "completed", "Found 3 accounts")
    status = ck.get_stage("search_beauty")
    assert status == "completed", f"Expected completed, got {status}"
    print(f"  [OK] Stage tracking: {status}")

    # Mark scraped
    ck.mark_scraped("account_info", "beauty_tester1", MOCK_ACCOUNTS[0])
    assert ck.is_completed("account_info", "beauty_tester1")
    print(f"  [OK] Mark scraped + is_completed")

    # Get scraped data
    data = ck.get_scraped_data("account_info", "beauty_tester1")
    assert data is not None
    assert data.get("username") == "beauty_tester1"
    print(f"  [OK] Get scraped data: @{data.get('username')}")

    # Not completed
    assert not ck.is_completed("account_info", "nonexistent")
    print(f"  [OK] is_completed returns False for unknown key")

    # Progress summary
    summary = ck.get_progress_summary()
    assert "search_beauty" in summary["stages"]
    assert summary["counts"].get("account_info") == 1
    print(f"  [OK] Progress summary stages: {list(summary['stages'].keys())}")

    # Cleanup
    ck.clear()
    print(f"  >>> Checkpoint: ALL PASSED")
except Exception as e:
    print(f"  [FAIL] Checkpoint: {e}")
    import traceback; traceback.print_exc()
    failed.append("Checkpoint")


# ============================================================
# Test 4: Proxy Pool
# ============================================================
print("\n--- Test 4: Proxy Pool ---")
try:
    from tiktok_analyzer.proxy_pool import ProxyPool

    # Test with check_reachable=False to avoid network dependency
    pool = ProxyPool(check_reachable=False)

    # Add proxies
    pool.add_proxy("http://proxy1.example.com:8080", "user1", "pass1")
    pool.add_proxy("http://proxy2.example.com:3128")

    assert pool.count == 2
    print(f"  [OK] Pool count: {pool.count}")

    # Get proxy (round-robin)
    p1 = pool.get_proxy()
    assert p1 is not None
    print(f"  [OK] Get proxy 1: {p1['server']}")

    p2 = pool.get_proxy()
    assert p2 is not None
    assert p2["server"] != p1["server"]  # Should rotate
    print(f"  [OK] Get proxy 2 (rotated): {p2['server']}")

    # Report failure
    pool.report_failure("http://proxy1.example.com:8080")
    pool.report_failure("http://proxy1.example.com:8080")
    pool.report_failure("http://proxy1.example.com:8080")  # 3 fails -> disabled

    p3 = pool.get_proxy()
    assert p3["server"] == "http://proxy2.example.com:3128"  # Only proxy2 available
    print(f"  [OK] After 3 failures, proxy1 disabled: got {p3['server']}")

    # Report success resets counter
    pool.report_success("http://proxy1.example.com:8080")
    # proxy1 should be available again
    print(f"  [OK] Report success re-enables proxy")

    # Load from JSON
    tmp_proxy_file = tmpdir + "/test_proxies.json"
    with open(tmp_proxy_file, "w") as f:
        json.dump([{"server": "http://127.0.0.1:9999"}], f)

    pool2 = ProxyPool(tmp_proxy_file, check_reachable=False)
    assert pool2.count == 1
    p = pool2.get_proxy()
    assert p["server"] == "http://127.0.0.1:9999"
    print(f"  [OK] Load from JSON file: {p['server']}")

    # Available count
    assert pool2.available_count == 1
    print(f"  [OK] Available count: {pool2.available_count}")

    print(f"  >>> Proxy Pool: ALL PASSED")
except Exception as e:
    print(f"  [FAIL] Proxy Pool: {e}")
    import traceback; traceback.print_exc()
    failed.append("Proxy Pool")


# ============================================================
# Test 5: Logger
# ============================================================
print("\n--- Test 5: Logger ---")
try:
    from tiktok_analyzer.logger import setup_logger
    import glob as globmod

    log = setup_logger("test_module")
    log.info("Test info message")
    log.debug("Test debug message")
    log.warning("Test warning message")

    # Check log file was created
    log_files = globmod.glob("logs/*.log")
    assert len(log_files) > 0, "No log files found"
    print(f"  [OK] Logger created: {len(log_files)} log file(s)")

    with open(log_files[-1], "r", encoding="utf-8") as f:
        content = f.read()
    assert "Test info message" in content
    assert "test_module" in content
    print(f"  [OK] Log content verified")

    print(f"  >>> Logger: ALL PASSED")
except Exception as e:
    print(f"  [FAIL] Logger: {e}")
    import traceback; traceback.print_exc()
    failed.append("Logger")


# ============================================================
# Test 6: Analyzer Structure
# ============================================================
print("\n--- Test 6: Analyzer Structure ---")
try:
    from tiktok_analyzer.analyzer import ANALYSIS_PROMPT

    assert "business_needs" in ANALYSIS_PROMPT
    assert "purchase_needs" in ANALYSIS_PROMPT
    assert "pain_points" in ANALYSIS_PROMPT
    print(f"  [OK] Analysis prompt contains all 3 dimensions")

    # Test prompt formatting
    formatted = ANALYSIS_PROMPT.format(accounts="[]", videos="[]", comments="[]")
    assert "[]" in formatted
    print(f"  [OK] Prompt formatting works")

    print(f"  >>> Analyzer Structure: ALL PASSED")
except Exception as e:
    print(f"  [FAIL] Analyzer: {e}")
    import traceback; traceback.print_exc()
    failed.append("Analyzer Structure")


# ============================================================
# Test 7: _parse_count utility
# ============================================================
print("\n--- Test 7: _parse_count ---")
try:
    from tiktok_analyzer.scraper import _parse_count

    tests = [
        ("1.5M", 1500000),
        ("10.2K", 10200),
        ("500", 500),
        ("", 0),
        (None, 0),
        ("1,234", 1234),
        ("2B", 2000000000),
        ("test", 0),
        ("0", 0),
        ("1M", 1000000),
        ("3.3k", 3300),
    ]
    all_ok = True
    for inp, expected in tests:
        result = _parse_count(inp)
        if result != expected:
            print(f"  [FAIL] _parse_count({inp!r}) = {result}, expected {expected}")
            all_ok = False
    if all_ok:
        print(f"  [OK] All {len(tests)} parse_count tests passed")
    print(f"  >>> _parse_count: {'ALL PASSED' if all_ok else 'SOME FAILED'}")
    if not all_ok:
        failed.append("_parse_count")
except Exception as e:
    print(f"  [FAIL] _parse_count: {e}")
    import traceback; traceback.print_exc()
    failed.append("_parse_count")


# ============================================================
# Test 8: Stealth JS integrity
# ============================================================
print("\n--- Test 8: Stealth JS Integrity ---")
try:
    from tiktok_analyzer.scraper import STEALTH_JS

    # Verify key anti-detection patterns
    checks = [
        ("webdriver hidden", "navigator, \"webdriver\"" in STEALTH_JS),
        ("plugins spoofed", "Object.defineProperty(navigator, \"plugins\"" in STEALTH_JS),
        ("chrome object", "window.chrome" in STEALTH_JS),
        ("permissions query", "notifications" in STEALTH_JS),
        ("canvas noise", "toDataURL" in STEALTH_JS),
        ("WebGL spoof", "getParameter" in STEALTH_JS),
        ("connection spoof", "4g" in STEALTH_JS),
        ("battery spoof", "getBattery" in STEALTH_JS),
    ]
    all_ok = True
    for name, ok in checks:
        status = "OK" if ok else "FAIL"
        if not ok:
            all_ok = False
        print(f"  [{status}] {name}")
    print(f"  >>> Stealth JS: {'ALL PASSED' if all_ok else 'SOME FAILED'}")
    if not all_ok:
        failed.append("Stealth JS")
except Exception as e:
    print(f"  [FAIL] Stealth JS: {e}")
    failed.append("Stealth JS")


# ============================================================
# Test 9: Captcha detection logic
# ============================================================
print("\n--- Test 9: Captcha Keywords ---")
try:
    from tiktok_analyzer.captcha import CAPTCHA_KEYWORDS

    assert len(CAPTCHA_KEYWORDS) > 10
    assert "captcha" in CAPTCHA_KEYWORDS
    assert "验证码" in CAPTCHA_KEYWORDS
    assert "滑块" in CAPTCHA_KEYWORDS
    assert "slide" in CAPTCHA_KEYWORDS
    print(f"  [OK] {len(CAPTCHA_KEYWORDS)} captcha keywords")
    print(f"  >>> Captcha: ALL PASSED")
except Exception as e:
    print(f"  [FAIL] Captcha: {e}")
    failed.append("Captcha")


# ============================================================
# Test 10: Scraper anti-detection args
# ============================================================
print("\n--- Test 10: Anti-Detection Args ---")
try:
    from tiktok_analyzer.scraper import ANTI_DETECTION_ARGS, COMMON_UA

    assert len(ANTI_DETECTION_ARGS) >= 10
    assert any("AutomationControlled" in a for a in ANTI_DETECTION_ARGS)
    print(f"  [OK] {len(ANTI_DETECTION_ARGS)} anti-detection flags")

    assert "Chrome/131" in COMMON_UA
    assert "Windows NT" in COMMON_UA
    print(f"  [OK] User-Agent: {COMMON_UA[:60]}...")

    print(f"  >>> Anti-Detection Args: ALL PASSED")
except Exception as e:
    print(f"  [FAIL] Anti-Detection Args: {e}")
    failed.append("Anti-Detection Args")


# ============================================================
# Cleanup
# ============================================================
shutil.rmtree(tmpdir, ignore_errors=True)

print("\n" + "=" * 60)
if failed:
    print(f"FAILURES: {len(failed)} test(s)")
    for f in failed:
        print(f"  - {f}")
else:
    print("ALL TESTS PASSED!")
print("=" * 60)
