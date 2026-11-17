#!/usr/bin/env python3
"""HandleHunt home helper: checks Instagram usernames from YOUR internet connection
and sends the results to your HandleHunt site, where they show up in Live finds.

Needs only Python 3.8+ (no installs). Run it on a computer that stays on:

    python3 ig_helper.py --server https://handlehunt.onrender.com --token YOUR_HELPER_TOKEN

Stop it with Ctrl+C. It checks a few names per minute and backs off when Instagram says "slow down".
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
IG_URL = "https://www.instagram.com/api/v1/users/web_profile_info/?username={}"
IG_APP_ID = "936619743392459"


def http(url, method="GET", headers=None, data=None, timeout=20):
    body = json.dumps(data).encode() if data is not None else None
    h = {"User-Agent": UA, **(headers or {})}
    if body is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace") if e.fp else ""
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, str(e)


def check_instagram(name):
    code, body = http(IG_URL.format(name), headers={"X-IG-App-ID": IG_APP_ID, "Accept": "application/json"})
    if code == 404:
        return "AVAILABLE", "no profile (could still be reserved/banned)"
    if code == 200:
        try:
            user = (json.loads(body).get("data") or {}).get("user")
        except ValueError:
            return "UNKNOWN", "unparseable response"
        if user:
            return "TAKEN", f"{user.get('edge_followed_by', {}).get('count', '?')} followers"
        return "AVAILABLE", "empty profile result"
    return "UNKNOWN", f"HTTP {code}"


def main():
    ap = argparse.ArgumentParser(description="Check Instagram names from home for HandleHunt.")
    ap.add_argument("--server", default=os.getenv("HANDLEHUNT_SERVER", "https://handlehunt.onrender.com"))
    ap.add_argument("--token", default=os.getenv("HANDLEHUNT_HELPER_TOKEN"), help="the HELPER_TOKEN you set on Render")
    ap.add_argument("--batch", type=int, default=5, help="names per round (default 5)")
    ap.add_argument("--interval", type=float, default=60, help="seconds per round (default 60)")
    ap.add_argument("--gap", type=float, default=5, help="seconds between Instagram requests (default 5)")
    a = ap.parse_args()
    if not a.token:
        sys.exit("Missing --token (the HELPER_TOKEN value from your Render environment).")
    server = a.server.rstrip("/")
    auth = {"X-Helper-Token": a.token}
    pause = 0
    print(f"HandleHunt helper -> {server}  (Ctrl+C to stop)")

    while True:
        started = time.time()
        code, body = http(f"{server}/api/helper/next?platform=instagram&n={a.batch}", headers=auth)
        if code != 200:
            print(f"[server] HTTP {code}: {body[:150]}  - retrying in 60s")
            time.sleep(60)
            continue
        names = json.loads(body)["names"]
        results = []
        for i, name in enumerate(names):
            status, detail = check_instagram(name)
            results.append({"username": name, "platform": "instagram", "status": status, "detail": detail})
            mark = {"AVAILABLE": "YES  free", "TAKEN": "no   taken", "UNKNOWN": "?    " + detail}[status]
            print(f"  @{name:<20} {mark}")
            if status == "UNKNOWN":
                break  # Instagram is pushing back: stop this round
            if i < len(names) - 1:
                time.sleep(a.gap)
        if results:
            http(f"{server}/api/helper/results", "POST", auth, results)
        blocked = results and results[-1]["status"] == "UNKNOWN"
        pause = min(max(pause * 2, 300), 3600) if blocked else 0
        wait = pause or max(0, a.interval - (time.time() - started))
        if pause:
            print(f"  Instagram is limiting requests - pausing {int(pause / 60)} min")
        time.sleep(wait)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
