"""Search: folders over every root's index, emails over the email index, and one mail's preview.

Reads only, with narrow exceptions behind their own checks: "Show all"
reads a mail's ``.msg`` (looked up by row id, never a client path, and only
inside the email folders) in a child process, and the PC-only actions open a
mail in Outlook, its live copy (Find in mailbox, through the Outlook bridge's
child) or a folder in File Explorer for loopback callers.
"""

from __future__ import annotations

import os
import time
from contextlib import ExitStack
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.webapp.auth import is_pc_itself
from app.webapp.routers.archive import SHOW_TIMEOUT_S, archive_status, bridge
from app.webapp.state import EMAILS, POOL
from src import config, folder_search, store
from src.desktop import GoneError, ensure_there, open_in_explorer, open_in_outlook, outlook_exe
from src.email.body import ERR_NOT_SET_UP, ERR_OUTSIDE, failed, fetch_body
from src.email.search import PAGE, SORTS, EmailIndex
from src.email.settings import EmailPart, email_part, is_under, under_any
from src.roots import Root, resolve_roots

router = APIRouter(prefix="/api")

# HTTP status per Show all failure, so the page can tell them apart.
_BODY_STATUS = {"timeout": 504, "gone": 410, "parse": 422, "read": 502, ERR_NOT_SET_UP: 503, ERR_OUTSIDE: 403}


def _roots() -> list[Root]:
    return resolve_roots(config.load()["roots"])


def _part() -> EmailPart:
    return email_part(config.load(), _roots())


def _emails() -> EmailIndex:
    part = _part()
    if not part.ready or part.index_path is None:
        raise HTTPException(status_code=503, detail="Email search isn't set up here: " + str(part.problem))
    try:
        return EMAILS.get(part.index_path)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Email search isn't set up here: " + str(exc)) from exc


def _scanned(roots: list[Root]) -> list[tuple[Root, dict[str, Any]]]:
    out = []
    for root in roots:
        scan = store.current_index(root.key)
        if scan is not None:
            out.append((root, scan))
    return out


@router.get("/search/status")
def status(request: Request) -> dict[str, Any]:
    """Which search parts run here, and why a part that doesn't is off."""
    cfg = config.load()
    roots = _roots()
    scanned = _scanned(roots)
    part = email_part(cfg, roots)
    emails: dict[str, Any] = {"ready": part.ready, "reason": part.problem, "reader": part.reader_problem, "count": 0,
                              "fields": []}
    if part.ready and part.index_path is not None:
        try:
            index = EMAILS.get(part.index_path)
            with index.lock:
                emails["count"] = index.count()
            emails["fields"] = sorted(index.columns)
        except (OSError, ValueError) as exc:
            emails.update(ready=False, reason="the email index could not be opened: " + str(exc))
    pc = is_pc_itself(request)
    outlook = outlook_exe() is not None
    return {
        "pc": pc,
        "folders": {"ready": bool(scanned), "reason": None if scanned else "no root has a finished scan yet"},
        "emails": emails,
        "outlook": {"ready": pc and outlook, "reason": None if outlook else "Outlook (classic) isn't installed on this PC"},
        "archive": archive_status(cfg, pc),
        "folder_search": cfg["folder_search"],
    }


@router.get("/search/folders")
def search_folders(q: str = "", prune: Optional[bool] = None, skip: Optional[int] = Query(None, ge=0, le=config.MAX_SKIP_LEVELS),
                   onedrive: Optional[bool] = None) -> dict[str, Any]:
    opts = config.load()["folder_search"]
    entries = [(root, POOL.get(store.index_dir() / scan["index_file"])) for root, scan in _scanned(_roots())]
    with ExitStack() as stack:
        for _, (_, lock) in entries:
            stack.enter_context(lock)
        return folder_search.search([(root, ix) for root, (ix, _) in entries], q,
                                    opts["prune_people"] if prune is None else prune,
                                    opts["skip_levels"] if skip is None else skip,
                                    onedrive_prevails=opts["onedrive_prevails"] if onedrive is None else onedrive)


@router.get("/search/emails")
def search_emails(q: str = "", sort: str = "relevance", offset: int = Query(0, ge=0),
                  limit: int = Query(PAGE, ge=1, le=200)) -> dict[str, Any]:
    if sort not in SORTS:
        raise HTTPException(status_code=400, detail="sort must be relevance or newest")
    index = _emails()
    with index.lock:
        return index.search(q, sort, offset, limit)


def _locate(path: str) -> Optional[dict[str, Any]]:
    """Where a file sits in the scanned tree: its root, folder id and indexed size and cloud state."""
    for root in sorted(_roots(), key=lambda r: len(r.path), reverse=True):
        if not is_under(path, root.path):
            continue
        scan = store.current_index(root.key)
        if scan is None:
            return None
        names = [p for p in os.path.relpath(path, root.path).split(os.sep) if p and p != "."]
        index, lock = POOL.get(store.index_dir() / scan["index_file"])
        with lock:
            folder = index.find_folder(names[:-1])
            found = index.find_file(folder, names[-1]) if folder is not None and names else None
        if folder is None:
            return None
        return {"root": root.key, "folder": folder, "scan_id": scan["id"], "file": found}
    return None


@router.get("/emails/{row_id}")
def email(row_id: int, request: Request, q: str = "") -> dict[str, Any]:
    index = _emails()
    try:
        with index.lock:
            body = index.email(row_id, q)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="no such email in the index") from exc
    part = _part()
    body["location"] = _locate(body["path"])
    body["readable"] = part.reader_problem is None and under_any(body["path"], part.roots)
    body["reader_problem"] = part.reader_problem if part.reader_problem else (
        None if body["readable"] else "this mail is outside the email folders")
    body["can_open"] = is_pc_itself(request) and outlook_exe() is not None
    return body


@router.get("/emails/{row_id}/body")
def email_body(row_id: int) -> JSONResponse:
    index = _emails()
    try:
        with index.lock:
            path = index.path_of(row_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="no such email in the index") from exc
    part = _part()
    if part.reader_problem is not None:
        result = failed(ERR_NOT_SET_UP) if "extract-msg" in part.reader_problem else failed(ERR_OUTSIDE)
    elif not under_any(path, part.roots):
        result = failed(ERR_OUTSIDE)
    else:
        result = fetch_body(path)
    if not result.ok:
        return JSONResponse({"error": result.error, "detail": result.message}, status_code=_BODY_STATUS.get(result.error or "", 502))
    return JSONResponse({"text": result.text, "html": result.html})


def _pc_only(request: Request) -> None:
    if not is_pc_itself(request):
        raise HTTPException(status_code=403, detail="this works on the PC only")


@router.post("/emails/{row_id}/open")
def open_email(row_id: int, request: Request) -> dict[str, str]:
    _pc_only(request)
    index = _emails()
    try:
        with index.lock:
            path = index.path_of(row_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="no such email in the index") from exc
    if not os.path.isfile(path):
        raise HTTPException(status_code=410, detail="the file is no longer there")
    try:
        open_in_outlook(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "opened"}


@router.post("/emails/{row_id}/find")
def find_in_mailbox(row_id: int, request: Request) -> dict[str, Any]:
    """Find in mailbox: open the live copy of an archived mail in Outlook, looked up by its Message-ID."""
    _pc_only(request)
    index = _emails()
    try:
        with index.lock:
            message_id = index.email(row_id)["message_id"]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="no such email in the index") from exc
    if not message_id:
        raise HTTPException(status_code=422, detail="this mail has no Message-ID to look it up by")
    return bridge(["show", "--message-id=" + message_id], SHOW_TIMEOUT_S)


@router.post("/folders/open")
def open_folder(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, str]:
    started = time.perf_counter()
    _pc_only(request)
    root = next((r for r in _roots() if r.key == body.get("root")), None)
    scan = store.current_index(root.key) if root else None
    if root is None or scan is None or not isinstance(body.get("id"), int):
        raise HTTPException(status_code=404, detail="no such folder")
    index, lock = POOL.get(store.index_dir() / scan["index_file"])
    with lock:
        names = [p["name"] for p in index.path(body["id"])][1:]
    path = folder_search.full_path(root, names)
    try:
        ensure_there(path, True, started)
        open_in_explorer(path, started)
    except GoneError as exc:
        raise HTTPException(status_code=410, detail="the folder is no longer there") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=422, detail="Windows can't open this folder: " + str(exc)) from exc
    return {"status": "opened"}


@router.get("/searches")
def list_searches() -> dict[str, Any]:
    return {"searches": store.saved_searches()}


@router.post("/searches")
def add_search(body: dict[str, Any] = Body(...)) -> JSONResponse:
    try:
        saved = store.save_search(str(body.get("mode", "")), str(body.get("name", "")), str(body.get("query", "")))
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    return JSONResponse(saved)


@router.delete("/searches/{search_id}")
def remove_search(search_id: int) -> dict[str, bool]:
    if not store.delete_search(search_id):
        raise HTTPException(status_code=404, detail="no such saved search")
    return {"deleted": True}
