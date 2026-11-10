"""Process-wide webapp state: the open-index pool, the open email index, their housekeeping, and the shared folder search."""

from __future__ import annotations

import logging
import os
from contextlib import ExitStack
from typing import Any, Optional

from src import folder_search, store
from src.email.search import EmailIndexHolder
from src.email.settings import is_under, under_any
from src.index import IndexPool
from src.roots import Root, resolve_roots

logger = logging.getLogger(__name__)

# Folders the Archive sheet offers when its "Another email folder" box is searched.
EMAIL_FOLDER_HITS = 8

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
        return folder_search.search([(root, ix) for root, _, (ix, _) in entries], q,
                                    opts["prune_people"] if prune is None else prune,
                                    opts["skip_levels"] if skip is None else skip,
                                    onedrive_prevails=opts["onedrive_prevails"] if onedrive is None else onedrive)


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
    skip = cfg["folder_search"]["skip_levels"]
    index, lock = POOL.get(store.index_dir() / scan["index_file"])
    with lock:
        kids = index.children(place["id"], "size", None, cfg["explore"]["show_hidden"])["children"]
    out = []
    for kid in kids:
        child = os.path.join(path, kid["name"])
        out.append({**kid, "root": root.key, "path": child, "display": folder_search.display_path(child, skip)})
    return out


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


def email_folder_hits(cfg: dict[str, Any], query: str, limit: int) -> list[dict[str, Any]]:
    """The folder search's hits inside the email folders, each with its place in the scan.

    The Archive sheet's "Another email folder" box offers these: the same matcher and options as Quick search,
    kept to the folders a mail can be filed in.
    """
    email_roots = list((cfg.get("email") or {}).get("roots") or [])
    scan_ids = {root.key: scan["id"] for root, scan, _ in _open_scans(cfg)}
    hits = [{"path": r["path"], "name": r["name"],
             "location": {"root": r["root"], "scan_id": scan_ids[r["root"]], "id": r["id"]}}
            for r in folder_hits(cfg, query)["results"] if under_any(r["path"], email_roots)]
    return hits[:limit]
