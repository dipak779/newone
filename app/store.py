"""SQLite storage for every check result (manual scans and the background hunter)."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from .finder import Result

SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
    username   TEXT NOT NULL,
    platform   TEXT NOT NULL,
    status     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    checked_at REAL NOT NULL,
    first_free REAL,             -- when we first saw it AVAILABLE (kept while it stays free)
    PRIMARY KEY (username, platform)
);
CREATE INDEX IF NOT EXISTS idx_checks_status ON checks(status, checked_at);
"""


class Store:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            # v1: Fragment "Available" (= for sale) was wrongly counted as free; re-check those names.
            with self.db:
                self.db.execute("DELETE FROM checks WHERE platform='telegram' AND status='AVAILABLE'")
                self.db.execute("PRAGMA user_version = 1")
        if version < 2:
            # v2: Telegram names only count as AVAILABLE once Telegram itself confirms them.
            with self.db:
                self.db.execute("UPDATE checks SET status='UNCONFIRMED' WHERE platform='telegram' "
                                "AND status='AVAILABLE' AND detail != 'confirmed by Telegram'")
                self.db.execute("PRAGMA user_version = 2")

    def save(self, r: Result, now: float | None = None) -> None:
        now = now or time.time()
        with self.lock, self.db:
            self.db.execute(
                """INSERT INTO checks(username, platform, status, detail, checked_at, first_free)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(username, platform) DO UPDATE SET
                     status=excluded.status, detail=excluded.detail, checked_at=excluded.checked_at,
                     first_free=CASE WHEN excluded.status='AVAILABLE'
                                     THEN COALESCE(checks.first_free, excluded.checked_at) ELSE NULL END""",
                (r.username, r.platform, r.status, r.detail, now, now if r.status == "AVAILABLE" else None),
            )

    def save_unknown(self, r: Result, now: float | None = None) -> None:
        """Record a blocked/failed check: never overwrites a real answer, only bumps an UNKNOWN row."""
        now = now or time.time()
        with self.lock, self.db:
            self.db.execute(
                """INSERT INTO checks(username, platform, status, detail, checked_at) VALUES (?, ?, 'UNKNOWN', ?, ?)
                   ON CONFLICT(username, platform) DO UPDATE SET detail=excluded.detail, checked_at=excluded.checked_at
                   WHERE checks.status='UNKNOWN'""",
                (r.username, r.platform, r.detail, now),
            )

    def last_checked(self, username: str) -> float | None:
        """Oldest check time across platforms (None if any platform was never checked)."""
        with self.lock:
            rows = self.db.execute("SELECT checked_at FROM checks WHERE username=?", (username,)).fetchall()
        return min(r[0] for r in rows) if rows else None

    def statuses(self, usernames: list[str]) -> dict[str, dict[str, tuple[str, float]]]:
        out: dict[str, dict[str, tuple[str, float]]] = {}
        with self.lock:
            for i in range(0, len(usernames), 500):
                chunk = usernames[i:i + 500]
                q = f"SELECT username, platform, status, checked_at FROM checks WHERE username IN ({','.join('?' * len(chunk))})"
                for row in self.db.execute(q, chunk):
                    out.setdefault(row[0], {})[row[1]] = (row[2], row[3])
        return out

    def available(self, platform: str | None = None, limit: int = 200) -> list[dict]:
        q = "SELECT username, platform, detail, checked_at, first_free FROM checks WHERE status='AVAILABLE'"
        args: list = []
        if platform:
            q += " AND platform=?"
            args.append(platform)
        q += " ORDER BY first_free DESC LIMIT ?"
        args.append(limit)
        with self.lock:
            return [dict(r) for r in self.db.execute(q, args)]

    def finds(self, platform: str | None = None, limit: int = 300) -> list[dict]:
        """Usernames AVAILABLE on `platform` (or on any platform), each with every platform's status."""
        q = "SELECT username, MAX(first_free) AS found FROM checks WHERE status='AVAILABLE'"
        args: list = []
        if platform:
            q += " AND platform=?"
            args.append(platform)
        q += " GROUP BY username ORDER BY found DESC LIMIT ?"
        args.append(limit)
        with self.lock:
            rows = [dict(r) for r in self.db.execute(q, args)]
        if not rows:
            return []
        with self.lock:
            names = [r["username"] for r in rows]
            st: dict[str, dict] = {}
            q2 = f"SELECT username, platform, status, detail, checked_at FROM checks WHERE username IN ({','.join('?' * len(names))})"
            for r in self.db.execute(q2, names):
                st.setdefault(r[0], {})[r[1]] = {"status": r[2], "detail": r[3], "checked_at": r[4]}
        return [{"username": r["username"], "found": r["found"], "platforms": st.get(r["username"], {})} for r in rows]

    def clear_unconfirmed_telegram(self) -> int:
        """Forget Telegram AVAILABLE results that Telegram itself didn't confirm, so they're re-checked."""
        with self.lock, self.db:
            cur = self.db.execute("DELETE FROM checks WHERE platform='telegram' AND "
                                  "(status='UNCONFIRMED' OR (status='AVAILABLE' AND detail != 'confirmed by Telegram'))")
            return cur.rowcount

    def counts(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        with self.lock:
            for p, s, n in self.db.execute("SELECT platform, status, COUNT(*) FROM checks GROUP BY 1, 2"):
                out.setdefault(p, {})[s] = n
        return out
