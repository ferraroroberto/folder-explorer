"""The folder lists Settings edits: scan roots, the skip list, email folders and email excludes.

``config`` checks each value's shape; this module checks the paths themselves
before a save: a new entry must be an existing folder, written in full, once,
not nested in another entry of its list, and inside the folder it belongs to
(an email folder or a skip folder inside a scan root, an email exclude inside
an email folder). Only entries that are not already saved are checked, so a
stale path from an older config still loads, shows and can be removed. A
folder that still holds an entry of the list below it can't be removed until
that entry goes first. Checking a folder is one directory query, never a file
read, so a OneDrive folder is never downloaded.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Optional

from src import config
from src.email.settings import is_under, under_any
from src.roots import normalize, resolve_roots

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FolderList:
    """One list Settings edits: how to read it from a config and how to name it."""

    label: str
    get: Callable[[dict[str, Any]], list[str]]


SCAN = FolderList("Folders to scan", lambda cfg: [r.path for r in resolve_roots(cfg["roots"])])
SKIP = FolderList("Skip these folders", lambda cfg: list(cfg["exclude"]))
EMAIL = FolderList("Email folders", lambda cfg: list(cfg["email"]["roots"]))
EMAIL_SKIP = FolderList("Leave out of the email index", lambda cfg: list(cfg["email"]["exclude"]))

# Each list and the list its new entries must sit inside.
INSIDE: tuple[tuple[FolderList, Optional[FolderList]], ...] = (
    (SCAN, None), (SKIP, SCAN), (EMAIL, SCAN), (EMAIL_SKIP, EMAIL),
)


def _expanded(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path.strip()))


def _check_new(path: str, others: list[str], parent: Optional[list[str]], parent_label: str) -> None:
    full = _expanded(path)
    if not os.path.isabs(full):
        raise ValueError("Use a full folder path: " + path)
    if not os.path.isdir(full):
        raise ValueError("Folder not found: " + path)
    for other in others:
        if normalize(_expanded(other)) == normalize(full):
            raise ValueError("Already in the list: " + path)
        if is_under(full, _expanded(other)):
            raise ValueError(path + " is inside " + other + ", which is already in the list")
        if is_under(_expanded(other), full):
            raise ValueError(path + " holds " + other + ", which is already in the list")
    if parent is not None and not under_any(full, [_expanded(p) for p in parent]):
        raise ValueError(path + " is not inside any of " + parent_label.lower())


def check(current: dict[str, Any], merged: dict[str, Any]) -> None:
    """Raise ``ValueError`` when ``merged`` adds a bad folder or orphans a saved one."""
    for child, parent in INSIDE:
        before, after = child.get(current), child.get(merged)
        # Each saved entry excuses one entry of the new list, so a second copy is checked as new.
        saved = Counter(normalize(_expanded(p)) for p in before)
        parent_after = parent.get(merged) if parent else None
        for i, path in enumerate(after):
            key = normalize(_expanded(path))
            if saved[key]:
                saved[key] -= 1
            else:
                _check_new(path, after[:i] + after[i + 1:], parent_after, parent.label if parent else "")
        if parent is None:
            continue
        gone = [p for p in parent.get(current) if not under_any(_expanded(p), [_expanded(q) for q in parent_after])]
        for path in after:
            holder = next((p for p in gone if is_under(_expanded(path), _expanded(p))), None)
            if holder is not None and path in before:
                raise ValueError("Remove " + path + " from " + child.label.lower() + " first: " + holder + " still holds it")


def save(updates: dict[str, Any]) -> dict[str, Any]:
    """Check the shape, then the paths, then save; returns the new effective settings."""
    if updates.get("roots") == []:
        raise ValueError("Keep at least one folder to scan, or switch to every local drive and OneDrive")
    clean = config.validate_update(updates)
    current = config.load()
    merged = {**current, **clean}
    try:
        check(current, merged)
    except ValueError as exc:
        logger.info("ℹ️ folder list change refused: %s", exc)
        raise
    return config.save(clean)
