"""Browse one root's current index: tree, contents, largest files, percentile, types, summary.

Every endpoint takes ``root`` (a root key), ``folder`` (a folder id in that
root's current index, ``0`` is the root), ``basis`` (``size`` or ``disk``)
and an optional ``type``: a file extension, or ``-`` for files with none.
Each answer carries the ``scan_id`` it was read from; folder ids belong to
one scan, so the page starts over at the root when the scan id changes.

Tree and contents leave hidden items out unless ``explore.show_hidden`` is
on, and say which in ``hidden``. ``POST /api/open`` is the one action here:
on this PC only, it asks Windows to open a folder or a file looked up by its
id in the current scan, never by a path from the client.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request

from app.webapp.auth import is_pc_itself
from app.webapp.state import POOL, ScanChanged, scanned_item_path
from src import config, desktop, store
from src.index import BASES, Index
from src.roots import Root, resolve_roots

router = APIRouter(prefix="/api")

NO_EXT_PARAM = "-"


def _ext(type_: Optional[str]) -> Optional[str]:
    if type_ is None or type_ == "":
        return None
    return "" if type_ == NO_EXT_PARAM else type_.lower().lstrip(".")


def _basis(basis: str) -> str:
    if basis not in BASES:
        raise HTTPException(status_code=400, detail="basis must be size or disk")
    return basis


def _query(root: str, fn: Callable[[Index], dict[str, Any]]) -> dict[str, Any]:
    scan = store.current_index(root)
    if scan is None:
        raise HTTPException(status_code=404, detail="this root has no finished scan yet")
    index, lock = POOL.get(store.index_dir() / scan["index_file"])
    try:
        with lock:
            body = fn(index)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="no such folder in this scan") from exc
    body["scan_id"] = scan["id"]
    return body


def _show_hidden() -> bool:
    return bool(config.load()["explore"]["show_hidden"])


def _hidden(ix: Index, shown: bool) -> dict[str, bool]:
    """Whether hidden items are listed, and whether this index can tell them apart (older ones can't)."""
    return {"shown": shown, "known": ix.knows_hidden()}


@router.get("/tree")
def tree(root: str, folder: int = 0, basis: str = "size", type: Optional[str] = None) -> dict[str, Any]:
    ext = _ext(type)
    shown = _show_hidden()

    def run(ix: Index) -> dict[str, Any]:
        node = ix.node(ix.folder(folder), ext)
        return {"folder": node, "path": ix.path(folder), "hidden": _hidden(ix, shown),
                **ix.children(folder, _basis(basis), ext, shown)}

    return _query(root, run)


@router.get("/folder")
def folder_contents(request: Request, root: str, folder: int = 0, basis: str = "size", type: Optional[str] = None,
                    mode: str = Query("direct", pattern="^(direct|nested)$")) -> dict[str, Any]:
    ext = _ext(type)
    shown = _show_hidden()

    def run(ix: Index) -> dict[str, Any]:
        node = ix.node(ix.folder(folder), ext)
        if mode == "nested":
            files = ix.largest(folder, 100, _basis(basis), ext, shown)
            body: dict[str, Any] = {"items": [{"kind": "file", **f} for f in files], "total_items": node["files"]}
        else:
            body = ix.contents(folder, _basis(basis), ext, shown)
        return {"folder": node, "path": ix.path(folder), "mode": mode, "hidden": _hidden(ix, shown),
                "can_open": is_pc_itself(request), **body}

    return _query(root, run)


def scanned_path_or_http(roots: list[Root], body: dict[str, Any], kind: str) -> str:
    """The path on disk of the item a request body names by ``root``, ``scan_id`` and ``id``; 404 or 409 when it can't be resolved.

    The one place a PC-only open action turns ids into a path: an id from an older scan could name another
    item now, so the scan must be the root's current one.
    """
    item_id = body.get("id")
    if not isinstance(item_id, int) or isinstance(item_id, bool):
        raise HTTPException(status_code=404, detail="no such item")
    try:
        return scanned_item_path(roots, str(body.get("root")), body.get("scan_id"), kind, item_id)[2]
    except ScanChanged as exc:
        raise HTTPException(status_code=409, detail="the index changed since this page loaded: reload it") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="no such item") from exc


@router.post("/open")
def open_item(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, str]:
    """Ask Windows to open a folder (``explore``) or a file (``open``, ``edit``, ``reveal``) on this PC."""
    started = time.perf_counter()
    if not is_pc_itself(request):
        raise HTTPException(status_code=403, detail="this works on the PC only")
    kind, verb = body.get("kind"), body.get("verb")
    verbs = desktop.FOLDER_VERBS if kind == "folder" else desktop.FILE_VERBS if kind == "file" else ()
    if verb not in verbs:
        raise HTTPException(status_code=400, detail="unknown action for a " + str(kind))
    path = scanned_path_or_http(resolve_roots(config.load()["roots"]), body, str(kind))
    try:
        desktop.ensure_there(path, kind == "folder", started)
        desktop.open_item(path, str(verb), started)
    except desktop.GoneError as exc:
        raise HTTPException(status_code=410, detail="it is no longer there") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=422, detail="Windows can't " + str(verb) + " this file: " + str(exc)) from exc
    return {"status": "opened"}


@router.get("/top")
def top(root: str, folder: int = 0, basis: str = "size", type: Optional[str] = None,
        n: int = Query(10, ge=1, le=100)) -> dict[str, Any]:
    ext = _ext(type)
    return _query(root, lambda ix: {"folder": ix.node(ix.folder(folder), ext),
                                    "files": ix.largest(folder, n, _basis(basis), ext)})


@router.get("/percentile")
def percentile(root: str, folder: int = 0, basis: str = "size", type: Optional[str] = None,
               p: float = Query(1, gt=0, le=50)) -> dict[str, Any]:
    ext = _ext(type)
    return _query(root, lambda ix: ix.percentile(folder, p, _basis(basis), ext))


@router.get("/types")
def types(root: str, folder: int = 0, basis: str = "size") -> dict[str, Any]:
    return _query(root, lambda ix: {"folder": ix.node(ix.folder(folder), None), **ix.types(folder, _basis(basis))})


@router.get("/summary")
def summary(root: str, folder: int = 0, type: Optional[str] = None) -> dict[str, Any]:
    ext = _ext(type)
    return _query(root, lambda ix: {"totals": ix.totals(folder, ext)})
