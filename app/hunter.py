"""Background hunter: every minute, check the next batch of word usernames and record results.

Order of work each round:
  1. names never checked yet (shuffled once, so the feed isn't alphabetical)
  2. names that were AVAILABLE (older than RECHECK_FREE) or UNKNOWN/blocked (older than RECHECK_UNKNOWN)
  3. everything else older than RECHECK_ALL
"""
from __future__ import annotations

import random
import threading
import time
from typing import Callable

from .store import Store


class Hunter:
    def __init__(self, pool: list[str], check: Callable, store: Store, platforms: list[str],
                 interval: float = 60, batch: int = 5,
                 recheck_free: float = 6 * 3600, recheck_all: float = 7 * 86400,
                 recheck_unknown: float = 3600, seed: int = 7):
        self.pool = list(dict.fromkeys(pool))
        random.Random(seed).shuffle(self.pool)
        self.check, self.store, self.platforms = check, store, platforms
        self.interval, self.batch = interval, batch
        self.recheck_free, self.recheck_all = recheck_free, recheck_all
        self.recheck_unknown = recheck_unknown
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.rounds = 0
        self.last_round_at: float | None = None
        self.last_batch: list[str] = []
        self.last_error = ""

    # ---- choosing work
    def next_batch(self, now: float | None = None) -> list[str]:
        now = now or time.time()
        seen = self.store.statuses(self.pool)
        fresh, stale_free, stale = [], [], []
        for name in self.pool:
            st = seen.get(name, {})
            if any(p not in st for p in self.platforms):
                fresh.append(name)
                continue
            oldest = min(st[p][1] for p in self.platforms)
            free = any(st[p][0] == "AVAILABLE" for p in self.platforms)
            unknown = any(st[p][0] == "UNKNOWN" for p in self.platforms)
            if (free and now - oldest > self.recheck_free) or (unknown and now - oldest > self.recheck_unknown):
                stale_free.append((oldest, name))
            elif now - oldest > self.recheck_all:
                stale.append((oldest, name))
        picks = fresh[: self.batch]
        for group in (sorted(stale_free), sorted(stale)):
            picks += [n for _, n in group][: self.batch - len(picks)]
        return picks

    # ---- running
    def run_round(self) -> list:
        names = self.next_batch()
        results = []
        for name in names:
            for p in self.platforms:
                if self.stop_event.is_set():
                    return results
                res, _cached = self.check(name, p, fresh=True)  # the checker saves known results
                if res.status == "UNKNOWN":
                    self.store.save_unknown(res)
                results.append(res)
        self.rounds += 1
        self.last_round_at = time.time()
        self.last_batch = names
        return results

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                self.run_round()
                self.last_error = ""
            except Exception as e:  # keep hunting even if one round blows up
                self.last_error = f"{type(e).__name__}: {e}"
            self.stop_event.wait(max(0.0, self.interval - (time.monotonic() - started)))

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop, name="hunter", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)

    def status(self) -> dict:
        checked = self.store.statuses(self.pool)
        done = sum(1 for n in self.pool if all(p in checked.get(n, {}) for p in self.platforms))
        return {
            "running": bool(self.thread and self.thread.is_alive()),
            "interval_seconds": self.interval,
            "batch": self.batch,
            "pool_size": len(self.pool),
            "pool_checked": done,
            "rounds": self.rounds,
            "last_round_at": self.last_round_at,
            "last_batch": self.last_batch,
            "last_error": self.last_error,
        }
