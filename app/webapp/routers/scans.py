"""Scan roots, their last results and live progress, and the Rescan action."""

from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from app.webapp.state import refresh_pool
from src import config, scan_runner, store
from src.roots import Root, resolve_roots
from src.run_state import RunState, derive_run_state
from src.scan_lock import ScanBusy

router = APIRouter(prefix="/api")

_RESULT_FIELDS = ("id", "started_at", "finished_at", "folders", "files", "size", "alloc",
                  "online_files", "online_size", "skipped_links", "errors", "trigger")


def _pick(row: Optional[dict[str, Any]], fields: tuple[str, ...]) -> Optional[dict[str, Any]]:
    return {k: row[k] for k in fields} if row else None


def describe_root(root: Root, lock_held: Optional[bool]) -> dict[str, Any]:
    current = store.current_index(root.key)
    latest = store.latest_scan(root.key)
    derived = derive_run_state(latest["status"] if latest else None, latest["message"] if latest else None, lock_held)
    running = latest if derived.state is RunState.RUNNING else None
    # The last attempt failed or stopped, so the index shown is older than it.
    failed = latest if derived.state in (RunState.FAILED, RunState.INTERRUPTED) else None
    return {
        **root.as_dict(),
        "exists": os.path.isdir(root.path),
        "current": _pick(current, _RESULT_FIELDS),
        "running": _pick(running, _RESULT_FIELDS),
        "failed": {"status": derived.state.value, "message": derived.reason, "at": failed["finished_at"]} if failed else None,
        **derived.as_dict(),
    }


@router.get("/roots")
def roots() -> dict[str, Any]:
    resolved = resolve_roots(config.load()["roots"])
    held = scan_runner.settle_if_idle()
    scan = scan_runner.scan_state(held)
    refresh_pool(resolved)
    return {"roots": [describe_root(r, held) for r in resolved], "scanning": scan.running, **scan.as_dict()}


@router.post("/scan")
def start_scan(body: Optional[dict[str, Any]] = Body(default=None)) -> JSONResponse:
    keys = (body or {}).get("roots") or None
    if keys is not None and not (isinstance(keys, list) and all(isinstance(k, str) for k in keys)):
        return JSONResponse({"detail": "roots must be a list of root keys"}, status_code=400)
    known = {r.key for r in resolve_roots(config.load()["roots"])}
    if keys and not set(keys) <= known:
        return JSONResponse({"detail": "unknown root"}, status_code=400)
    try:
        scan_runner.start_background(keys, "app", on_done=lambda: refresh_pool(resolve_roots(config.load()["roots"])))
    except ScanBusy:
        return JSONResponse({"detail": "a scan is already running"}, status_code=409)
    return JSONResponse({"started": keys or sorted(known)}, status_code=202)


@router.get("/scan")
def scan_progress() -> dict[str, Any]:
    scan, rows = scan_runner.scan_activity()
    return {"scanning": scan.running, "running": [_pick(r, _RESULT_FIELDS + ("root_key",)) for r in rows], **scan.as_dict()}
