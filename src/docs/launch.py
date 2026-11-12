"""Where a file Windows hands the "Open with" launcher sits in the scan: its root, that root's current scan, its folder's id and its name.

The launcher runs on this PC with the path Windows gives it. It matches that
path against the configured roots and reads the root's current index
(read-only) here, in its own process, so the request it then sends the app
names the file by root, scan id, folder id and file name, never by a path.
Nothing here opens or lists the file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from src import store
from src.docs.reader import is_document
from src.email.settings import is_under
from src.index import Index, open_index
from src.roots import Root

MESSAGES = {
    "not_document": "Only Markdown files open in folder-explorer.",
    "no_root": "This file is in no folder folder-explorer scans.",
    "not_scanned": "This file's drive has no finished scan yet. Run a scan, then open it again.",
    "folder_not_scanned": "This file's folder is newer than the last scan. Run a scan, then open it again.",
}


class NotPlaced(Exception):
    """The file can't be named by the scan, with the reason code and the sentence the launcher shows."""

    def __init__(self, reason: str) -> None:
        super().__init__(MESSAGES[reason])
        self.reason = reason


@dataclass(frozen=True)
class Place:
    """A file by its scanned folder: what ``POST /api/doc/find`` takes."""

    root: str
    scan_id: int
    folder: int
    name: str

    def as_request(self) -> dict[str, Any]:
        return {"root": self.root, "scan_id": self.scan_id, "folder": self.folder, "name": self.name}


def place_of(path: str, roots: list[Root]) -> Place:
    """The scanned folder holding ``path`` and the file's name; raises :class:`NotPlaced`.

    The innermost root holding the path decides, as it does for a folder.
    """
    path = os.path.abspath(path)
    name = os.path.basename(path)
    if not is_document(name):
        raise NotPlaced("not_document")
    root = next((r for r in sorted(roots, key=lambda r: len(r.path), reverse=True) if is_under(path, r.path)), None)
    if root is None:
        raise NotPlaced("no_root")
    scan = store.current_index(root.key)
    if scan is None:
        raise NotPlaced("not_scanned")
    names = [p for p in os.path.relpath(os.path.dirname(path), root.path).split(os.sep) if p and p != "."]
    conn = open_index(store.index_dir() / scan["index_file"])
    try:
        folder = Index(conn).find_folder(names)
    finally:
        conn.close()
    if folder is None:
        raise NotPlaced("folder_not_scanned")
    return Place(root.key, int(scan["id"]), folder, name)
