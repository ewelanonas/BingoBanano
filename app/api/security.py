"""Operator authentication, CSRF, at rate limiting.

Dalawang paraan para ma-authorize ang paggawa ng pairing:

1. `X-API-Key` header — para sa server-to-server at kiosk backends.
2. Operator session cookie + katugmang `X-CSRF-Token` — para sa browser kiosk,
   para hindi kailanman mapunta sa JavaScript ang API key.

Ang session store at rate limiter ay in-memory: tama lang sa single-worker na
local dev. Palitan ng Redis bago mag-deploy ng maraming worker.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from app.config import Settings, get_settings

OPERATOR_COOKIE = "bb_operator"
CSRF_HEADER = "X-CSRF-Token"
API_KEY_HEADER = "X-API-Key"

_SESSION_TTL_SECONDS = 60 * 60 * 8


@dataclass(frozen=True, slots=True)
class OperatorSession:
    token: str
    csrf_token: str
    expires_at: float


class OperatorSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, OperatorSession] = {}

    def create(self) -> OperatorSession:
        self._purge()
        session = OperatorSession(
            token=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(24),
            expires_at=time.monotonic() + _SESSION_TTL_SECONDS,
        )
        self._sessions[session.token] = session
        return session

    def get(self, token: str | None) -> OperatorSession | None:
        if not token:
            return None
        session = self._sessions.get(token)
        if session is None:
            return None
        if session.expires_at <= time.monotonic():
            del self._sessions[token]
            return None
        return session

    def revoke(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)

    def _purge(self) -> None:
        now = time.monotonic()
        for token in [t for t, s in self._sessions.items() if s.expires_at <= now]:
            del self._sessions[token]


class SlidingWindowLimiter:
    """Simpleng per-key na sliding window. In-memory, per-process."""

    def __init__(self, window_seconds: int = 60) -> None:
        self._window = window_seconds
        self._hits: dict[str, list[float]] = {}

    def check(self, key: str, limit: int) -> bool:
        now = time.monotonic()
        cutoff = now - self._window
        hits = [t for t in self._hits.get(key, ()) if t > cutoff]
        if len(hits) >= limit:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True

    def reset(self) -> None:
        self._hits.clear()


_sessions = OperatorSessionStore()
_limiter = SlidingWindowLimiter()


def get_session_store() -> OperatorSessionStore:
    return _sessions


def get_limiter() -> SlidingWindowLimiter:
    return _limiter


def client_key(request: Request) -> str:
    """Rate-limit key. Sa likod ng reverse proxy, gamitin ang trusted
    `X-Forwarded-For` na pinu-populate ng proxy, hindi ang raw header."""
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(request: Request, *, bucket: str, limit: int) -> None:
    if not _limiter.check(f"{bucket}:{client_key(request)}", limit):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Try again in a moment.",
        )


def verify_api_key(candidate: str, settings: Settings) -> bool:
    return secrets.compare_digest(candidate, settings.operator_api_key)


def require_operator(request: Request) -> None:
    """I-raise ang 401/403 kung hindi authorized ang caller."""
    settings = get_settings()
    settings.require_secrets()

    api_key = request.headers.get(API_KEY_HEADER)
    if api_key and verify_api_key(api_key, settings):
        return

    session = _sessions.get(request.cookies.get(OPERATOR_COOKIE))
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Host authorization required.",
        )

    supplied_csrf = request.headers.get(CSRF_HEADER, "")
    if not secrets.compare_digest(supplied_csrf, session.csrf_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token mismatch.",
        )
