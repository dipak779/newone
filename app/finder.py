#!/usr/bin/env python3
"""Username finder: check short, word-like usernames on Telegram and Instagram.

Pure standard library (Python 3.9+). Usage examples:

    python -m app.finder money cash karma
    python -m app.finder --random 30 --min-len 5 --max-len 7
    python -m app.finder --file my_words.txt --platform telegram --out results.csv

Results are best-effort signals from public pages, not guarantees:
  AVAILABLE  - the public page/endpoint says nobody has it (may still be reserved/banned)
  TAKEN      - an account, channel or group is using it
  FRAGMENT   - Telegram name is held/sold/auctioned on fragment.com
  INVALID    - breaks the platform's username rules (e.g. Telegram needs 5+ chars)
  UNKNOWN    - blocked, rate limited, or response not recognised
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable

AVAILABLE, TAKEN, FRAGMENT, INVALID, UNKNOWN = "AVAILABLE", "TAKEN", "FRAGMENT", "INVALID", "UNKNOWN"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
IG_APP_ID = "936619743392459"  # public app id used by instagram.com's own web client

TG_RULE = re.compile(r"^[a-zA-Z](?!.*__)[a-zA-Z0-9_]{3,30}[a-zA-Z0-9]$")  # 5-32 chars
IG_RULE = re.compile(r"^(?!.*\.\.)(?!\.)(?!.*\.$)[a-zA-Z0-9._]{1,30}$")

DEFAULT_WORDS = Path(__file__).with_name("words.txt")


@dataclass
class Response:
    status: int
    body: str
    url: str


Fetcher = Callable[[str, dict], Response]


def http_get(url: str, headers: dict | None = None, timeout: float = 15.0) -> Response:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return Response(r.status, r.read().decode("utf-8", "replace"), r.geturl())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace") if e.fp else ""
        return Response(e.code, body, url)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return Response(0, str(e), url)


@dataclass
class Result:
    username: str
    platform: str
    status: str
    detail: str = ""


# --------------------------------------------------------------------------- Telegram

def check_telegram(username: str, fetch: Fetcher = http_get, use_fragment: bool = True) -> Result:
    if not TG_RULE.match(username):
        return Result(username, "telegram", INVALID, "Telegram needs 5-32 chars: letters, digits, _")

    r = fetch(f"https://t.me/{username}", {})
    if r.status != 200:
        return Result(username, "telegram", UNKNOWN, f"t.me HTTP {r.status}")
    if "tgme_page_title" in r.body:
        kind = "channel/group" if re.search(r"subscribers|members", r.body) else "user/bot"
        return Result(username, "telegram", TAKEN, kind)

    if use_fragment:
        listed, frag = fragment_status(username, fetch)
        if listed is None:
            return Result(username, "telegram", UNKNOWN, f"fragment.com unreachable ({frag})")
        if listed:
            # Any name with its own Fragment page belongs to Fragment's marketplace. Note that
            # Fragment's "Available" means "available to BUY", not free to claim.
            label = {"available": "for sale on Fragment", "unavailable": "reserved on Fragment"}.get(
                (frag or "").lower(), f"Fragment: {frag or 'listed'}")
            return Result(username, "telegram", FRAGMENT, label)
    return Result(username, "telegram", AVAILABLE, "no t.me page, not on Fragment")


def fragment_status(username: str, fetch: Fetcher = http_get) -> tuple[bool | None, str]:
    """(listed, status_text). listed=None means Fragment couldn't be checked (treat as unknown).
    Names Fragment doesn't know about redirect to its search page (?query=...)."""
    r = fetch(f"https://fragment.com/username/{username}", {})
    if r.status != 200:
        return None, f"HTTP {r.status}"
    if "?query=" in r.url:
        return False, ""
    m = re.search(r'tm-section-header-status[^"]*"[^>]*>\s*([^<]+?)\s*<', r.body)
    return True, (m.group(1) if m else "")


# --------------------------------------------------------------------------- Instagram

def check_instagram(username: str, fetch: Fetcher = http_get) -> Result:
    if not IG_RULE.match(username):
        return Result(username, "instagram", INVALID, "Instagram: 1-30 chars, letters, digits, . _")

    r = fetch(
        f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}",
        {"X-IG-App-ID": IG_APP_ID, "Accept": "application/json"},
    )
    if r.status == 404:
        return Result(username, "instagram", AVAILABLE, "no profile (could still be reserved/banned)")
    if r.status == 200:
        try:
            user = (json.loads(r.body).get("data") or {}).get("user")
        except json.JSONDecodeError:
            return Result(username, "instagram", UNKNOWN, "unparseable 200 response")
        if user:
            return Result(username, "instagram", TAKEN, f"{user.get('edge_followed_by', {}).get('count', '?')} followers")
        return Result(username, "instagram", AVAILABLE, "empty profile result")
    if r.status == 429:
        return Result(username, "instagram", UNKNOWN, "rate limited (HTTP 429) - slow down")
    return Result(username, "instagram", UNKNOWN, f"HTTP {r.status} (login wall / blocked)")


CHECKERS = {"telegram": check_telegram, "instagram": check_instagram}


# --------------------------------------------------------------------------- candidates

def load_words(path: Path) -> list[str]:
    words = []
    for line in path.read_text(encoding="utf-8").splitlines():
        w = line.strip().lower().lstrip("@")
        if w and not w.startswith("#"):
            words.append(w)
    return list(dict.fromkeys(words))  # dedupe, keep order


def pick_candidates(words: Iterable[str], count: int | None, min_len: int, max_len: int,
                    seed: int | None = None) -> list[str]:
    pool = [w for w in words if min_len <= len(w) <= max_len]
    if count is None or count >= len(pool):
        return pool
    return random.Random(seed).sample(pool, count)


# --------------------------------------------------------------------------- run

def run(usernames: list[str], platforms: list[str], delay: float, fetch: Fetcher = http_get,
        on_result: Callable[[Result], None] | None = None) -> list[Result]:
    results, backoff = [], delay
    for i, name in enumerate(usernames):
        for p in platforms:
            res = CHECKERS[p](name, fetch=fetch)
            results.append(res)
            if on_result:
                on_result(res)
            # Be polite: double the wait after a rate limit, recover gradually otherwise.
            backoff = min(backoff * 2, 120) if "429" in res.detail else max(delay, backoff / 2)
        if i < len(usernames) - 1 and delay > 0:
            time.sleep(backoff)
    return results


COLORS = {AVAILABLE: "\033[92m", TAKEN: "\033[90m", FRAGMENT: "\033[93m", INVALID: "\033[91m", UNKNOWN: "\033[95m"}


def print_result(r: Result, color: bool) -> None:
    status = f"{COLORS[r.status]}{r.status:<9}\033[0m" if color else f"{r.status:<9}"
    print(f"@{r.username:<20} {r.platform:<10} {status} {r.detail}", flush=True)


def save(results: list[Result], path: Path) -> None:
    rows = [asdict(r) for r in results]
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    else:
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["username", "platform", "status", "detail"])
            w.writeheader()
            w.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Find available word usernames on Telegram and Instagram.")
    ap.add_argument("usernames", nargs="*", help="usernames to check (with or without @)")
    ap.add_argument("-f", "--file", type=Path, help="word list file, one per line")
    ap.add_argument("-r", "--random", type=int, metavar="N", help="check N random words from the list")
    ap.add_argument("--min-len", type=int, default=3)
    ap.add_argument("--max-len", type=int, default=8)
    ap.add_argument("--seed", type=int, help="random seed for repeatable picks")
    ap.add_argument("-p", "--platform", nargs="+", choices=list(CHECKERS), default=list(CHECKERS))
    ap.add_argument("-d", "--delay", type=float, default=3.0, help="seconds between names (default 3)")
    ap.add_argument("-o", "--out", type=Path, help="save results to .csv or .json")
    ap.add_argument("--only-available", action="store_true", help="print only AVAILABLE hits")
    ap.add_argument("--no-color", action="store_true")
    a = ap.parse_args(argv)

    if a.usernames:
        names = [u.strip().lstrip("@").lower() for u in a.usernames]
    else:
        names = pick_candidates(load_words(a.file or DEFAULT_WORDS), a.random, a.min_len, a.max_len, a.seed)
    if not names:
        ap.error("no usernames to check (adjust --min-len/--max-len or the word list)")

    color = sys.stdout.isatty() and not a.no_color
    print(f"Checking {len(names)} name(s) on {', '.join(a.platform)} ...\n")

    def show(r: Result) -> None:
        if not a.only_available or r.status == AVAILABLE:
            print_result(r, color)

    try:
        results = run(names, a.platform, a.delay, on_result=show)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130

    hits = [r for r in results if r.status == AVAILABLE]
    print(f"\n{len(hits)} available out of {len(results)} checks.")
    for r in hits:
        print(f"  @{r.username} ({r.platform})")
    if a.out:
        save(results, a.out)
        print(f"Saved to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
