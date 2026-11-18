"""The versioned window API for other apps: ``GET /api/v1/explorer/folder``, shape in ``docs/service-api.md``.

Answers the folder of the foremost File Explorer window to a caller on this PC,
whatever the folder is: no email-folder restriction, unlike the Archive sheet's
route. The read itself is ``src.explorer_window.get_current_explorer_folder``,
the one place that asks Windows; nothing here reads a file.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.webapp.auth import is_pc_itself
from src.explorer_window import ExplorerUnavailableError, get_current_explorer_folder

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/explorer")

SCHEMA_VERSION = 1
NO_EXPLORER_FOLDER = "no_explorer_folder"
EXPLORER_UNAVAILABLE = "explorer_unavailable"
NOT_THIS_PC = "not_this_pc"


def _error(code: str, reason: str, status: int) -> JSONResponse:
    body: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "state": "error", "code": code, "reason": reason}
    return JSONResponse(body, status_code=status)


@router.get("/folder")
def folder(request: Request) -> JSONResponse:
    """The folder shown in the foremost File Explorer window on this PC."""
    if not is_pc_itself(request):
        return _error(NOT_THIS_PC, "the open File Explorer folder can only be read on the PC itself", 403)
    try:
        opened = get_current_explorer_folder()
    except ExplorerUnavailableError as exc:
        return _error(EXPLORER_UNAVAILABLE, "Windows could not be asked which folders are open: %s" % exc, 502)
    if not opened:
        return _error(NO_EXPLORER_FOLDER, "no File Explorer window is open on a folder", 404)
    logger.info("ℹ️ explorer v1: answered the front window's folder")
    return JSONResponse({"schema_version": SCHEMA_VERSION, "state": "ok", "folder_path": opened})
