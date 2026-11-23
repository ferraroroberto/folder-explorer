"""Atomic writes: a reader sees the old file or the new one, never half of either.

The one place this repo calls ``os.replace`` for a write. Each write goes to a
unique ``mkstemp`` sibling of the target (two writers of one file never share a
temp), then ``os.replace`` swaps it in. When the write or the swap raises, the
temp is unlinked and the error re-raised, so the old content stays intact, no
orphan is left beside it, and each caller keeps its own failure policy. The
parent folder must already exist.

On Windows the swap fails while another program holds the target open
without delete sharing, as OneDrive does for a moment while it syncs a file:
the swap is retried for a bounded time before the error is raised.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION: what a replace meets while a sync engine reads the target.
HELD_ERRORS = frozenset({5, 32, 33})
# About 2.3 s in all: a sync engine's read of a document finishes well inside it, and a save never hangs.
REPLACE_WAITS = (0.05, 0.1, 0.2, 0.4, 0.6, 0.9)


def is_held(exc: OSError) -> bool:
    """Whether Windows refused because another program holds the file open, rather than for good."""
    return getattr(exc, "winerror", None) in HELD_ERRORS


def _replace(temporary: str, path: Path) -> None:
    """``os.replace``, retried while the target is held open by another program."""
    for attempt, wait in enumerate(REPLACE_WAITS, 1):
        try:
            os.replace(temporary, path)
        except OSError as exc:
            if not is_held(exc):
                raise
            time.sleep(wait)
        else:
            if attempt > 1:
                logger.info("ℹ️ atomic write: %s was held open, replaced on try %d", path.name, attempt)
            return
    os.replace(temporary, path)


def atomic_write_text(path: Path, text: str) -> None:
    """Replace ``path`` with ``text`` as UTF-8."""
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        _replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data`` exactly, with no newline translation (a Markdown file keeps its own line ends)."""
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        _replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def swap_in(built: Path, dest: Path) -> None:
    """Rename a file that was built and closed beside ``dest`` onto it; there is no text to write."""
    os.replace(built, dest)


__all__ = ["atomic_write_bytes", "atomic_write_text", "is_held", "swap_in"]
