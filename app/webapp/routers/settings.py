"""Settings, build identity and health."""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

from app.webapp.auth import is_pc_itself
from app.webapp.bind import resolve_bind
from app.webapp.build import BUILD_INFO
from app.webapp.routers.archive import archive_status
from src import config, file_assoc, folder_lists, password, store
from src.secrets_env import PASSWORD_VAR, TOKEN_VAR, get_secret
from src.snap import settings as snap_settings
from src.snap.folder import open_save_folder

logger = logging.getLogger(__name__)

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
        # Where captures go once ~ and %VARS% expand, for the Screenshot rows; null in config.json means the Pictures folder.
        "snap_folder": {"path": str(snap_settings.resolve(cfg["snap"]).folder), "can_open": pc},
        "remote": {
            "configured": cfg["remote_access"],
            "running": running,
            "would_be": wanted.state,
            "restart_needed": running != wanted.state,
            "password_set": bool(password.is_configured(legacy=get_secret(PASSWORD_VAR)) and get_secret(TOKEN_VAR)),
        },
        "index_bytes": _index_bytes(),
        "archive": archive_status(cfg, pc),
        "config_file": {"exists": config.config_path().is_file()},
        "md_assoc": {"state": file_assoc.state() if pc else "pc_only"},
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
        folder_lists.save(updates)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    return JSONResponse(settings_body(is_pc_itself(request)))


@router.post("/api/config/create")
def create_config(request: Request) -> JSONResponse:
    """First run: config.json from config.example.json. A file that exists is left as it is (``created`` false)."""
    try:
        created = config.create_from_example()
    except OSError as exc:
        logger.warning("⚠️ config.json not created: %s", exc)
        return JSONResponse({"detail": "config.json could not be created: " + (exc.strerror or "the file system refused")},
                            status_code=500)
    return JSONResponse({**settings_body(is_pc_itself(request)), "created": created})


@router.post("/api/snap/open-folder")
def post_snap_open_folder(request: Request) -> dict[str, str]:
    """Settings' Open folder for the screenshot save folder: File Explorer on this PC only, the folder named by config, never by the page."""
    if not is_pc_itself(request):
        raise HTTPException(status_code=403, detail="this works on the PC only")
    try:
        open_save_folder(snap_settings.resolve(config.load()["snap"]).folder)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        logger.warning("⚠️ snap: could not open the save folder: %s", exc)
        raise HTTPException(status_code=422, detail="The save folder could not be opened: " + str(exc)) from exc
    return {"status": "opened"}


@router.post("/api/md-assoc")
def post_md_assoc(request: Request, body: dict[str, Any] = Body(...)) -> JSONResponse:
    """Settings' "Associate Markdown files with this app": ``{"on": bool}``, for this PC only; answers the state read back."""
    if not is_pc_itself(request):
        return JSONResponse({"detail": "Only this PC can change its file associations"}, status_code=403)
    on = body.get("on")
    if not isinstance(on, bool):
        return JSONResponse({"detail": "on must be true or false"}, status_code=400)
    try:
        return JSONResponse({"state": file_assoc.set_on(on)})
    except file_assoc.NotAllowed as exc:
        return JSONResponse({"detail": str(exc)}, status_code=409)
    except OSError as exc:
        logger.warning("⚠️ file association not changed: %s", exc)
        return JSONResponse({"detail": "Windows refused the change: " + (exc.strerror or str(exc)), "state": file_assoc.state()},
                            status_code=500)
