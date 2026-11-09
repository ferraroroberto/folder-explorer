"""The versioned search API for other apps: ``/api/v1/search/{status,folders,emails}``, shapes in ``docs/service-api.md``.

Thin over the same searches the Search tab runs; ``src.search_api`` owns the
states and the hit shapes. A caller on this PC needs no sign-in (the
loopback exemption in ``app.webapp.auth``); any other device needs the token.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import ExitStack
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.webapp.state import EMAILS, POOL
from src import config, folder_search, scan_runner, search_api, store
from src.email import indexer
from src.email.search import SORTS
from src.email.settings import email_part
from src.roots import Root, resolve_roots
from src.scan_lock import is_locked
from src.search_api import DEFAULT_LIMIT, MAX_LIMIT, OK, doc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/search")

_SORT_PATTERN = "^(" + "|".join(SORTS) + ")$"


def _answer(body: dict[str, Any]) -> JSONResponse:
    return JSONResponse(body, status_code=search_api.HTTP_STATUS[body["state"]])


def _scanning() -> bool:
    return is_locked(scan_runner.lock().path)


def _roots(cfg: dict[str, Any]) -> tuple[list[Root], list[tuple[Root, dict[str, Any]]]]:
    """The roots set up, and those with a finished scan paired with their current scan."""
    roots = resolve_roots(cfg["roots"])
    return roots, [(r, scan) for r in roots if (scan := store.current_index(r.key)) is not None]


@router.get("/status")
def status() -> dict[str, Any]:
    """Each part's state, so a caller can tell "not set up" and "building" from "no match"."""
    cfg = config.load()
    roots, scanned = _roots(cfg)
    scanning = _scanning()
    f_state, f_reason = search_api.folders_state(len(roots), len(scanned), scanning)
    part = email_part(cfg, roots)
    e_state, e_reason = search_api.emails_state(part, indexer.is_running())
    count = 0
    if e_state == OK and part.index_path is not None:
        try:
            index = EMAILS.get(part.index_path)
            with index.lock:
                count = index.count()
        except (OSError, ValueError, sqlite3.Error) as exc:
            logger.warning("⚠️ v1 search status: the email index could not be read: %s", exc)
            e_state, e_reason = search_api.ERROR, "the email index could not be read: " + str(exc)
    return doc(OK, folders={"state": f_state, "reason": f_reason, "roots": len(roots), "scanned": len(scanned),
                            "scanning": scanning},
               emails={"state": e_state, "reason": e_reason, "count": count})


@router.get("/folders")
def folders(q: str = "", limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
            within: list[str] = Query(default=[])) -> JSONResponse:
    """Folders whose full path holds every word, best first, optionally kept to the ``within`` folders."""
    empty: dict[str, Any] = {"query": q, "results": [], "total": 0, "truncated": False, "needs_rescan": [],
                             "missing": [], "page": search_api.page_link("folders", q)}
    cfg = config.load()
    roots, scanned = _roots(cfg)
    state, reason = search_api.folders_state(len(roots), len(scanned), _scanning() if not scanned else False)
    if state != OK:
        return _answer(doc(state, reason, **empty))
    opts = cfg["folder_search"]
    try:
        entries = [(root, POOL.get(store.index_dir() / scan["index_file"])) for root, scan in scanned]
        with ExitStack() as stack:
            for _, (_, lock) in entries:
                stack.enter_context(lock)
            found = folder_search.search([(root, ix) for root, (ix, _) in entries], q, opts["prune_people"], 0, limit,
                                         onedrive_prevails=opts["onedrive_prevails"], within=within)
    except (OSError, sqlite3.Error) as exc:
        logger.warning("⚠️ v1 folder search failed: %s", exc)
        return _answer(doc(search_api.ERROR, "a folder index could not be read: " + str(exc), **empty))
    return _answer(doc(OK, **{**empty, "results": [search_api.folder_hit(h) for h in found["results"]],
                              "total": found["total"], "truncated": found["truncated"],
                              "needs_rescan": found["needs_rescan"], "missing": found["missing"]}))


@router.get("/emails")
def emails(q: str = "", sort: str = Query("relevance", pattern=_SORT_PATTERN), offset: int = Query(0, ge=0),
           limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT), facets: bool = False) -> JSONResponse:
    """One page of archived mails for the query (newest first when it is empty), with the match marks."""
    empty: dict[str, Any] = {"query": q, "hits": [], "total": 0, "notes": [], "facets": None,
                             "marks": search_api.MARKS, "page": search_api.page_link("emails", q)}
    cfg = config.load()
    part = email_part(cfg, resolve_roots(cfg["roots"]))
    state, reason = search_api.emails_state(part, indexer.is_running())
    if state != OK or part.index_path is None:  # a ready part always names its index file
        return _answer(doc(state, reason, **empty))
    try:
        index = EMAILS.get(part.index_path)
        with index.lock:
            found = index.search(q, sort, offset, limit, facets=facets)
    except (OSError, ValueError, sqlite3.Error) as exc:
        logger.warning("⚠️ v1 email search failed: %s", exc)
        return _answer(doc(search_api.ERROR, "the email index could not be read: " + str(exc), **empty))
    return _answer(doc(OK, **{**empty, "hits": [search_api.email_hit(h) for h in found["hits"]],
                              "total": found["total"], "notes": found["notes"], "facets": found["facets"]}))
