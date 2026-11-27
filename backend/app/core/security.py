"""JWT verification, auth dependency, rate limiting and idempotency for the v1 API."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.core.config import AppSettings, get_settings
from app.core.exceptions import AppError, RateLimited

_bearer = HTTPBearer(auto_error=False)


class Unauthorized(AppError):
    status_code, code = 401, "unauthorized"


def decode_token(token: str, s: AppSettings) -> dict[str, Any]:
    try:
        # algorithms pinned: rejects alg=none / algorithm confusion
        payload = jwt.decode(token, s.SECRET_KEY, algorithms=[s.JWT_ALGORITHM])
    except JWTError:
        raise Unauthorized("invalid or expired token")
    if not payload.get("sub"):
        raise Unauthorized("invalid token payload")
    return payload


async def current_user_id(creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
                          s: AppSettings = Depends(get_settings)) -> str:
    if creds is None:
        raise Unauthorized("missing bearer token")
    return str(decode_token(creds.credentials, s)["sub"])


class SlidingWindowLimiter:
    """In-process limiter (single replica). Swap for a Redis-backed implementation to scale horizontally."""

    def __init__(self, limit: int, window_s: float = 60.0, clock=time.monotonic):
        self.limit, self.window, self._clock = limit, window_s, clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = self._clock()
        q = self._hits[key]
        while q and q[0] <= now - self.window:
            q.popleft()
        if len(q) >= self.limit:
            raise RateLimited("rate limit exceeded", details={"retry_after_s": round(self.window - (now - q[0]), 1)})
        q.append(now)


_limiter: SlidingWindowLimiter | None = None


def get_limiter() -> SlidingWindowLimiter:
    global _limiter
    if _limiter is None:
        _limiter = SlidingWindowLimiter(get_settings().RATE_LIMIT_PER_MINUTE)
    return _limiter


async def rate_limited_user(request: Request, user_id: str = Depends(current_user_id)) -> str:
    get_limiter().check(f"{user_id}:{request.url.path}")
    return user_id


class IdempotencyStore:
    """Replays the stored response for a repeated (user, key). Scoped per user so keys cannot collide across users."""

    def __init__(self):
        self._d: dict[tuple[str, str, str], Any] = {}

    def get(self, user_id: str, route: str, key: str | None):
        return self._d.get((user_id, route, key)) if key else None

    def put(self, user_id: str, route: str, key: str | None, value: Any) -> None:
        if key:
            self._d[(user_id, route, key)] = value


idempotency = IdempotencyStore()


def idempotency_key(x_idempotency_key: str | None = Header(default=None, max_length=128)) -> str | None:
    return x_idempotency_key
