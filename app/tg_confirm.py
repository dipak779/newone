"""Confirm Telegram usernames with Telegram itself (account.checkUsername), via the owner's own account.

Public pages (t.me, fragment.com) can't see names Telegram has reserved or banned ("Sorry, this
username is invalid"). This asks Telegram the same question the app asks when you type a username.

Setup: TG_API_ID / TG_API_HASH from https://my.telegram.org, then log in once at /admin.
The login is saved as a session string in the data folder (keep that disk private).
"""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

from telethon import TelegramClient, errors, functions
from telethon.sessions import StringSession


class TelegramConfirmer:
    def __init__(self, api_id: int, api_hash: str, session_path: str | Path, min_gap: float = 10.0):
        self.api_id, self.api_hash = api_id, api_hash
        self.session_path = Path(session_path)
        self.min_gap = min_gap
        self.lock = threading.Lock()
        self.next_at = 0.0
        self.paused_until = 0.0
        self.last_error = ""
        self.checks = 0
        self._auth_cache: tuple[float, bool] = (0.0, False)
        self._phone: str | None = None
        self._code_hash: str | None = None
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, name="telegram", daemon=True).start()
        self.client: TelegramClient = self._run(self._make_client())

    # ---- plumbing
    def _run(self, coro, timeout: float = 60):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def _make_client(self) -> TelegramClient:
        saved = self.session_path.read_text().strip() if self.session_path.exists() else ""
        return TelegramClient(StringSession(saved), self.api_id, self.api_hash)

    async def _connected(self) -> TelegramClient:
        if not self.client.is_connected():
            await self.client.connect()
        return self.client

    def _save_session(self) -> None:
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_path.write_text(self.client.session.save())
        try:
            self.session_path.chmod(0o600)
        except OSError:
            pass

    # ---- login (driven from the /admin page)
    def authorized(self, max_age: float = 300) -> bool:
        ts, val = self._auth_cache
        if time.time() - ts < max_age:
            return val

        async def go():
            return await (await self._connected()).is_user_authorized()
        try:
            val = bool(self._run(go(), timeout=30))
        except Exception as e:  # network trouble shouldn't crash the app
            self.last_error = f"{type(e).__name__}: {e}"
            val = False
        self._auth_cache = (time.time(), val)
        return val

    def send_code(self, phone: str) -> None:
        async def go():
            sent = await (await self._connected()).send_code_request(phone)
            return sent.phone_code_hash
        self._code_hash = self._run(go())
        self._phone = phone

    def sign_in(self, code: str | None = None, password: str | None = None) -> str:
        """Returns 'ok' or 'password_needed'. Raises ValueError with a readable message on failure."""
        async def go():
            client = await self._connected()
            try:
                if password:
                    await client.sign_in(password=password)
                else:
                    if not (self._phone and self._code_hash):
                        raise ValueError("Send the code first.")
                    await client.sign_in(self._phone, code, phone_code_hash=self._code_hash)
            except errors.SessionPasswordNeededError:
                return "password_needed"
            except (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError):
                raise ValueError("That code is wrong or expired. Send a new one.")
            except errors.PasswordHashInvalidError:
                raise ValueError("Wrong 2-step verification password.")
            return "ok"
        result = self._run(go())
        if result == "ok":
            self._save_session()
            self._auth_cache = (time.time(), True)
        return result

    def logout(self) -> None:
        async def go():
            await (await self._connected()).log_out()
        try:
            self._run(go())
        finally:
            self.session_path.unlink(missing_ok=True)
            self._auth_cache = (time.time(), False)

    # ---- the actual check
    def check(self, username: str) -> tuple[str, str]:
        """(status, detail) with status AVAILABLE / TAKEN / FRAGMENT / INVALID / UNKNOWN."""
        with self.lock:
            if time.time() < self.paused_until:
                mins = int((self.paused_until - time.time()) / 60) + 1
                return "UNKNOWN", f"Telegram asked us to wait ({mins} min left)"
            delay = self.next_at - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.next_at = time.monotonic() + self.min_gap

            async def go():
                client = await self._connected()
                return await client(functions.account.CheckUsernameRequest(username))
            try:
                free = self._run(go(), timeout=30)
                self.checks += 1
            except errors.FloodWaitError as e:
                self.paused_until = time.time() + e.seconds + 5
                return "UNKNOWN", f"Telegram rate limit: wait {e.seconds}s"
            except errors.UsernameInvalidError:
                return "INVALID", "Telegram says invalid (reserved or banned)"
            except errors.UsernameOccupiedError:
                return "TAKEN", "Telegram says taken"
            except errors.UsernamePurchaseAvailableError:
                return "FRAGMENT", "for sale on Fragment"
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                return "UNKNOWN", f"Telegram check failed: {type(e).__name__}"
            return ("AVAILABLE", "confirmed by Telegram") if free else ("TAKEN", "Telegram says taken")

    def status(self) -> dict:
        wait = max(0, int(self.paused_until - time.time()))
        return {"authorized": self.authorized(max_age=0), "checks": self.checks,
                "rate_limited_seconds": wait, "last_error": self.last_error}
