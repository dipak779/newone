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
    main.build(fake_fetch, ":memory:")
    main.app.state.checker.confirmer = FakeConfirmer({})  # a connected Telegram that confirms by default
    return TestClient(main.app)


def events(text):
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(l.split(": ", 1) for l in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


class FakeConfirmer:
    def __init__(self, answers, logged_in=True):
        self.answers, self.logged_in, self.asked = answers, logged_in, []

    def authorized(self, max_age=300):
        return self.logged_in

    def check(self, name):
        self.asked.append(name)
        return self.answers.get(name, ("AVAILABLE", "confirmed by Telegram"))


def test_telegram_confirmation_filters_reserved_names():
    c = client()
    conf = FakeConfirmer({"getelite": ("INVALID", "Telegram says invalid (reserved or banned)")})
    main.app.state.checker.confirmer = conf
    r = c.get("/api/scan", params={"names": "getelite,therapy,money", "platforms": "telegram"})
    got = {d["username"]: d["status"] for e, d in events(r.text) if e == "result"}
    assert got == {"getelite": "INVALID", "therapy": "AVAILABLE", "money": "TAKEN"}
    assert conf.asked == ["getelite", "therapy"]  # taken-by-page names never hit the Telegram API
    assert [i["username"] for i in c.get("/api/finds").json()["items"]] == ["therapy"]


def test_unconfirmed_results_are_labelled_and_cleared_after_login():
    c = client()
    main.app.state.checker.confirmer = FakeConfirmer({}, logged_in=False)
    r = c.get("/api/scan", params={"names": "therapy", "platforms": "telegram"})
    d = [d for e, d in events(r.text) if e == "result"][0]
    assert d["status"] == "UNCONFIRMED"
    assert c.get("/api/finds").json()["items"] == []  # never listed until confirmed
    assert main.app.state.store.clear_unconfirmed_telegram() == 1


def test_admin_endpoints_need_token_and_config(monkeypatch):
    c = client()
    monkeypatch.setattr(main, "HELPER_TOKEN", "s3cret")
    assert c.get("/api/admin/telegram").status_code == 401
    r = c.get("/api/admin/telegram", headers={"X-Helper-Token": "s3cret"})
    assert r.status_code == 404 and "TG_API_ID" in r.json()["detail"]
    assert c.get("/admin").status_code == 200


def test_index_and_health():
    c = client()
    assert c.get("/").status_code == 200
    assert c.get("/api/health").json()["ok"]


def test_telegram_never_available_without_confirmation():
    c = client()
    main.app.state.checker.confirmer = None
    r = c.get("/api/scan", params={"names": "therapy", "platforms": "telegram"})
    assert [d["status"] for e, d in events(r.text) if e == "result"] == ["UNCONFIRMED"]
    assert c.get("/api/finds").json()["items"] == []


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


def test_hunter_round_feeds_available_list():
    c = client()
    h = main.Hunter(["money", "therapy", "pleased"], main.app.state.checker.check, main.app.state.store,
                    ["telegram", "instagram"], batch=2)
    main.app.state.hunter = h
    h.run_round()
    h.run_round()
    assert h.next_batch() == []  # everything checked, nothing stale
    free = {(i["username"], i["platform"]) for i in c.get("/api/available").json()["items"]}
    assert free == {(n, p) for n in ("therapy", "pleased") for p in ("telegram", "instagram")}
    st = c.get("/api/hunter").json()
    assert st["pool_checked"] == 3 and st["rounds"] == 2


def test_unknown_never_overwrites_and_is_retried():
    from app.store import Store
    s = Store(":memory:")
    s.save(finder.Result("therapy", "instagram", "AVAILABLE"), now=100)
    s.save_unknown(finder.Result("therapy", "instagram", "UNKNOWN", "HTTP 429"), now=200)
    assert s.available()[0]["username"] == "therapy"
    s.save(finder.Result("therapy", "telegram", "AVAILABLE"), now=100)
    s.save_unknown(finder.Result("karma1", "instagram", "UNKNOWN"), now=100)
    s.save(finder.Result("karma1", "telegram", "TAKEN"), now=100)
    h = main.Hunter(["therapy", "karma1"], None, s, ["telegram", "instagram"], recheck_free=10**9)
    assert h.next_batch(now=100 + 3601) == ["karma1"]  # unknown retried after an hour
    s.save(finder.Result("therapy", "instagram", "TAKEN"), now=300)
    assert [i["platform"] for i in s.available()] == ["telegram"]


def test_blocked_platform_is_paused_and_others_continue():
    from app.store import Store
    store = Store(":memory:")
    calls = []

    def check(name, platform, fresh=False):
        calls.append(platform)
        status = "UNKNOWN" if platform == "instagram" else "AVAILABLE"
        res = finder.Result(name, platform, status, "HTTP 429" if status == "UNKNOWN" else "")
        if status != "UNKNOWN":
            store.save(res)
        return res, False

    pool = [f"name{i}x" for i in range(20)]
    h = main.Hunter(pool, check, store, ["telegram", "instagram"], batch=5, block_after=3)
    h.run_round()
    assert calls.count("instagram") == 3 and "instagram" in h.status()["paused"]
    calls.clear()
    h.run_round()  # instagram paused: telegram keeps going on NEW names
    assert set(calls) == {"telegram"} and len(calls) == 5
    assert len(store.available("telegram")) == 10


def test_finds_and_helper(monkeypatch):
    c = client()
    c.get("/api/scan", params={"names": "therapy,money", "platforms": "telegram"})
    items = c.get("/api/finds").json()["items"]
    assert [i["username"] for i in items] == ["therapy"]
    assert items[0]["platforms"]["telegram"]["status"] == "AVAILABLE"

    assert c.get("/api/helper/next").status_code == 404  # disabled without a token
    monkeypatch.setattr(main, "HELPER_TOKEN", "s3cret")
    assert c.get("/api/helper/next", headers={"X-Helper-Token": "nope"}).status_code == 401
    h = {"X-Helper-Token": "s3cret"}
    main.app.state.hunter = main.Hunter(["money", "therapy", "pleased"], main.app.state.checker.check,
                                        main.app.state.store, ["telegram"])
    names = c.get("/api/helper/next", params={"n": 2}, headers=h).json()["names"]
    assert names[0] == "therapy"  # free on Telegram -> checked on Instagram first
    r = c.post("/api/helper/results", headers=h, json=[
        {"username": "therapy", "platform": "instagram", "status": "TAKEN", "detail": "9 followers"},
        {"username": "bad name!", "platform": "instagram", "status": "AVAILABLE"},
        {"username": "x", "platform": "telegram", "status": "AVAILABLE"},
    ])
    assert r.json()["saved"] == 1
    items = c.get("/api/finds").json()["items"]
    assert items[0]["platforms"]["instagram"]["status"] == "TAKEN"


def test_migration_clears_old_telegram_available(tmp_path):
    from app.store import Store
    db = tmp_path / "old.db"
    s = Store(db)
    s.db.execute("PRAGMA user_version = 0")
    s.save(finder.Result("wiselab", "telegram", "AVAILABLE"))
    s.save(finder.Result("money", "telegram", "TAKEN"))
    s.db.close()
    s2 = Store(db)
    assert s2.available() == [] and s2.counts()["telegram"] == {"TAKEN": 1}
    s2.save(finder.Result("newone", "telegram", "AVAILABLE"))
    s2.db.close()
    assert len(Store(db).available()) == 1  # migration runs only once


def test_confirmer_maps_telegram_errors(tmp_path):
    from telethon import errors
    from app.tg_confirm import TelegramConfirmer
    c = TelegramConfirmer(1, "x", tmp_path / "s.session", min_gap=0)

    def answer(value):
        def run(coro, timeout=60):
            coro.close()
            if isinstance(value, Exception):
                raise value
            return value
        return run
    cases = [
        (True, "AVAILABLE"), (False, "TAKEN"),
        (errors.UsernameInvalidError(request=None), "INVALID"),
        (errors.UsernameOccupiedError(request=None), "TAKEN"),
        (errors.UsernamePurchaseAvailableError(request=None), "FRAGMENT"),
    ]
    for value, expected in cases:
        c._run = answer(value)
        assert c.check("getelite")[0] == expected
    flood = errors.FloodWaitError(request=None, capture=120)
    c._run = answer(flood)
    assert c.check("getelite")[0] == "UNKNOWN"
    assert "wait" in c.check("other")[1]  # paused: doesn't call Telegram again


def test_word_list_is_real_words_only():
    words = main.WORDS
    assert words[:13] == "guys deed ominous half heck dreamed urgent craziest upcoming trolling embassy emotion wept".split()
    assert all(w.isalpha() and w.islower() and 3 <= len(w) <= 10 for w in words)
    for bad in ("getkarma", "karmahq", "john", "london", "lol"):
        assert bad not in words
    pool = main.hunt_pool()
    assert pool == words  # no getX / Xhq variants by default


def test_prune_except():
    from app.store import Store
    s = Store(":memory:")
    s.save(finder.Result("getelite", "telegram", "TAKEN"))
    s.save(finder.Result("embassy", "telegram", "TAKEN"))
    assert s.prune_except(["embassy"]) == 1
    assert s.counts()["telegram"] == {"TAKEN": 1}
