"""Process-wide webapp state: the open-index pool and its housekeeping."""

from __future__ import annotations

import logging

from src import store
from src.index import IndexPool
from src.roots import Root

logger = logging.getLogger(__name__)

POOL = IndexPool()


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
