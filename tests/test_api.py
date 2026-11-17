"""API tests with a fake fetcher (no network)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app import finder, main  # noqa: E402

R = finder.Response


def fake_fetch(url, headers):
    if url.startswith("https://t.me/money"):
        return R(200, '<div class="tgme_page_title">Money</div>', url)
    if url.startswith("https://t.me/"):
        return R(200, "<html></html>", url)
    if url.startswith("https://fragment.com/"):
        return R(200, "", "https://fragment.com/?query=x")
    if "username=money" in url:
        return R(200, json.dumps({"data": {"user": {"edge_followed_by": {"count": 5}}}}), url)
    return R(404, "", url)


def client():
    main.MIN_INTERVAL.update(telegram=0, instagram=0)
    main.app.state.checker = main.Checker(fake_fetch)
    main.app.state.limiter = main.RateLimiter(100)
    return TestClient(main.app)


def events(text):
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(l.split(": ", 1) for l in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_index_and_health():
    c = client()
    assert c.get("/").status_code == 200
    assert c.get("/api/health").json()["ok"]


def test_scan_stream():
    c = client()
    r = c.get("/api/scan", params={"names": "@money, therapy cash", "platforms": "telegram,instagram"})
    assert r.status_code == 200
    got = {(d["username"], d["platform"]): d["status"] for e, d in events(r.text) if e == "result"}
    assert got == {
        ("money", "telegram"): "TAKEN", ("money", "instagram"): "TAKEN",
        ("therapy", "telegram"): "AVAILABLE", ("therapy", "instagram"): "AVAILABLE",
        ("cash", "telegram"): "INVALID", ("cash", "instagram"): "AVAILABLE",
    }
    # second scan is served from cache
    r2 = c.get("/api/scan", params={"names": "therapy", "platforms": "instagram"})
    assert [d["cached"] for e, d in events(r2.text) if e == "result"] == [True]


def test_validation_and_limits():
    c = client()
    assert c.get("/api/scan", params={"names": "", "platforms": "telegram"}).status_code == 400
    many = ",".join(f"name{i}" for i in range(main.MAX_NAMES_PER_SCAN + 1))
    assert c.get("/api/scan", params={"names": many}).status_code == 400
    main.app.state.limiter = main.RateLimiter(3)
    assert c.get("/api/scan", params={"names": "aaaaa,bbbbb"}).status_code == 429  # needs 4


def test_words_styles():
    c = client()
    names = c.get("/api/words", params={"count": 10, "min_len": 5, "max_len": 6, "style": "suffix", "seed": 1}).json()["names"]
    assert len(names) == 10 and all(any(n.endswith(s) for s in main.SUFFIXES) for n in names)
