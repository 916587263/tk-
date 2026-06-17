"""CDP end-to-end test"""
import urllib.request, json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

payload = {
    "keywords": "skincare",
    "accounts_per_keyword": 1,
    "videos_per_account": 3,
    "comments_per_video": 10,
    "browser": "msedge",
    "cdp_port": 9222,
    "use_ai": False,
}

data = json.dumps(payload).encode()
req = urllib.request.Request(
    "http://127.0.0.1:5000/api/start",
    data=data,
    headers={"Content-Type": "application/json"},
)
r = json.loads(urllib.request.urlopen(req).read())
tid = r["task_id"]
print(f"Task: {tid}")

req = urllib.request.Request(f"http://127.0.0.1:5000/api/progress/{tid}")
req.add_header("Accept", "text/event-stream")
try:
    for line in urllib.request.urlopen(req, timeout=120):
        line = line.decode().strip()
        if not line.startswith("data: "):
            continue
        msg = json.loads(line[6:])
        if msg["msg"] == "heartbeat":
            continue
        tag = ""
        if msg.get("data", {}).get("done"):
            tag = " [DONE]"
        elif msg.get("data", {}).get("error"):
            tag = " [ERR]"
        print(f"  {msg['msg']}{tag}")
        if msg.get("data", {}).get("done") or msg.get("data", {}).get("error"):
            break
except Exception as e:
    print(f"SSE error: {e}")

# Results
try:
    rr = json.loads(
        urllib.request.urlopen(f"http://127.0.0.1:5000/api/results/{tid}").read()
    )
    s = rr["summary"]
    print(f"\n{s['accounts']} accounts, {s['videos']} videos, {s['comments']} comments")
    for a in (rr.get("accounts") or [])[:3]:
        print(f"  @{a['username']:25s} | {a.get('follower_count',0):>10,} fans | {a.get('like_count',0):>10,} likes | {a.get('bio','')[:30]}")
    for v in (rr.get("videos") or [])[:5]:
        desc = (v.get("desc") or "")[:45]
        print(f"  {desc:45s} | plays={v.get('play_count',0):>8,} | tags={v.get('tags',[])}")
except Exception as e:
    print(f"Results error: {e}")
