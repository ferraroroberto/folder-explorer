"""Process-wide webapp state: the open-index pool, the open email index, their housekeeping, and the shared folder search."""

from __future__ import annotations

import logging
import os
from contextlib import ExitStack
from typing import Any, Optional

from src import folder_search, store
from src.docs import listing
from src.email.search import EmailIndexHolder
from src.email.settings import is_under, under_any
from src.folder_search import item_path
from src.index import IndexPool
from src.roots import Root, resolve_roots

logger = logging.getLogger(__name__)

# Folders the Archive sheet lists at once, for a search and for a folder stepped into; more words narrow the list.
SHEET_FOLDER_ROWS = 8

POOL = IndexPool()
EMAILS = EmailIndexHolder()


def current_files(roots: list[Root]) -> set[str]:
    files = set()
    for root in roots:
        scan = store.current_index(root.key)
        if scan is not None:
            files.add(scan["index_file"])
    return files


def refresh_pool(roots: list[Root]) -> None:
    """Close indexes a newer scan replaced, then delete their files.

    A scan run by the CLI in another process cannot delete an index this
    process still has open; this catches up on the next page load.
    """
    POOL.retire(current_files(roots))
    removed = store.prune_indexes()
    if removed:
        logger.info("ℹ️ removed %d replaced index file(s)", len(removed))


def locate_folder(roots: list[Root], path: str) -> Optional[dict[str, Any]]:
    """A folder's place in its root's current scan: ``{root, scan_id, id}``, so ``/api/open`` can name it by id.

    The innermost root holding ``path`` decides; ``None`` when no scanned root
    holds it or the scan has no such folder (made after the scan ran).
    """
    for root in sorted(roots, key=lambda r: len(r.path), reverse=True):
        if not is_under(path, root.path):
            continue
        scan = store.current_index(root.key)
        if scan is None:
            return None
        names = [p for p in os.path.relpath(path, root.path).split(os.sep) if p and p != "."]
        index, lock = POOL.get(store.index_dir() / scan["index_file"])
        with lock:
            folder = index.find_folder(names)
        return None if folder is None else {"root": root.key, "scan_id": scan["id"], "id": folder}
    return None


def _open_scans(cfg: dict[str, Any]) -> list[tuple[Root, dict[str, Any], tuple[Any, Any]]]:
    """Each configured root with a finished scan, its scan row and its pooled index and lock."""
    out = []
    for root in resolve_roots(cfg["roots"]):
        scan = store.current_index(root.key)
        if scan is not None:
            out.append((root, scan, POOL.get(store.index_dir() / scan["index_file"])))
    return out


def folder_hits(cfg: dict[str, Any], q: str, prune: Optional[bool] = None, skip: Optional[int] = None,
                onedrive: Optional[bool] = None) -> dict[str, Any]:
    """The folder search with the settings' options: ``GET /api/search/folders``, the archive window and sheet all ask here.

    ``prune``, ``skip`` and ``onedrive`` override ``folder_search``'s own choices for one search.
    """
    opts = cfg["folder_search"]
    entries = _open_scans(cfg)
    with ExitStack() as stack:
        for _, _, (_, lock) in entries:
            stack.enter_context(lock)
        found = folder_search.search([(root, ix) for root, _, (ix, _) in entries], q,
                                     opts["prune_people"] if prune is None else prune,
                                     opts["skip_levels"] if skip is None else skip,
                                     onedrive_prevails=opts["onedrive_prevails"] if onedrive is None else onedrive)
    # Each hit names the scan it was read from, taken from the indexes just searched: a folder id means nothing without it.
    scan_ids = {root.key: scan["id"] for root, scan, _ in entries}
    found["results"] = [{**hit, "scan_id": scan_ids[hit["root"]]} for hit in found["results"]]
    return found


def doc_hits(cfg: dict[str, Any], q: str, offset: int, limit: int) -> dict[str, Any]:
    """One page of the Markdown list: ``GET /api/search/docs``. Reads each root's current index and nothing on disk."""
    sources = [(root, scan, index, lock) for root, scan, (index, lock) in _open_scans(cfg)]
    return listing.search(sources, q, cfg["explore"]["show_hidden"], offset, limit)


def _subfolders(cfg: dict[str, Any], root: Root, scan: dict[str, Any], path: str, fid: int) -> list[dict[str, Any]]:
    """The direct subfolders of folder ``fid`` of ``scan`` (at ``path``), heaviest first, shaped like a folder search hit."""
    skip = cfg["folder_search"]["skip_levels"]
    index, lock = POOL.get(store.index_dir() / scan["index_file"])
    with lock:
        kids = index.children(fid, "size", None, cfg["explore"]["show_hidden"])["children"]
    out = []
    for kid in kids:
        child = os.path.join(path, kid["name"])
        out.append({**kid, "root": root.key, "path": child, "display": folder_search.display_path(child, skip)})
    return out


def folder_children(cfg: dict[str, Any], path: str) -> list[dict[str, Any]]:
    """The direct subfolders of the scanned folder at ``path``, heaviest first, shaped like a folder search hit.

    Looked up by path in the root's current scan, so a newer scan between the search and the step in
    can't swap the folder; empty when no scan holds it.
    """
    roots = resolve_roots(cfg["roots"])
    place = locate_folder(roots, path)
    if place is None:
        return []
    root = next(r for r in roots if r.key == place["root"])
    scan = store.current_index(root.key)
    if scan is None:
        return []
    return _subfolders(cfg, root, scan, path, place["id"])


class ScanChanged(Exception):
    """A folder id was read from a scan that is no longer the root's current one, so it could name another folder now."""


def scanned_item_path(roots: list[Root], root_key: str, scan_id: object, kind: str,
                      item_id: int) -> tuple[Root, dict[str, Any], str]:
    """A folder or file named by root key, scan id and item id: its root, the root's current scan row and its path on disk.

    Every route that hands an id to Windows or to a listing resolves it here, never from a client path.
    ``KeyError`` when the root, its scan or the item is unknown, ``ScanChanged`` when ``scan_id`` is not the
    root's current scan (an id from an older scan could name another item now).
    """
    root = next((r for r in roots if r.key == root_key), None)
    scan = store.current_index(root.key) if root else None
    if root is None or scan is None:
        raise KeyError("no such root")
    if scan_id != scan["id"]:
        raise ScanChanged(root_key)
    index, lock = POOL.get(store.index_dir() / scan["index_file"])
    with lock:
        return root, scan, item_path(root, index, kind, item_id)


def sheet_hit(hit: dict[str, Any], scan_id: int, email_roots: list[str]) -> dict[str, Any]:
    """One folder as the Archive sheet lists it: its path and name, its place in the scan, and whether a mail may be filed in it."""
    return {"path": hit["path"], "name": hit["name"], "fileable": under_any(hit["path"], email_roots),
            "location": {"root": hit["root"], "scan_id": scan_id, "id": hit["id"]}}


def _sheet_page(hits: list[dict[str, Any]]) -> dict[str, Any]:
    """The first rows of ``hits`` with the folders a mail can go in first, each group in its own order, and how many there were."""
    ordered = sorted(hits, key=lambda h: not h["fileable"])
    return {"folders": ordered[:SHEET_FOLDER_ROWS], "total": len(ordered)}


def sheet_folders(cfg: dict[str, Any], query: str) -> dict[str, Any]:
    """The Archive sheet's folder search: Quick search's own, every scanned folder, those inside the email folders first.

    A folder outside ``email.roots`` is listed too, marked ``fileable: false``: it can be stepped into and shown
    in File Explorer, never filed in (the filer refuses it).
    """
    email_roots = list((cfg.get("email") or {}).get("roots") or [])
    return _sheet_page([sheet_hit(r, r["scan_id"], email_roots) for r in folder_hits(cfg, query)["results"]])


def sheet_subfolders(cfg: dict[str, Any], root_key: str, scan_id: int, folder_id: int) -> dict[str, Any]:
    """The subfolders of the folder a sheet row names by root, scan id and folder id, in the sheet's shape.

    Never takes a path from the client: the path is rebuilt from the scan. ``KeyError`` when the root, the scan or
    the folder is unknown, ``ScanChanged`` when ``scan_id`` is not the root's current scan.
    """
    root, scan, path = scanned_item_path(resolve_roots(cfg["roots"]), root_key, scan_id, "folder", folder_id)
    email_roots = list((cfg.get("email") or {}).get("roots") or [])
    kids = [sheet_hit(k, scan["id"], email_roots) for k in _subfolders(cfg, root, scan, path, folder_id)]
    return {"path": path, **_sheet_page(kids)}


class LocalFolders:
    """The folder search and a folder's subfolders from this process's own index pool.

    The archive window opens in the Outlook bridge's process, often with no web app running, so it
    searches here rather than over HTTP, through the same ``folder_hits`` the search route calls.
    """

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg

    def folders(self, query: str) -> dict[str, Any]:
        return folder_hits(self.cfg, query)

    def children(self, path: str) -> list[dict[str, Any]]:
        return folder_children(self.cfg, path)
