"""The cleanup shortlist: files marked in Largest for a later decision, kept across scans in state.sqlite3.

A file is marked by root, scan id and item id, never by a client path, and kept
by its names below its root, so the mark outlives a rescan. Each listing finds
every entry again in its root's current scan by those names: the file's id,
size and folder there, or ``file: null`` when that scan no longer has it.
Nothing here reads, opens, moves or deletes a file; the list is index lookups
and one table.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException

from app.webapp.state import POOL
from src import config, store
from src.roots import Root, resolve_roots

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/shortlist")


def _roots() -> dict[str, Root]:
    return {r.key: r for r in resolve_roots(config.load()["roots"])}


def _entry(roots: dict[str, Root], row: dict[str, Any]) -> dict[str, Any]:
    """One shortlisted file as the sheet lists it, looked up by its names in its root's current scan."""
    key, rel = row["root_key"], row["rel"]
    root = roots.get(key)
    out: dict[str, Any] = {"id": row["id"], "root": key, "root_name": root.name if root else key,
                           "name": rel[-1], "folder": rel[:-1], "size": row["size"], "disk": row["alloc"],
                           "scan_id": None, "folder_id": None, "file": None}
    scan = store.current_index(key) if root else None
    if scan is None:
        return out
    index, lock = POOL.get(store.index_dir() / scan["index_file"])
    with lock:
        folder_id = index.find_folder(rel[:-1])
        hit = index.find_file(folder_id, rel[-1]) if folder_id is not None else None
    out.update(scan_id=scan["id"], folder_id=folder_id)
    if hit is not None:
        out["file"] = {"id": hit["id"], "size": hit["size"], "disk": hit["disk"], "online": hit["online"]}
    return out


def _listing() -> dict[str, Any]:
    roots = _roots()
    items = [_entry(roots, row) for row in store.shortlist()]
    present = [i["file"] for i in items if i["file"] is not None]
    return {"items": items, "size": sum(f["size"] for f in present), "disk": sum(f["disk"] for f in present),
            "max": store.SHORTLIST_MAX}


def _int(value: object) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@router.get("")
def listing() -> dict[str, Any]:
    return _listing()


@router.post("")
def mark(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Add the file named by ``root``, ``scan_id`` and ``id`` (an id in that root's current scan)."""
    root_key, file_id = str(body.get("root")), _int(body.get("id"))
    scan = store.current_index(root_key) if root_key in _roots() else None
    if scan is None or file_id is None:
        raise HTTPException(status_code=404, detail="no such file")
    if body.get("scan_id") != scan["id"]:
        raise HTTPException(status_code=409, detail="the index changed since this page loaded: reload it")
    index, lock = POOL.get(store.index_dir() / scan["index_file"])
    try:
        with lock:
            record = index.file_record(file_id)
            names = [p["name"] for p in index.path(record["folder_id"])][1:]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="no such file") from exc
    try:
        store.shortlist_add(root_key, names + [record["name"]], int(record["size"]), int(record["disk"]))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("ℹ️ shortlist: marked a %s file", record["ext"] or "no-extension")
    return _listing()


@router.delete("/{entry_id}")
def unmark(entry_id: int) -> dict[str, Any]:
    if not store.shortlist_remove(entry_id):
        raise HTTPException(status_code=404, detail="not on the shortlist")
    return _listing()
