"""Where a document's relative link points: path arithmetic fenced to the root, then the item's id in the index.

Nothing here touches the disk. A link resolves against the document's own
folder and must stay inside the root it was scanned under: an absolute path,
a drive, a share, a URL scheme or enough ``..`` to climb out resolve to
nothing. The target is then looked up in the root's index by its names, so
every later read still goes by root, scan id and item id.
"""

from __future__ import annotations

import ntpath
import os
import re
from typing import Any, Optional
from urllib.parse import unquote

from src.index import Index

# A scheme (https:, mailto:, file:) or a drive (C:); either means the link is not relative to the document.
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def split_fragment(href: str) -> tuple[str, str]:
    """``href`` without its query and fragment, and the fragment (no ``#``)."""
    path, _, fragment = href.partition("#")
    return path.partition("?")[0], fragment


def resolve_href(root_path: str, doc_path: str, href: str) -> Optional[str]:
    """The path ``href`` names relative to the document at ``doc_path``, or None when it leaves the root or isn't relative."""
    path = unquote(split_fragment(href.strip())[0])
    if not path or _SCHEME.match(path) or path.startswith(("/", "\\")):
        return None
    target = ntpath.normpath(ntpath.join(ntpath.dirname(doc_path), path.replace("/", "\\")))
    root = ntpath.normpath(root_path)
    try:
        inside = ntpath.commonpath([ntpath.normcase(root), ntpath.normcase(target)]) == ntpath.normcase(root)
    except ValueError:  # a different drive
        return None
    if not inside or ntpath.normcase(target) == ntpath.normcase(root):
        return None
    return target if os.sep == "\\" else target.replace("\\", os.sep)


def find_item(root_path: str, index: Index, path: str) -> Optional[dict[str, Any]]:
    """The indexed file at ``path`` under the root (its id, size and cloud flag), or None when the scan didn't see it."""
    rel = ntpath.relpath(ntpath.normpath(path.replace(os.sep, "\\")), ntpath.normpath(root_path.replace(os.sep, "\\")))
    names = [p for p in rel.split("\\") if p and p != "."]
    if not names or ".." in names:
        return None
    folder = index.find_folder(names[:-1])
    return index.find_file(folder, names[-1]) if folder is not None else None
