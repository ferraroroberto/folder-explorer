"""Available features: the coarse blocks of the app a machine can switch off, and whether each can run here.

A block is one coherent part that sits on one source or primitive (the email index, the Outlook bridge, the document
files, the tray's hotkey). Each is on by default; ``config.json``'s ``features`` block holds the machine's choice and
is never committed. A block switched off hides its UI, stops its background work and answers its API with a plain
sentence; a block whose dependency is missing here stays switchable but is reported unavailable, with the reason.

This module imports nothing from the app, so the config, the email part and the routers can all ask it.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from typing import Any, Optional

OFF_SENTENCE = "Switched off in Settings, Available features"


@dataclass(frozen=True)
class Block:
    """One feature block: its key in ``config.json``, its Settings title, what it covers, and where a change lands."""

    key: str
    title: str
    covers: str
    # None when a change applies at once; else the part that must restart for it to apply.
    restart: Optional[str] = None


BLOCKS: tuple[Block, ...] = (
    Block("email", "Email",
          "The email index and its search, mail previews with Show all, the email step of every scan and the Emails section of Scans."),
    Block("archive", "Outlook archive",
          "Filing a mail from Outlook: the Archive sheet and window, the Outlook macro, Find in mailbox and the archive verbs over HTTP."),
    Block("documents", "Documents",
          "Opening, editing and dropping Markdown and HTML files, the Documents search mode and the Open with registration."),
    Block("screenshot", "Screenshot",
          "The tray's region screenshot and its hotkey.", restart="the tray"),
)

KEYS: tuple[str, ...] = tuple(b.key for b in BLOCKS)
DEFAULTS: dict[str, bool] = {key: True for key in KEYS}


def clean(raw: Any) -> dict[str, bool]:
    """The ``features`` block with every key present: a key that is missing or not true or false is on."""
    out = dict(DEFAULTS)
    if isinstance(raw, dict):
        for key in KEYS:
            if isinstance(raw.get(key), bool):
                out[key] = raw[key]
    return out


def enabled(cfg: dict[str, Any], key: str) -> bool:
    """Whether block ``key`` is switched on in the loaded settings."""
    if key not in DEFAULTS:
        raise KeyError(key)
    return clean(cfg.get("features"))[key]


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _missing(key: str) -> Optional[str]:
    """Why block ``key`` cannot run on this machine, or None. Looks only; it imports and opens nothing."""
    if key == "email":
        return None if _installed("extract_msg") else "extract-msg isn't installed (requirements-email.txt)"
    if key == "archive":
        if sys.platform != "win32":
            return "Outlook archiving needs Windows"
        if not _installed("win32com"):
            return "pywin32 isn't installed (requirements-outlook.txt)"
        from src.desktop import outlook_exe

        return None if outlook_exe() is not None else "Outlook (classic) isn't installed"
    if key == "documents":
        absent = [m for m in ("markdown_it", "pygments") if not _installed(m)]
        return "the document renderer isn't installed (" + ", ".join(absent) + ")" if absent else None
    if key == "screenshot":
        if sys.platform != "win32":
            return "the screenshot needs Windows"
        absent = [m for m in ("mss", "PIL", "tkinter") if not _installed(m)]
        return "the screenshot's packages aren't installed (" + ", ".join(absent) + "; requirements-tray.txt)" if absent else None
    raise KeyError(key)


def status(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Every block as Settings shows it: ``enabled``, ``available`` and, when it cannot run here, the ``reason``."""
    chosen = clean(cfg.get("features"))
    out = []
    for block in BLOCKS:
        reason = _missing(block.key)
        out.append({"key": block.key, "title": block.title, "covers": block.covers, "enabled": chosen[block.key],
                    "available": reason is None, "reason": reason, "restart": block.restart})
    return out


def validate(value: Any) -> dict[str, bool]:
    """The subset of a ``features`` update that is valid; raises ``ValueError`` for an unknown key or a non-boolean."""
    if not isinstance(value, dict) or not value:
        raise ValueError("features takes " + ", ".join(KEYS) + ", each true or false")
    if not set(value) <= set(KEYS) or not all(isinstance(v, bool) for v in value.values()):
        raise ValueError("features takes " + ", ".join(KEYS) + ", each true or false")
    return dict(value)
