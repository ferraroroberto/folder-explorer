"""Every indexed Markdown file across the roots, newest first, narrowed by name.

Reads the index only: no file is opened, listed or stat'ed, so a cloud-only
file is listed with its online mark and nothing comes down. Each hit names its
file by root, scan id and id, which is how the viewer opens it; ``path`` is for display and Copy path only.
"""

from __future__ import annotations

import os
import re
import threading
from typing import Any, Sequence

from src.docs import reader
from src.folder_match import fold
from src.index import Index
from src.roots import Root

# The index keeps an extension lower-case and without its dot; the reader owns which ones are Markdown.
EXTS = tuple(sorted(e.lstrip(".") for e in reader.DOC_EXTS))
# Hits per page: a whole page is drawn at once, and more load on a tap.
PAGE = 100
MAX_PAGE = 200
_WORDS = re.compile(r"[\s;]+")

# One root with a finished scan: its root, scan row, open index and that index's lock.
Source = tuple[Root, dict[str, Any], Index, threading.Lock]


def _matches(name: str, words: list[str]) -> bool:
    folded = fold(name)
    return all(w in folded for w in words)


def search(sources: Sequence[Source], query: str, show_hidden: bool, offset: int = 0, limit: int = PAGE) -> dict[str, Any]:
    """One page of the Markdown files whose name holds every word of ``query``, newest first across all ``sources``."""
    words = [fold(w) for w in _WORDS.split(query) if w]
    found: list[tuple[int, dict[str, Any]]] = []
    for at, (_root, _scan, index, lock) in enumerate(sources):
        with lock:
            files = index.files_by_ext(EXTS, show_hidden)
        found.extend((at, f) for f in files if _matches(f["name"], words))
    found.sort(key=lambda pair: (-pair[1]["mtime"], pair[1]["name"].casefold()))
    page = []
    below: dict[tuple[int, int], list[str]] = {}
    for at, f in found[offset:offset + limit]:
        root, scan, index, lock = sources[at]
        if (at, f["folder_id"]) not in below:
            with lock:
                below[(at, f["folder_id"])] = [p["name"] for p in index.path(f["folder_id"])][1:]
        names = below[(at, f["folder_id"])]
        page.append({"root": root.key, "root_name": root.name, "scan_id": scan["id"], "id": f["id"], "name": f["name"],
                     "ext": f["ext"], "size": f["size"], "mtime": f["mtime"], "online": f["online"],
                     "folder_id": f["folder_id"], "folder": names, "path": os.path.join(root.path, *names, f["name"])})
    return {"hits": page, "total": len(found), "offset": offset}
