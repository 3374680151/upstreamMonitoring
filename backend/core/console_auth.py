"""Process-local console authentication state for the FastAPI app."""

from __future__ import annotations

import hmac
import os
import secrets
import threading
import time


PUBLIC_API_PATHS = frozenset(
    {"/api/auth/login", "/api/auth/logout", "/api/auth/status"}
)


def configured_password() -> str:
    return (os.getenv("CONSOLE_PASSWORD") or "").strip()


def enabled() -> bool:
    return bool(configured_password())


def password_matches(candidate: str) -> bool:
    expected = configured_password()
    supplied = (candidate or "").strip()
    return bool(expected and supplied) and hmac.compare_digest(
        supplied.encode("utf-8"), expected.encode("utf-8")
    )


def session_ttl_seconds() -> int:
    try:
        return max(300, int(os.getenv("CONSOLE_SESSION_TTL") or "604800"))
    except ValueError:
        return 604800


class SessionStore:
    """Thread-safe session store for the configured single FastAPI worker."""

    def __init__(self) -> None:
        self._values: dict[str, float] = {}
        self._lock = threading.RLock()

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._values[token] = now + session_ttl_seconds()
            for stale, expiry in tuple(self._values.items()):
                if expiry < now:
                    self._values.pop(stale, None)
        return token

    def valid(self, token: str) -> bool:
        token = (token or "").strip()
        if not token:
            return False
        with self._lock:
            expiry = self._values.get(token)
            if expiry is None:
                return False
            if expiry < time.time():
                self._values.pop(token, None)
                return False
            return True

    def drop(self, token: str) -> None:
        with self._lock:
            self._values.pop((token or "").strip(), None)


sessions = SessionStore()
