from __future__ import annotations

import secrets
import time
from urllib.parse import urlsplit

from .errors import AppError


class Sessions:
    def __init__(self, origin: str, lifetime: int = 43_200):
        parsed = urlsplit(origin)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
            raise ValueError("The local service must use an explicit 127.0.0.1 HTTP origin.")
        self.origin = origin
        self.host = parsed.netloc
        self.cookie_name = "aijs_" + str(parsed.port)
        self.lifetime = lifetime
        self.tokens: dict[str, float] = {}
        self.sessions: dict[str, tuple[str, float]] = {}

    def mint(self) -> str:
        self.tokens = {key: expiry for key, expiry in self.tokens.items() if expiry > time.monotonic()}
        token = secrets.token_urlsafe(32)
        self.tokens[token] = time.monotonic() + 90
        return token

    def bootstrap(self, token: str) -> tuple[str, str]:
        expiry = self.tokens.pop(token, 0)
        if expiry < time.monotonic():
            raise AppError("launch_token_expired", "This login link expired or was already used. Restart the backend and open the fresh link printed in its terminal.", 401)
        session_id, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.sessions = {key: item for key, item in self.sessions.items() if item[1] > time.monotonic()}
        if len(self.sessions) >= 16:
            self.sessions.pop(next(iter(self.sessions)))
        self.sessions[session_id] = csrf, time.monotonic() + self.lifetime
        return session_id, csrf

    def verify(self, session_id: str | None, csrf: str | None = None, *, mutation: bool = False) -> str:
        record = self.sessions.get(session_id or "")
        if not record or record[1] < time.monotonic():
            raise AppError("session_required", "Open the login link printed in the backend terminal. If it expired, restart the backend for a fresh link.", 401)
        if mutation and (not csrf or not secrets.compare_digest(record[0], csrf)):
            raise AppError("csrf_required", "Refresh the app before making this change.", 403)
        return record[0]
