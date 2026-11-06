"""Process-wide webapp state: the open-index pool, the open email index, and their housekeeping."""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from src import store
from src.email.search import EmailIndexHolder
from src.email.settings import is_under
from src.index import IndexPool
from src.roots import Root

logger = logging.getLogger(__name__)

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
