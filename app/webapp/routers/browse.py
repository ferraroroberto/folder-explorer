"""Browse one root's current index: tree, contents, largest files, percentile, types, summary.

Every endpoint takes ``root`` (a root key), ``folder`` (a folder id in that
root's current index, ``0`` is the root), ``basis`` (``size`` or ``disk``)
and an optional ``type``: a file extension, or ``-`` for files with none.
Each answer carries the ``scan_id`` it was read from; folder ids belong to
one scan, so the page starts over at the root when the scan id changes.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException, Query

from src import store
from src.index import BASES, Index
from app.webapp.state import POOL

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


@router.get("/tree")
def tree(root: str, folder: int = 0, basis: str = "size", type: Optional[str] = None) -> dict[str, Any]:
    ext = _ext(type)

    def run(ix: Index) -> dict[str, Any]:
        node = ix.node(ix.folder(folder), ext)
        return {"folder": node, "path": ix.path(folder), **ix.children(folder, _basis(basis), ext)}

    return _query(root, run)


@router.get("/folder")
def folder_contents(root: str, folder: int = 0, basis: str = "size", type: Optional[str] = None,
                    mode: str = Query("direct", pattern="^(direct|nested)$")) -> dict[str, Any]:
    ext = _ext(type)

    def run(ix: Index) -> dict[str, Any]:
        node = ix.node(ix.folder(folder), ext)
        if mode == "nested":
            files = ix.largest(folder, 100, _basis(basis), ext)
            body: dict[str, Any] = {"items": [{"kind": "file", **f} for f in files], "total_items": node["files"]}
        else:
            body = ix.contents(folder, _basis(basis), ext)
        return {"folder": node, "path": ix.path(folder), "mode": mode, **body}

    return _query(root, run)


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
