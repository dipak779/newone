"""HandleHunt web portal: FastAPI backend + static dashboard.

Run:  uvicorn app.main:app --reload   then open http://localhost:8000
"""
from __future__ import annotations

import hmac
import json
import os
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import finder
from .hunter import Hunter
from .store import Store

STATIC = Path(__file__).with_name("static")

MAX_NAMES_PER_SCAN = int(os.getenv("MAX_NAMES_PER_SCAN", "50"))
CHECKS_PER_HOUR_PER_IP = int(os.getenv("CHECKS_PER_HOUR_PER_IP", "300"))
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "900"))
# Minimum gap between outgoing requests to each platform, shared by ALL users of the server,
# so the portal as a whole stays under the platforms' rate limits.
MIN_INTERVAL = {
    "telegram": float(os.getenv("TELEGRAM_INTERVAL", "0.7")),
    "instagram": float(os.getenv("INSTAGRAM_INTERVAL", "3.0")),
}

DB_PATH = os.getenv("DB_PATH", str(Path(__file__).resolve().parent.parent / "data" / "handlehunt.db"))
HUNT_ENABLED = os.getenv("HUNT_ENABLED", "1") == "1"
HUNT_INTERVAL = float(os.getenv("HUNT_INTERVAL_SECONDS", "60"))
HUNT_BATCH = int(os.getenv("HUNT_BATCH", "5"))
HUNT_VARIANTS = os.getenv("HUNT_VARIANTS", "1") == "1"  # also hunt getX / Xhq style names
HELPER_TOKEN = os.getenv("HELPER_TOKEN", "")  # enables the home helper endpoints when set
HELPER_PLATFORMS = ("instagram",)
HUNT_PLATFORMS = [p for p in os.getenv("HUNT_PLATFORMS", "telegram,instagram").split(",") if p in finder.CHECKERS]

PREFIXES = ["get", "the", "its", "im", "real", "hey", "mr"]
SUFFIXES = ["hq", "app", "io", "x", "club", "lab", "pro"]


class Throttle:
    def __init__(self, interval: float):
        self.interval, self.lock, self.next_at = interval, threading.Lock(), 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            if self.next_at > now:
                time.sleep(self.next_at - now)
            self.next_at = time.monotonic() + self.interval

    def penalize(self, seconds: float) -> None:
        with self.lock:
            self.next_at = max(self.next_at, time.monotonic() + seconds)


class Checker:
    """Shared checker: cache + per-platform throttling. `fetch` is swappable for tests."""

    def __init__(self, fetch: finder.Fetcher = finder.http_get, store: Store | None = None):
        self.fetch, self.store = fetch, store
        self.cache: dict[tuple[str, str], tuple[float, finder.Result]] = {}
        self.throttles = {p: Throttle(MIN_INTERVAL[p]) for p in finder.CHECKERS}

    def check(self, name: str, platform: str, fresh: bool = False) -> tuple[finder.Result, bool]:
        key = (name, platform)
        hit = self.cache.get(key)
        if hit and not fresh and time.time() - hit[0] < CACHE_TTL:
            return hit[1], True

        def throttled_fetch(url: str, headers: dict) -> finder.Response:
            self.throttles[platform].wait()
            return self.fetch(url, headers)

        res = finder.CHECKERS[platform](name, fetch=throttled_fetch)
        if "429" in res.detail:
            self.throttles[platform].penalize(60)
        if res.status != finder.UNKNOWN:
            self.cache[key] = (time.time(), res)
            if self.store:
                self.store.save(res)
        return res, False


class RateLimiter:
    def __init__(self, limit: int, window: float = 3600):
        self.limit, self.window = limit, window
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def remaining(self, ip: str) -> int:
        with self.lock:
            q, cutoff = self.hits[ip], time.time() - self.window
            while q and q[0] < cutoff:
                q.popleft()
            return self.limit - len(q)

    def take(self, ip: str, n: int = 1) -> None:
        with self.lock:
            self.hits[ip].extend([time.time()] * n)


WORDS = finder.load_words(finder.DEFAULT_WORDS)


def hunt_pool() -> list[str]:
    pool = list(WORDS)
    if HUNT_VARIANTS:
        pool += [p + w for w in WORDS for p in PREFIXES] + [w + s for w in WORDS for s in SUFFIXES]
    return pool


def build(fetch: finder.Fetcher = finder.http_get, db_path: str = DB_PATH) -> None:
    """(Re)create the shared services. Tests call this with a fake fetcher and :memory:."""
    store = Store(db_path)
    app.state.store = store
    app.state.checker = Checker(fetch, store)
    app.state.limiter = RateLimiter(CHECKS_PER_HOUR_PER_IP)
    app.state.hunter = Hunter(hunt_pool(), app.state.checker.check, store, HUNT_PLATFORMS,
                              interval=HUNT_INTERVAL, batch=HUNT_BATCH)


@asynccontextmanager
async def lifespan(_app):
    if HUNT_ENABLED:
        _app.state.hunter.start()
    yield
    _app.state.hunter.stop()


app = FastAPI(title="HandleHunt", docs_url="/api/docs", lifespan=lifespan)
build()


def clean_names(raw: str) -> list[str]:
    parts = raw.replace("\n", ",").replace(" ", ",").split(",")
    names = [p.strip().lstrip("@").lower() for p in parts if p.strip().lstrip("@")]
    return list(dict.fromkeys(names))


@app.get("/api/health")
def health():
    return {"ok": True, "words": len(WORDS)}


@app.get("/api/words")
def words(
    count: int = Query(20, ge=1, le=MAX_NAMES_PER_SCAN),
    min_len: int = Query(3, ge=1, le=30),
    max_len: int = Query(8, ge=1, le=30),
    style: str = Query("plain", pattern="^(plain|prefix|suffix|mixed)$"),
    seed: int | None = None,
):
    import random

    rng = random.Random(seed)
    base = [w for w in WORDS if min_len <= len(w) <= max_len] or WORDS
    picks = []
    for w in rng.sample(base, min(count, len(base))):
        s = style if style != "mixed" else rng.choice(["plain", "prefix", "suffix"])
        if s == "prefix":
            w = rng.choice(PREFIXES) + w
        elif s == "suffix":
            w = w + rng.choice(SUFFIXES)
        picks.append(w)
    return {"names": picks}


@app.get("/api/limits")
def limits(request: Request):
    ip = request.client.host if request.client else "?"
    return {
        "remaining": app.state.limiter.remaining(ip),
        "per_hour": CHECKS_PER_HOUR_PER_IP,
        "max_names": MAX_NAMES_PER_SCAN,
    }


@app.get("/api/available")
def available(platform: str | None = Query(None, pattern="^(telegram|instagram)$"),
              limit: int = Query(200, ge=1, le=1000)):
    """Names the hunter (or anyone's scan) found AVAILABLE, newest first."""
    return {"items": app.state.store.available(platform, limit)}


@app.get("/api/finds")
def finds(platform: str | None = Query(None, pattern="^(telegram|instagram)$"),
          limit: int = Query(300, ge=1, le=1000)):
    """Names free on `platform` (or anywhere), with the yes/no status on every platform."""
    return {"items": app.state.store.finds(platform, limit),
            "platforms": [p for p in finder.CHECKERS
                          if p in HUNT_PLATFORMS or (HELPER_TOKEN and p in HELPER_PLATFORMS)]}


# ---------- home helper: a script on the owner's computer checks Instagram and reports back

def require_helper(token: str | None) -> None:
    if not HELPER_TOKEN:
        raise HTTPException(404, "Helper is not enabled (set HELPER_TOKEN on the server).")
    if not token or not hmac.compare_digest(token, HELPER_TOKEN):
        raise HTTPException(401, "Bad helper token.")


@app.get("/api/helper/next")
def helper_next(platform: str = Query("instagram"), n: int = Query(5, ge=1, le=20),
                x_helper_token: str | None = Header(None)):
    require_helper(x_helper_token)
    if platform not in HELPER_PLATFORMS:
        raise HTTPException(400, "Unsupported platform.")
    hunter: Hunter = app.state.hunter
    app.state.helper_seen_at = time.time()
    return {"names": hunter.next_batch(platforms=[platform], n=n, prefer_free_on="telegram")}


@app.post("/api/helper/results")
def helper_results(results: list[dict] = Body(...), x_helper_token: str | None = Header(None)):
    require_helper(x_helper_token)
    saved = 0
    for r in results[:50]:
        name, platform, status = str(r.get("username", "")).lower(), r.get("platform"), r.get("status")
        if platform not in HELPER_PLATFORMS or status not in (finder.AVAILABLE, finder.TAKEN, finder.UNKNOWN):
            continue
        if not finder.IG_RULE.match(name):
            continue
        res = finder.Result(name, platform, status, str(r.get("detail", ""))[:200])
        if status == finder.UNKNOWN:
            app.state.store.save_unknown(res)
        else:
            app.state.store.save(res)
            app.state.checker.cache.pop((name, platform), None)
        saved += 1
    app.state.helper_seen_at = time.time()
    return {"saved": saved}


@app.get("/api/hunter")
def hunter_status():
    seen = getattr(app.state, "helper_seen_at", None)
    return {**app.state.hunter.status(), "counts": app.state.store.counts(), "enabled": HUNT_ENABLED,
            "helper": {"enabled": bool(HELPER_TOKEN), "last_seen_at": seen}}


@app.get("/api/scan")
def scan(request: Request, names: str, platforms: str = "telegram,instagram"):
    """Server-Sent Events stream: one `result` event per (name, platform), then `done`."""
    name_list = clean_names(names)
    plats = [p for p in platforms.split(",") if p in finder.CHECKERS]
    if not name_list or not plats:
        raise HTTPException(400, "Give at least one name and one platform.")
    if len(name_list) > MAX_NAMES_PER_SCAN:
        raise HTTPException(400, f"Max {MAX_NAMES_PER_SCAN} names per scan.")

    ip = request.client.host if request.client else "?"
    needed = len(name_list) * len(plats)
    if app.state.limiter.remaining(ip) < needed:
        raise HTTPException(429, "Hourly check limit reached. Try again later.")
    app.state.limiter.take(ip, needed)

    checker: Checker = app.state.checker

    def stream():
        yield f"event: start\ndata: {json.dumps({'total': needed})}\n\n"
        for name in name_list:
            for p in plats:
                res, cached = checker.check(name, p)
                payload = {**asdict(res), "cached": cached}
                yield f"event: result\ndata: {json.dumps(payload)}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
