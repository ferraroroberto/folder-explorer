"""Scan roots: every local fixed drive plus OneDrive by default, or the configured list.

Each root is deduplicated by its resolved, case-folded path, so two settings
that name the same folder (``OneDrive`` and ``OneDriveConsumer`` usually point
at one directory) become one root, scanned once and listed once.
"""

from __future__ import annotations

import os
import re
import string
import sys
from dataclasses import dataclass
from typing import Optional

# Environment variables the OneDrive client sets for the personal and the
# work account; either, both or neither may exist, often with the same value.
ONEDRIVE_ENV_VARS = ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")

_DRIVE_FIXED = 3


@dataclass(frozen=True)
class Root:
    """One scan root as the app shows it."""

    key: str
    name: str
    path: str
    kind: str  # "local" or "synced"

    def as_dict(self) -> dict[str, str]:
        return {"key": self.key, "name": self.name, "path": self.path, "kind": self.kind}


def normalize(path: str) -> str:
    """The identity two root paths are compared by."""
    return os.path.normcase(os.path.realpath(os.path.expandvars(os.path.expanduser(path)))).rstrip("\\/") or path


def fixed_drives() -> list[str]:
    """Root paths of the local fixed drives (``C:\\``), never network or removable ones."""
    if sys.platform != "win32":
        return ["/"]
    import ctypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    mask = k32.GetLogicalDrives()
    drives = []
    for i, letter in enumerate(string.ascii_uppercase):
        if mask & (1 << i):
            root = letter + ":\\"
            if k32.GetDriveTypeW(ctypes.c_wchar_p(root)) == _DRIVE_FIXED:
                drives.append(root)
    return drives


def onedrive_folders() -> list[str]:
    """The OneDrive folders this user's sync clients declare, existing ones only."""
    found = []
    for var in ONEDRIVE_ENV_VARS:
        value = os.environ.get(var, "").strip()
        if value and os.path.isdir(value):
            found.append(value)
    return found


def _volume_label(drive: str) -> str:
    if sys.platform != "win32":
        return ""
    import ctypes

    buf = ctypes.create_unicode_buffer(261)
    ok = ctypes.WinDLL("kernel32").GetVolumeInformationW(ctypes.c_wchar_p(drive), buf, 261, None, None, None, None, 0)
    return buf.value if ok else ""


def _is_drive_root(path: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z]:[\\/]*", path))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "root"


def describe(path: str, synced_paths: set[str]) -> tuple[str, str, str]:
    """``(key stem, display name, kind)`` for one root path."""
    if _is_drive_root(path):
        letter = path[0].upper()
        label = _volume_label(letter + ":\\")
        return letter.lower(), (label + " (" + letter + ":)") if label else letter + ":", "local"
    base = os.path.basename(path.rstrip("\\/")) or path
    kind = "synced" if normalize(path) in synced_paths else "local"
    name = "OneDrive" if kind == "synced" and base.lower().startswith("onedrive") else base
    return _slug(name), name, kind


def resolve_roots(configured: Optional[list[str]]) -> list[Root]:
    """The roots to show and scan.

    ``configured`` of ``None`` (the default) means every fixed drive plus each
    OneDrive folder. A list names the root paths explicitly. Paths that do not
    exist are kept, so the Scans tab can say the root is missing rather than
    silently dropping it.
    """
    synced = {normalize(p) for p in onedrive_folders()}
    paths = fixed_drives() + onedrive_folders() if configured is None else [p for p in configured if str(p).strip()]
    roots: list[Root] = []
    seen: set[str] = set()
    keys: set[str] = set()
    for raw in paths:
        path = os.path.expandvars(os.path.expanduser(str(raw).strip()))
        if _is_drive_root(path):
            # One spelling, so a path joined below it has a single separator and never a drive-relative ``C:Users``.
            path = path[0].upper() + ":\\"
        ident = normalize(path)
        if ident in seen:
            continue
        seen.add(ident)
        stem, name, kind = describe(path, synced)
        key, n = stem, 2
        while key in keys:
            key, n = stem + "-" + str(n), n + 1
        keys.add(key)
        roots.append(Root(key, name, path, kind))
    return roots
