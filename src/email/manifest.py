"""Which ``.msg`` files the email folders hold: path, size, modified time, online-only.

Two sources, both metadata only. After a disk scan the manifest comes from
that scan's index, so the nightly run lists every folder once. A scoped run
(``email index --root <folder>``) lists just that folder with the same
directory-handle listing the disk walk uses. Either way a folder whose listing
failed somewhere below makes its manifest incomplete, and the stage then
purges nothing under it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from src import winfs
from src.index import Index
from src.scanner import Lister, exclude_key, extension

MSG_EXT = "msg"


@dataclass(frozen=True)
class MsgFile:
    path: str
    size: int
    mtime: int
    online: bool


@dataclass
class Manifest:
    """The ``.msg`` files under one email folder, and whether every folder below it was listed."""

    folder: str
    files: list[MsgFile] = field(default_factory=list)
    complete: bool = True


def from_index(index: Index, root_path: str, folder: str) -> Optional[Manifest]:
    """The manifest of ``folder`` from a disk index of the root at ``root_path``; None when it isn't there."""
    rel = os.path.relpath(folder, root_path)
    names = [] if rel == "." else [p for p in rel.split(os.sep) if p]
    fid = index.find_folder(names)
    if fid is None:
        return None
    f = index.folder(fid)
    paths: dict[int, str] = {}
    out = Manifest(folder, complete=int(f["errors"]) == 0)
    for row in index.conn.execute(
        "SELECT folder_id, name, size, attrs, mtime FROM files WHERE ext = ? AND folder_id BETWEEN ? AND ?",
        (MSG_EXT, f["lo"], f["hi"]),
    ):
        fol = int(row["folder_id"])
        if fol not in paths:
            below = [p["name"] for p in index.path(fol)][len(names) + 1:]
            paths[fol] = os.path.join(folder, *below) if below else folder
        out.files.append(MsgFile(os.path.join(paths[fol], row["name"]), int(row["size"]), int(row["mtime"]),
                                 winfs.is_online_only(int(row["attrs"]))))
    return out


def walk(folder: str, lister: Lister = winfs.list_dir, exclude: frozenset[str] = frozenset()) -> Manifest:
    """List ``folder`` and everything below it; links are counted out, never followed."""
    out = Manifest(folder)
    stack = [folder]
    while stack:
        path = stack.pop()
        try:
            entries = list(lister(path))
        except OSError:
            out.complete = False
            continue
        for e in entries:
            full = os.path.join(path, e.name)
            if winfs.is_link(e):
                continue
            if e.is_dir:
                if exclude_key(full) not in exclude:
                    stack.append(full)
            elif extension(e.name) == MSG_EXT:
                out.files.append(MsgFile(full, e.size, e.mtime, winfs.is_online_only(e.attrs)))
    return out
