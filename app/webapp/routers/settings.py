"""Settings, build identity and health."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from src import config, store
from src.secrets_env import PASSWORD_VAR, TOKEN_VAR, get_secret
from app.webapp.auth import is_pc_itself
from app.webapp.bind import resolve_bind
from app.webapp.routers.archive import archive_status
from app.webapp.build import BUILD_INFO

router = APIRouter()

# The bind this process actually started with, set by app.webapp.__main__;
# a process started some other way (a test harness) reports what config
# would give it now.
EFFECTIVE_BIND_ENV = "FOLDER_EXPLORER_BIND_STATE"


def effective_state() -> str:
    return os.environ.get(EFFECTIVE_BIND_ENV, "") or resolve_bind(config.load()).state


def _index_bytes() -> int:
    total = 0
    for path in store.index_dir().glob("*.sqlite3"):
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return total


def settings_body(pc: bool) -> dict[str, Any]:
    cfg = config.load()
    wanted = resolve_bind(cfg)
    running = effective_state()
    return {
        "config": cfg,
        "remote": {
            "configured": cfg["remote_access"],
            "running": running,
            "would_be": wanted.state,
            "restart_needed": running != wanted.state,
            "password_set": bool(get_secret(PASSWORD_VAR) and get_secret(TOKEN_VAR)),
        },
        "index_bytes": _index_bytes(),
        "archive": archive_status(cfg, pc),
    }


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/version")
def version() -> dict[str, str]:
    return BUILD_INFO.as_dict()


@router.get("/api/config")
def get_config(request: Request) -> dict[str, Any]:
    return settings_body(is_pc_itself(request))


@router.post("/api/config")
def post_config(request: Request, updates: dict[str, Any] = Body(...)) -> JSONResponse:
    try:
        config.save(updates)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    return JSONResponse(settings_body(is_pc_itself(request)))


