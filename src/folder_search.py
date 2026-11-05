"""Folder search over every root's current index: every word must appear in the folder's full path.

A query is split on spaces and ``;`` into words; a folder matches when its
full path (case-folded, either separator) holds every word, so ``alpha
budget`` finds ``...\\Alpha\\2025\\Budget`` where the ancestry supplies one word
and the folder's own name the other. A folder whose subfolders are named like
email addresses is a "folder of people"; with ``prune`` on, every match at or
below it folds into that one result. ``skip`` hides leading path components
from the displayed path only; the full path is always kept for copying and
opening.

This is a query over the scan's index, never a walk: it reads no folder on disk.
"""

from __future__ import annotations

import bisect
import os
from contextlib import contextmanager
from typing import Any, Iterator

from src import store
from src.index import Index, open_index
from src.roots import Root
from src.scanner import search_key

# Folders read per root before ranking, and results returned in all.
CANDIDATES = 5000
LIMIT = 200


def parse_terms(text: str) -> list[str]:
    """The query's words, search-keyed like the index's paths."""
    return [search_key(t) for t in (text or "").replace(";", " ").split() if t.strip()]


def display_path(path: str, skip: int) -> str:
    """``path`` without its first ``skip`` components; the last one always stays."""
    parts = [p for p in path.replace("/", "\\").split("\\") if p]
    if skip <= 0 or not parts:
        return path
    return "\\".join(parts[min(skip, len(parts) - 1):])


def _fold(ids: list[int], owners: list[tuple[int, int, int, int]]) -> dict[int, int]:
    """Each match's result id: its outermost folder of people when one contains it, else itself."""
    los = [o[0] for o in owners]
    out: dict[int, int] = {}
    for fid in ids:
        pos = bisect.bisect_right(los, fid) - 1
        if pos >= 0 and owners[pos][0] <= fid <= owners[pos][1]:
            out[fid] = owners[pos][2]
        else:
            out[fid] = fid
    return out


def full_path(root: Root, names: list[str]) -> str:
    """The folder's path on disk from the root's path and the names below it."""
    return os.path.join(root.path, *names) if names else root.path


def item_path(root: Root, index: Index, kind: str, item_id: int) -> str:
    """A folder's or a file's path on disk, from its id in the root's index; ``KeyError`` when there is none.

    The path is built from the root's own path and the names the scan
    recorded below it, so it never leaves the root.
    """
    if kind == "folder":
        index.folder(item_id)
        return full_path(root, [p["name"] for p in index.path(item_id)][1:])
    row = index.file_row(item_id)
    folder = int(row["folder_id"])
    return os.path.join(full_path(root, [p["name"] for p in index.path(folder)][1:]), row["name"])


def search_root(root: Root, index: Index, terms: list[str], prune: bool, skip: int) -> dict[str, Any]:
    """One root's matching folders, ranked, plus whether the candidate cap cut the list."""
    if not index.searchable():
        return {"results": [], "truncated": False, "needs_rescan": True}
    ids, truncated = index.match_folders(terms, CANDIDATES)
    owners = index.people_folders() if prune else []
    folded = _fold(ids, owners) if owners else {i: i for i in ids}
    people = {o[2]: o[3] for o in owners}
    rows = index.folder_rows(sorted(set(folded.values())))
    results = []
    for r in rows:
        name_key = search_key(r["name"])
        results.append({
            "root": root.key, "root_name": root.name, "kind": root.kind, "id": int(r["id"]), "name": r["name"],
            "size": int(r["size"]), "disk": int(r["alloc"]), "files": int(r["files"]),
            "online_files": int(r["online_files"]), "people": people.get(int(r["id"]), 0),
            # A word in the folder's own name counts most; a shallower folder wins a tie.
            "score": sum(10 for t in terms if t in name_key) - int(r["depth"]),
        })
    return {"results": results, "truncated": truncated, "needs_rescan": False}


def _with_path(hit: dict[str, Any], root: Root, index: Index, skip: int) -> dict[str, Any]:
    names = [p["name"] for p in index.path(hit["id"])][1:]
    path = full_path(root, names)
    return {**hit, "path": path, "display": display_path(path, skip)}


@contextmanager
def open_scanned(roots: list[Root]) -> Iterator[list[tuple[Root, Index]]]:
    """Each root with a finished scan and its current index, opened for this block only.

    The web app shares its open-index pool; a one-shot caller (the CLI) opens
    its own read-only connections here and closes them on the way out.
    """
    opened: list[tuple[Root, Index]] = []
    try:
        for root in roots:
            scan = store.current_index(root.key)
            if scan is not None:
                opened.append((root, Index(open_index(store.index_dir() / scan["index_file"]))))
        yield opened
    finally:
        for _, index in opened:
            index.conn.close()


def search(indexes: list[tuple[Root, Index]], text: str, prune: bool, skip: int, limit: int = LIMIT) -> dict[str, Any]:
    """Every root's matches merged: the best ``limit``, the total, and the roots that need a rescan first."""
    terms = parse_terms(text)
    if not terms:
        return {"results": [], "total": 0, "truncated": False, "needs_rescan": []}
    merged: list[tuple[dict[str, Any], Root, Index]] = []
    truncated = False
    stale = []
    for root, index in indexes:
        part = search_root(root, index, terms, prune, skip)
        merged.extend((hit, root, index) for hit in part["results"])
        truncated = truncated or part["truncated"]
        if part["needs_rescan"]:
            stale.append(root.name)
    merged.sort(key=lambda x: (-x[0]["score"], -x[0]["size"]))
    # Paths are rebuilt only for the results shown: each costs one lookup per level.
    shown = [_with_path(hit, root, index, skip) for hit, root, index in merged[:limit]]
    return {"results": shown, "total": len(merged), "truncated": truncated or len(merged) > limit,
            "needs_rescan": stale}
