"""Sign-in for callers on other devices: password -> bearer token, loopback exempt.

A request from this PC (loopback) always passes. A request from any other
device must carry ``Authorization: Bearer <token>``; the page gets the token
by posting the password to ``/api/login`` through the vendored sign-in sheet.
The page shell, static assets, ``/healthz`` and ``/api/version`` stay open so
the sheet and the build line can render before sign-in.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.secret_compare import secrets_equal
from src.secrets_env import PASSWORD_VAR, TOKEN_VAR, get_secret

logger = logging.getLogger(__name__)

LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
_OPEN_EXACT = frozenset({"/", "/healthz", "/api/login", "/api/version", "/favicon.ico", "/manifest.webmanifest"})
_OPEN_PREFIXES = ("/static/",)

# Failed sign-ins allowed per client before a cool-down, and its length.
_MAX_FAILURES = 5
_LOCKOUT_SECONDS = 300

router = APIRouter()


def _client(scope_or_request: Any) -> str:
    client = scope_or_request.client if hasattr(scope_or_request, "client") else scope_or_request.get("client")
    if client is None:
        return ""
    return client.host if hasattr(client, "host") else str(client[0])


def token_ok(presented: str) -> bool:
    return secrets_equal(presented, get_secret(TOKEN_VAR))


class SignInMiddleware:
    """Plain ASGI gate (not ``BaseHTTPMiddleware``, which re-chunks every body)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self._allowed(scope):
            await self.app(scope, receive, send)
            return
        response = JSONResponse({"detail": "sign in required"}, status_code=401,
                                headers={"WWW-Authenticate": 'Bearer realm="folder-explorer"'})
        await response(scope, receive, send)

    def _allowed(self, scope: Scope) -> bool:
        if _client(scope) in LOOPBACK:
            return True
        path = scope.get("path", "")
        if path in _OPEN_EXACT or path.startswith(_OPEN_PREFIXES):
            return True
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode("latin-1")
        return auth.lower().startswith("bearer ") and token_ok(auth[7:].strip())


class _Throttle:
    """Counts failed sign-ins per client address."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fails: dict[str, list[float]] = {}

    def retry_after(self, client: str) -> int:
        with self._lock:
            recent = [t for t in self._fails.get(client, []) if time.time() - t < _LOCKOUT_SECONDS]
            self._fails[client] = recent
            if len(recent) < _MAX_FAILURES:
                return 0
            return int(_LOCKOUT_SECONDS - (time.time() - recent[0])) + 1

    def fail(self, client: str) -> None:
        with self._lock:
            self._fails.setdefault(client, []).append(time.time())

    def clear(self, client: str) -> None:
        with self._lock:
            self._fails.pop(client, None)


_throttle = _Throttle()


@router.post("/api/login")
async def login(request: Request) -> Any:
    client = _client(request) or "?"
    password, token = get_secret(PASSWORD_VAR), get_secret(TOKEN_VAR)
    if not password or not token:
        logger.info("ℹ️ sign-in attempt from %s, but no password is set (scripts/set_password.py)", client)
        raise HTTPException(status_code=503, detail="password sign-in is not set up")
    wait = _throttle.retry_after(client)
    if wait:
        return JSONResponse({"detail": "too many attempts"}, status_code=429, headers={"Retry-After": str(wait)})
    try:
        body = await request.json()
    except ValueError:
        body = {}
    presented = str(body.get("password") or "") if isinstance(body, dict) else ""
    if not secrets_equal(presented, password):
        _throttle.fail(client)
        logger.warning("⚠️ failed sign-in from %s", client)
        raise HTTPException(status_code=401, detail="wrong password")
    _throttle.clear(client)
    logger.info("✅ sign-in from %s", client)
    return {"token": token}
