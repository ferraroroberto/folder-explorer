"""The email part's settings: which index, which folders, and whether each piece can run here.

``email.roots`` are the email folders, each inside a scan root. With
``email.index_path`` null (the default) the app keeps its own index in the
runtime-data folder: the email stage fills it from those folders and search
reads it. A path instead names another app's index, which search reads and
nothing here writes. "Show all" and the stage need the reader's
``extract-msg`` (``requirements-email.txt``). A piece that cannot run says why
in its own words, never with an empty result.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from src.email.schema import own_index_path
from src.roots import Root, normalize

MB = 1024 * 1024


@dataclass(frozen=True)
class EmailPart:
    """The email part as this PC can run it."""

    index_path: Optional[Path]
    roots: list[str] = field(default_factory=list)
    # Why email search is off, or None when it can search.
    problem: Optional[str] = None
    # Why "Show all" is off, or None when it can read a mail.
    reader_problem: Optional[str] = None
    # True when the index is this app's own, which the email stage writes.
    own: bool = True
    budget_bytes: int = 0
    # Why the email stage cannot run, or None when it can.
    stage_problem: Optional[str] = None

    @property
    def ready(self) -> bool:
        return self.problem is None


def is_under(path: str, folder: str) -> bool:
    """True when ``path`` is ``folder`` or sits inside it, compared case-folded."""
    p, f = normalize(path), normalize(folder)
    return p == f or p.startswith(f.rstrip("\\/") + os.sep)


def under_any(path: str, folders: list[str]) -> bool:
    return any(is_under(path, f) for f in folders)


def reader_installed() -> bool:
    """Whether the reader's ``extract_msg`` dependency resolves, without importing it."""
    return importlib.util.find_spec("extract_msg") is not None


def email_part(cfg: dict[str, Any], scan_roots: list[Root]) -> EmailPart:
    """The email part from the settings, the scan roots and what is installed."""
    email = cfg.get("email") or {}
    raw_path = email.get("index_path")
    roots = list(email.get("roots") or [])
    outside = [r for r in roots if not under_any(r, [s.path for s in scan_roots])]
    folders_problem = ("no email folders are set (email.roots)" if not roots
                       else "an email folder is outside every scan root: " + outside[0] if outside else None)
    reader = folders_problem or (None if reader_installed() else "extract-msg isn't installed (requirements-email.txt)")
    own = not raw_path
    path = own_index_path() if own else Path(os.path.expandvars(os.path.expanduser(str(raw_path))))
    if path.is_file():
        problem = None
    elif own:
        problem = folders_problem or "the email index isn't built yet: Index emails now in Scans, or import one"
    else:
        problem = "the email index was not found: " + path.name
    stage = ("email.index_path names another app's index, which this app never writes" if not own else reader)
    return EmailPart(path, roots if not outside else [], problem, reader, own,
                     int(email.get("download_budget_mb") or 0) * MB, stage)
