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

from src import winfs

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


def key_within(inner: str, outer: str) -> bool:
    """True when ``inner`` is ``outer`` or sits inside it, both already in one spelling (nothing is resolved here)."""
    return inner == outer or inner.startswith(outer.rstrip("\\/") + os.sep)


def is_under(path: str, folder: str) -> bool:
    """True when ``path`` is ``folder`` or sits inside it, compared case-folded."""
    return key_within(normalize(path), normalize(folder))


def under_any(path: str, folders: list[str]) -> bool:
    return any(is_under(path, f) for f in folders)


def same_spelling(a: str, b: str) -> bool:
    """True when two strings spell one folder: case, slashes and a trailing separator ignored. Never touches the disk."""
    def spelled(p: str) -> str:
        return os.path.normcase(os.path.normpath(p)).rstrip("\\/")

    return spelled(a) == spelled(b)


def relative_parts(inner: str, outer: str) -> Optional[list[str]]:
    """The folder names from ``outer`` down to ``inner``, or ``None`` when ``inner`` is not strictly inside ``outer``."""
    a, b = normalize(inner), normalize(outer)
    if a == b or not key_within(a, b):
        return None
    return [p for p in a[len(b.rstrip("\\/")):].split(os.sep) if p]


def folder_identity(path: str) -> Optional[tuple[int, int]]:
    """The volume serial and file ID of the folder ``path`` reaches, or ``None`` when they can't be read."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino) if st.st_ino else None


def parts_within(inner: str, outer: str) -> Optional[list[str]]:
    """The folder names from ``outer`` down to ``inner`` by the folders the two paths reach, however they are spelled.

    Empty when both are one folder, ``None`` when ``inner`` is not inside
    ``outer``. Resolved paths answer first; a spelling they can't relate (a
    share onto a local folder) is matched by volume serial and file ID.
    """
    a, b = normalize(inner), normalize(outer)
    if key_within(a, b):
        return [p for p in a[len(b.rstrip("\\/")):].split(os.sep) if p]
    here, there = os.path.realpath(inner), os.path.realpath(outer)
    drives = [os.path.splitdrive(p)[0].lower() for p in (here, there)]
    # Two drive letters are two volumes, and a cloned disk repeats both numbers, so their IDs are never compared.
    if drives[0] != drives[1] and all(len(d) == 2 for d in drives):
        return None
    wanted = folder_identity(there)
    if wanted is None:
        return None
    names: list[str] = []
    while folder_identity(here) != wanted:
        parent, name = os.path.split(here)
        if not name or parent == here:
            return None
        names.insert(0, name)
        here = parent
    return names


def innermost_root(path: str, roots: list[Root]) -> Optional[Root]:
    """The deepest root holding ``path``, which is the one that answers for it; ``None`` when no root does."""
    return next((r for r in sorted(roots, key=lambda r: len(r.path), reverse=True) if is_under(path, r.path)), None)


def names_below(path: str, root_path: str) -> list[str]:
    """The names from ``root_path`` down to ``path``, which must be inside it; none for the root itself."""
    return [p for p in os.path.relpath(path, root_path).split(os.sep) if p and p != "."]


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
    silently dropping it. Each is named to the lister, so a root that is itself
    a junction lists its target.
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
        winfs.name_root(path)
        roots.append(Root(key, name, path, kind))
    return roots
