"""Folder search over every root's current index: every word must appear in the folder's full path.

A query is split on spaces and ``;`` into words; a folder matches when its
full path (case-folded, either separator) holds every word, so ``alpha
budget`` finds ``...\\Alpha\\2025\\Budget`` where the ancestry supplies one word
and the folder's own name the other. Accents never matter, and a word of six
letters or more one typo off still finds the folder (``src.folder_match``). Each result
carries a ``score`` from 0 to 1, the mean of its words' scores: 1 for an
exact or accent-folded hit, below 1 for a near word, so folders that hold
every word exactly rank first. A folder whose subfolders are named like
email addresses is a "folder of people"; with ``prune`` on, every match at or
below it folds into that one result. ``skip`` hides leading path components
from the displayed path only; the full path is always kept for copying and
opening. A OneDrive library lives on a local drive too, so with
``onedrive_prevails`` a folder in a scanned library is listed once, as the
library's row, and the drive's copy is left out.

This is a query over the scan's index, never a walk: it reads no folder on disk.
"""

from __future__ import annotations

import bisect
import os
from contextlib import contextmanager
from typing import Any, Iterator, Optional, Sequence

from src import store
from src.folder_match import fold, fuzzy, near_score
from src.index import Index, open_index
from src.roots import Root, normalize, relative_parts
from src.scanner import search_key

# Folders read per root before ranking, and results returned in all.
CANDIDATES = 5000
LIMIT = 200
# Paths read per root while looking for near matches, so a near word over a huge subtree stays interactive.
NEAR_READS = 200_000
# Sorts after any folder id, so a bisect on ``(id, NO_END)`` lands on the span starting at or before ``id``.
NO_END = 1 << 62


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


def synced_spans(root: Root, index: Index, indexes: list[tuple[Root, Index]]) -> list[tuple[int, int]]:
    """The id ranges in a local root's index that a scanned OneDrive root already lists.

    A synced library sits on a local drive, so the drive's scan holds it too.
    Only a library whose own index is searchable counts: hiding the drive's
    copy of one that was never scanned would hide the only copy.
    """
    if root.kind != "local" or not index.searchable():
        return []
    spans = []
    for other, other_index in indexes:
        names = relative_parts(other.path, root.path) if other.kind == "synced" and other_index.searchable() else None
        span = index.subtree_span(names) if names else None
        if span is not None:
            spans.append(span)
    return spans


def in_spans(spans: Sequence[tuple[int, int]], fid: int) -> bool:
    """Whether ``fid`` falls in one of the sorted, disjoint ``(lo, hi)`` ranges."""
    pos = bisect.bisect_right(spans, (fid, NO_END)) - 1
    return pos >= 0 and spans[pos][0] <= fid <= spans[pos][1]


def merge_spans(spans: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """The ranges sorted, with nested or overlapping ones joined, as ``in_spans`` needs them."""
    out: list[tuple[int, int]] = []
    for lo, hi in sorted(spans):
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def within_spans(root: Root, index: Index, paths: Sequence[str]) -> tuple[Optional[list[tuple[int, int]]], set[str]]:
    """The id ranges of the ``paths`` that lie in this root, and which of them it holds.

    The ranges are ``None`` when one of the paths is the root itself (the whole
    root is in scope). A path is walked by the names as typed below the root,
    matched case-insensitively, so a folder the scan never saw is not held.
    """
    spans: list[tuple[int, int]] = []
    held: set[str] = set()
    whole = False
    for path in paths:
        if normalize(path) == normalize(root.path):
            whole = True
            held.add(path)
        elif relative_parts(path, root.path) is not None:
            names = [p for p in os.path.relpath(os.path.abspath(path), root.path).split(os.sep) if p and p != "."]
            span = index.subtree_span(names)
            if span is not None:
                spans.append(span)
                held.add(path)
    return (None if whole else merge_spans(spans)), held


class Term:
    """One query word as one index matches it: the spellings its paths are searched for, and where its near words are."""

    def __init__(self, word: str, index: Index) -> None:
        self.folded = fold(word)
        # An older index stores paths case-folded only, so it is searched for the word as typed and folded.
        self.exact = [self.folded] if index.folds_accents() else sorted({word, self.folded})
        self.near_score = near_score(self.folded) if fuzzy(self.folded) and index.folds_accents() else 0.0
        self.named: set[int] = set()
        self.spans: list[tuple[int, int]] = []

    def find_near(self, index: Index) -> None:
        """Look up the folders under a near word of this one; only asked when the exact hits leave room."""
        if self.near_score:
            words = index.near_words(self.folded)
            if words:
                self.named, self.spans = index.word_spans(words)

    def near(self, fid: int) -> bool:
        return in_spans(self.spans, fid)

    def score(self, fid: int, key: str) -> float:
        """How well this word matches a folded path: 1 for itself, the near score under a near word, else 0."""
        if self.folded in key:
            return 1.0
        return self.near_score if self.near(fid) else 0.0


def _candidates(index: Index, terms: list[Term], room: int, skip_spans: Sequence[tuple[int, int]],
                only_spans: Sequence[tuple[int, int]] = ()) -> tuple[list[tuple[int, str]], int, bool]:
    """``(id, path)`` of folders holding every word exactly, then, when fewer than ``room``, those that need a near word.

    Also says how many lead the list as exact hits. A near match always
    scores below an exact one, so it is looked for only while the exact ones
    leave room in the results shown. Near matches are read from the subtrees
    under a near word, never by a pass over every path. With ``only_spans``
    every candidate lies in one of those subtrees.
    """
    found, truncated = index.match_folders([t.exact for t in terms], CANDIDATES, skip_spans, only_spans)
    exact = len(found)
    if exact >= room:
        return found, exact, truncated
    for t in terms:
        t.find_near(index)
    seen = {fid for fid, _ in found}
    read = 0
    for t in sorted((t for t in terms if t.spans), key=lambda t: sum(hi - lo + 1 for lo, hi in t.spans)):
        # Every path under ``t``'s spans matches ``t``; only the other words are left to check.
        others = [u for u in terms if u is not t]
        for fid, key in index.paths_in(t.spans):
            read += 1
            if read > NEAR_READS or len(found) >= CANDIDATES:
                return found, exact, True
            if fid in seen or (skip_spans and any(lo <= fid <= hi for lo, hi in skip_spans)):
                continue
            if only_spans and not in_spans(only_spans, fid):
                continue
            if all(u.folded in key or u.near(fid) for u in others):
                seen.add(fid)
                found.append((fid, key))
    return found, exact, truncated


def search_root(root: Root, index: Index, words: list[str], prune: bool, skip: int,
                skip_spans: Sequence[tuple[int, int]] = (), room: int = LIMIT,
                only_spans: Sequence[tuple[int, int]] = ()) -> dict[str, Any]:
    """One root's matching folders, each with its score, plus whether the candidate cap cut the list.

    With ``only_spans`` the search keeps to those subtrees, and a match folds
    only into a folder of people inside them, so no result lies outside.
    """
    if not index.searchable():
        return {"results": [], "truncated": False, "needs_rescan": True}
    terms = [Term(w, index) for w in words]
    found, exact, truncated = _candidates(index, terms, room, skip_spans, only_spans)
    owners = index.people_folders() if prune else []
    if only_spans:
        owners = [o for o in owners if in_spans(only_spans, o[0])]
    folded = _fold([fid for fid, _ in found], owners) if owners else {fid: fid for fid, _ in found}
    # A folder of people scores as its best-matching folder. An exact hit holds every word, so it reads 1;
    # a near one scores the mean of its words' scores.
    best: dict[int, float] = {}
    for i, (fid, key) in enumerate(found):
        score = 1.0 if i < exact else round(sum(t.score(fid, key) for t in terms) / len(terms), 3)
        best[folded[fid]] = max(score, best.get(folded[fid], 0.0))
    people = {o[2]: o[3] for o in owners}
    results = []
    for r in index.folder_rows(sorted(best)):
        fid = int(r["id"])
        name = fold(r["name"])
        results.append({
            "root": root.key, "root_name": root.name, "kind": root.kind, "id": fid, "name": r["name"],
            "size": int(r["size"]), "disk": int(r["alloc"]), "files": int(r["files"]),
            "online_files": int(r["online_files"]), "people": people.get(fid, 0), "score": best[fid],
            "depth": int(r["depth"]),
            # Within one score, a word in the folder's own name ranks above one in its ancestry, then a shallower folder.
            "_rank": (sum(1 for t in terms if t.folded in name or fid in t.named), -int(r["depth"])),
        })
    return {"results": results, "truncated": truncated, "needs_rescan": False}


def _with_path(hit: dict[str, Any], root: Root, index: Index, skip: int) -> dict[str, Any]:
    names = [p["name"] for p in index.path(hit["id"])][1:]
    path = full_path(root, names)
    shown = {k: v for k, v in hit.items() if k != "_rank"}
    return {**shown, "path": path, "display": display_path(path, skip)}


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


def search(indexes: list[tuple[Root, Index]], text: str, prune: bool, skip: int, limit: int = LIMIT,
           onedrive_prevails: bool = False, within: Sequence[str] = ()) -> dict[str, Any]:
    """Every root's matches merged: the best ``limit``, the total, and the roots that need a rescan first.

    ``within`` keeps the search to those folders and everything below them
    (a root's own path keeps that whole root); a root holding none of them is
    not searched, and ``missing`` lists the paths no scanned root holds.

    With ``onedrive_prevails`` a folder inside a scanned OneDrive root is
    listed once, as that root's row, not also under the drive that holds it.
    The drive's copies are left out of the query itself, so the total, the
    candidate cap and the folding of folders of people all see the same list.
    """
    terms = parse_terms(text)
    if not terms:
        return {"results": [], "total": 0, "truncated": False, "needs_rescan": [], "missing": []}
    merged: list[tuple[dict[str, Any], Root, Index]] = []
    truncated = False
    stale = []
    held: set[str] = set()
    for root, index in indexes:
        only: Optional[list[tuple[int, int]]] = None
        if within:
            only, found_here = within_spans(root, index, within)
            held |= found_here
            if only == []:
                continue
        spans = synced_spans(root, index, indexes) if onedrive_prevails else []
        part = search_root(root, index, terms, prune, skip, spans, limit, only or ())
        merged.extend((hit, root, index) for hit in part["results"])
        truncated = truncated or part["truncated"]
        if part["needs_rescan"]:
            stale.append(root.name)
    merged.sort(key=lambda x: (-x[0]["score"], *(-v for v in x[0]["_rank"]), -x[0]["size"]))
    # Paths are rebuilt only for the results shown: each costs one lookup per level.
    shown = [_with_path(hit, root, index, skip) for hit, root, index in merged[:limit]]
    return {"results": shown, "total": len(merged), "truncated": truncated or len(merged) > limit,
            "needs_rescan": stale, "missing": [p for p in within if p not in held]}
