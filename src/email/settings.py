"""The email part's settings: where the index is, which folders may be read, and whether it can run here.

Email search turns on when ``email.index_path`` names an existing index.
"Show all" also needs the email folders (``email.roots``), each inside a scan
root, and the reader's ``extract-msg`` package (``requirements-email.txt``).
A part that cannot run says why in its own words, never with an empty result.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from src.roots import Root, normalize


@dataclass(frozen=True)
class EmailPart:
    """The email part as this PC can run it."""

    index_path: Optional[Path]
    roots: list[str] = field(default_factory=list)
    # Why email search is off, or None when it can search.
    problem: Optional[str] = None
    # Why "Show all" is off, or None when it can read a mail.
    reader_problem: Optional[str] = None

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
    if not raw_path:
        return EmailPart(None, roots, "no email index is set (email.index_path)", "email search is off")
    path = Path(os.path.expandvars(os.path.expanduser(raw_path)))
    problem = None if path.is_file() else "the email index was not found: " + path.name
    outside = [r for r in roots if not under_any(r, [s.path for s in scan_roots])]
    if not roots:
        reader = "no email folders are set (email.roots)"
    elif outside:
        reader = "an email folder is outside every scan root: " + outside[0]
    elif not reader_installed():
        reader = "extract-msg isn't installed (requirements-email.txt)"
    else:
        reader = None
    return EmailPart(path, roots if not outside else [], problem, reader)
