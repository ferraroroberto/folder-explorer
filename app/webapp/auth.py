"""Sign-in for callers on other devices: password -> bearer token, loopback exempt.

A request from this PC (loopback, or the PC's own tailnet or LAN address) always passes. A request from any other
device must carry ``Authorization: Bearer <token>``; the page gets the token
by posting the password to ``/api/login`` through the vendored sign-in sheet.
The page shells (``/`` and the document window ``/doc``), static assets, ``/healthz`` and ``/api/version`` stay open so
the sheet and the build line can render before sign-in.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.login_alert import alert_notifier, alert_text
from src.login_lockout import LockoutAlert, LoginLockout, retry_after_header
from src.notify import NotifierError
from src.secret_compare import secrets_equal
from src.secrets_env import PASSWORD_VAR, TOKEN_VAR, get_secret
from src.this_pc import is_local_address

logger = logging.getLogger(__name__)

LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
_OPEN_EXACT = frozenset({"/", "/doc", "/healthz", "/api/login", "/api/version", "/favicon.ico", "/manifest.webmanifest"})
_OPEN_PREFIXES = ("/static/",)

router = APIRouter()


def _client(scope_or_request: Any) -> str:
    client = scope_or_request.client if hasattr(scope_or_request, "client") else scope_or_request.get("client")
    if client is None:
        return ""
    return client.host if hasattr(client, "host") else str(client[0])


def is_pc_itself(scope_or_request: Any) -> bool:
    """True for a caller on this PC: it needs no token and is never locked out.

    The app binds the tailnet directly (no tunnel in front), so a socket whose
    peer is loopback or one of this machine's own addresses (the PC opening the
    app through its tailnet name) is this machine and nothing else.
    """
    host = _client(scope_or_request)
    return host in LOOPBACK or is_local_address(host)


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
        if is_pc_itself(scope):
            return True
        path = scope.get("path", "")
        if path in _OPEN_EXACT or path.startswith(_OPEN_PREFIXES):
            return True
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode("latin-1")
        return auth.lower().startswith("bearer ") and token_ok(auth[7:].strip())


def _alert_owner(alert: LockoutAlert) -> None:
    """Send the guessing alert from a thread, so the login never waits on Telegram."""
    notifier = alert_notifier()
    if notifier is None:
        return
    text = alert_text(alert)

    def send() -> None:
        try:
            notifier.send_text(text)
        except NotifierError as exc:
            logger.warning("⚠️ login alert not delivered: %s", type(exc).__name__)

    threading.Thread(target=send, daemon=True, name="login-alert").start()


def make_lockout(clock: Callable[[], float] = time.monotonic) -> LoginLockout:
    return LoginLockout(on_alert=_alert_owner, clock=clock)


_lockout = make_lockout()


@router.post("/api/login")
async def login(request: Request) -> Any:
    client = _client(request) or "?"
    password, token = get_secret(PASSWORD_VAR), get_secret(TOKEN_VAR)
    if not password or not token:
        logger.info("ℹ️ sign-in attempt from %s, but no password is set (scripts/set_password.py)", client)
        raise HTTPException(status_code=503, detail="password sign-in is not set up")
    exempt = is_pc_itself(request)
    wait = 0.0 if exempt else _lockout.retry_after(client)
    if wait > 0:
        return JSONResponse({"detail": "too many attempts"}, status_code=429, headers=retry_after_header(wait))
    try:
        body = await request.json()
    except ValueError:
        body = {}
    presented = str(body.get("password") or "") if isinstance(body, dict) else ""
    if not secrets_equal(presented, password):
        if not exempt:
            _lockout.record_failure(client)
        logger.warning("⚠️ failed sign-in from %s", client)
        raise HTTPException(status_code=401, detail="wrong password")
    _lockout.reset(client)
    logger.info("✅ sign-in from %s", client)
    return {"token": token}
