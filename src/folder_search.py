"""Folder search over every root's current index, in tiers, so a wrong or misspelled word still finds folders.

A query is split on spaces and ``;`` into words. A word matches a folder
when a word of its full path (case and accents folded) starts with it, so
``alpha budget`` finds ``...\\Alpha\\2025\\Budget`` where the ancestry
supplies one word and the folder's own name the other. Results come in the
tiers of ``src.search_tiers``, never interleaved: ``exact`` (every word),
``partial`` (at least half of them), ``similar`` (at least half, counting a
word one edit off, two for a long word, ``src.folder_match``) and, only when
nothing else matched, the ``closest`` folders by name. Each tier is read
from the folder words and their subtrees' id ranges, never by a pass over
every path, and only while the tiers above leave room. Each result carries
its ``tier`` and a ``score`` from 0 to 1, the mean of its words' scores. A
folder whose subfolders are named like email addresses is a "folder of
people"; with ``prune`` on, every match at or below it folds into that one
result. ``skip`` hides leading path components from the displayed path
only; the full path is always kept for copying and opening. Two roots can
reach one folder (a OneDrive library lives on a local drive too), and it is
listed once: the synced root's row, else the first configured root's.

This is a query over the scan's index, never a walk: it reads no folder on disk.
"""

from __future__ import annotations

import bisect
import logging
import os
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Optional, Sequence

from src import search_tiers as tiers
from src import store
from src.folder_match import WORD_RE, fold, fuzzy, name_words
from src.index import MAX_NAMED, Index, open_index
from src.roots import Root, normalize, parts_within, relative_parts
from src.scanner import search_key

logger = logging.getLogger(__name__)

# Folders read per root before ranking, and results returned in all.
CANDIDATES = 5000
LIMIT = 200
# Paths read per root to check a word that is not looked up by name, so such a word stays interactive.
CHECK_READS = 50_000
# A punctuated word's candidates cost one key lookup each and no subtree, so its parts may name more than a whole
# word's: "202" starts every year and date stamp, and checking those names still costs a fraction of a path scan.
JOINED_PREFIX_WORDS = 20_000
JOINED_CANDIDATES = 100_000
# Folders a closest word contributes: a word naming thousands adds nothing a person could pick from.
CLOSEST_IDS = 20
# Sorts after any folder id, so a bisect on ``(id, NO_END)`` lands on the span starting at or before ``id``.
NO_END = 1 << 62
# The sweep's markers for the excluded and the kept ranges, apart from every word's layer.
SKIP, ONLY = -1, -2

Span = tuple[int, int]
# A match as ranked: its tier, how many words it holds exactly, and its score.
Hit = tuple[str, int, float]


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


def _listed(index: Index, names: list[str]) -> bool:
    """Whether the scan listed something in the folder ``names`` leads to.

    An empty row (a folder that was empty when scanned, or that the scan could
    not read) must not stand in for another root's copy.
    """
    fid = index.find_folder(names)
    if fid is None:
        return False
    row = index.folder(fid)
    return int(row["hi"]) > int(row["lo"]) or int(row["files"]) > 0


def _shared_span(root: Root, index: Index, other: Root, other_index: Index, onedrive_prevails: bool) -> Optional[Span]:
    """The id range in ``root``'s index of the folders ``other``'s index lists too, or ``None``."""
    names = parts_within(other.path, root.path)
    if names is not None:
        # A OneDrive library under the drive that holds it is the user's choice to see once or twice.
        chosen = bool(names) and root.kind == "local" and other.kind == "synced" and not onedrive_prevails
        return None if chosen or not _listed(other_index, []) else index.subtree_span(names)
    names = parts_within(root.path, other.path)
    return index.root_span() if names is not None and _listed(other_index, names) else None


def repeat_spans(indexes: list[tuple[Root, Index]], onedrive_prevails: bool,
                 held: dict[str, set[str]]) -> tuple[dict[str, list[Span]], list[tuple[Root, Root]]]:
    """Each root's id ranges that a preferred root lists too, and the ``(left out, kept)`` pairs behind them.

    Two roots can reach one folder (one inside the other, a junction, a share,
    a library synced under a drive). A synced root's row is preferred, else
    the first root in configured order. A root counts only when its scan is
    searchable, and only when it searches every ``held`` path (the ``within``
    folders) the other does, so no folder loses its only row. Worked out from
    the root paths and the indexes, never per match.
    """
    ranked = [entry for entry in sorted(indexes, key=lambda e: e[0].kind != "synced") if entry[1].searchable()]
    spans: dict[str, list[Span]] = {}
    pairs: list[tuple[Root, Root]] = []
    for pos, (root, index) in enumerate(ranked):
        for other, other_index in ranked[:pos]:
            if not held.get(root.key, set()) <= held.get(other.key, set()):
                continue
            span = _shared_span(root, index, other, other_index, onedrive_prevails)
            if span is not None:
                spans.setdefault(root.key, []).append(span)
                pairs.append((root, other))
    return {key: merge_spans(found) for key, found in spans.items()}, pairs


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
    """One query word as one index matches it: the folders under its words, or the spellings a path scan looks for.

    A single word (letters and digits) is looked up in the folder words: the
    folders whose own name has a word starting with it, or every folder when
    the root's own path does, and everything below them. A word joined by
    punctuation (``017-052``) is looked up the same way by the parts after a
    separator, then kept to the names holding it as typed. A word the folder
    words can't narrow, one the index names too many folders for, or an index
    from before accent folding is checked against the stored paths.
    """

    def __init__(self, word: str, index: Index) -> None:
        self.folded = fold(word)
        # An older index stores paths case-folded only, so it is searched for the word as typed and folded.
        self.exact = [self.folded] if index.folds_accents() else sorted({word, self.folded})
        self.named: set[int] = set()
        self.near: list[tuple[float, list[Span]]] = []
        self.spans: Optional[list[Span]] = None
        if index.folds_accents():
            self.spans = self._exact_spans(index) if self.folded.isalnum() else self._joined_spans(index)

    def _exact_spans(self, index: Index) -> Optional[list[Span]]:
        if any(w.startswith(self.folded) for w in name_words(index.root_path())):
            return [index.root_span()]
        words = index.prefix_words(self.folded)
        found = index.word_spans(words, MAX_NAMED) if words is not None else None
        if found is None:
            return None
        self.named, spans = found
        return spans

    def _joined_spans(self, index: Index) -> Optional[list[Span]]:
        """The subtrees of the folders whose own name holds this punctuated word; ``None`` when no part narrows them.

        A part after a separator starts a word of the name holding it, a whole
        word when a separator follows it too, so the folder words name the only
        candidates and just their names are read. A word holding ``/`` spans
        names, and one with no part after a separator (``c#``) has nothing to
        look up.
        """
        if "/" in self.folded:
            return None
        if self.folded in index.root_path():
            return [index.root_span()]
        candidates: Optional[set[int]] = None
        for part in WORD_RE.finditer(self.folded):
            if part.start() == 0:
                continue
            whole = part.end() < len(self.folded)
            words = [part.group()] if whole else index.prefix_words(part.group(), JOINED_PREFIX_WORDS)
            ids = None if words is None else index.word_folders(words, JOINED_CANDIDATES)
            if ids is not None:
                candidates = ids if candidates is None else candidates & ids
        if candidates is None:
            return None
        named = index.names_holding(candidates, self.folded)
        if len(named) > MAX_NAMED:
            return None
        self.named = named
        return index.subtrees(named)

    def find_near(self, index: Index) -> None:
        """Look up the folders under this word's near words; only asked when the tiers above leave room."""
        if self.spans is None or not fuzzy(self.folded):
            return
        one = index.near_words(self.folded)
        two = [w for w in tiers.two_edit_words(self.folded, index.vocabulary())
               if not w.startswith(self.folded) and w not in one]
        for words, edits in ((one, 1), (two, 2)):
            found = index.word_spans(words, MAX_NAMED) if words else None
            if found is not None:
                self.named |= found[0]
                self.near.append((tiers.edit_score(self.folded, edits), found[1]))


def _runs(terms: list[Term], near: bool, skip_spans: Sequence[Span],
          only_spans: Sequence[Span]) -> Iterator[tuple[int, int, int, int, float]]:
    """``(lo, hi, matched, near, score)`` of each id run in which every folder holds the same looked-up words.

    A sweep over the terms' subtree ranges, so no path is read: ``matched``
    words are held exactly, ``near`` more only through a near word, and
    ``score`` sums their scores. Runs inside ``skip_spans``, or outside
    ``only_spans`` when it is given, are left out.
    """
    layers: list[tuple[int, float]] = []
    events: list[tuple[int, int, int]] = []
    for i, t in enumerate(terms):
        if t.spans is None:
            continue
        for score, spans in [(1.0, t.spans)] + (t.near if near else []):
            layers.append((i, score))
            for lo, hi in spans:
                events += [(lo, 1, len(layers) - 1), (hi + 1, -1, len(layers) - 1)]
    for layer, spans in ((SKIP, skip_spans), (ONLY, only_spans)):
        for lo, hi in spans:
            events += [(lo, 1, layer), (hi + 1, -1, layer)]
    events.sort()
    count = [0] * len(layers)
    skip = only = 0
    for k, (pos, step, layer) in enumerate(events):
        if layer == SKIP:
            skip += step
        elif layer == ONLY:
            only += step
        else:
            count[layer] += step
        end = events[k + 1][0] if k + 1 < len(events) else pos
        if end == pos or skip or (only_spans and not only):
            continue
        best: dict[int, float] = {}
        for c, (i, score) in zip(count, layers):
            if c and score > best.get(i, 0.0):
                best[i] = score
        if best:
            exact = sum(1 for s in best.values() if s == 1.0)
            yield pos, end - 1, exact, len(best) - exact, sum(best.values())


def _tiered(index: Index, terms: list[Term], room: int, skip_spans: Sequence[Span],
            only_spans: Sequence[Span]) -> tuple[dict[int, Hit], bool]:
    """Each matching folder's ``(tier, matched, score)``, tier by tier while the ones above leave ``room``, and whether a cap cut them.

    ``exact`` and ``partial`` come from the words' own subtrees, ``similar``
    only then from their near words. A word checked against paths is read
    from the paths of the runs the others found, at most ``CHECK_READS``.
    """
    n = len(terms)
    checked = [t for t in terms if t.spans is None]
    found: dict[int, Hit] = {}
    reads = 0

    def take(runs: Iterator[tuple[int, int, int, int, float]], allowed: tuple[str, ...]) -> bool:
        """Add the runs' folders in tier order; ``False`` when a cap stopped it."""
        nonlocal reads
        # A checked word may lift each folder by one word, so a run ranks by the best its folders could reach.
        ranked = []
        for run in runs:
            tier = tiers.tier_of(run[2] + len(checked), run[3], n)
            if tier in allowed:
                ranked.append((tiers.sort_key(tier, run[2] + len(checked), run[4]), run))
        ranked.sort(key=lambda r: r[0])
        last_tier = None
        for key, (lo, hi, matched, near, score) in ranked:
            if key[0] != last_tier and len(found) >= room:
                return True
            last_tier = key[0]
            if checked:
                pairs: Iterable[tuple[int, int]] = (
                    (fid, sum(1 for t in checked if t.folded in path)) for fid, path in index.paths_in([(lo, hi)]))
            else:
                pairs = ((fid, 0) for fid in range(lo, hi + 1))
            for fid, extra in pairs:
                if checked:
                    reads += 1
                    if reads > CHECK_READS:
                        return False
                tier = tiers.tier_of(matched + extra, near, n)
                if tier in allowed and fid not in found:
                    found[fid] = (tier, matched + extra, round((score + extra) / n, 3))
                    if len(found) >= CANDIDATES:
                        return False
        return True

    if not take(_runs(terms, False, skip_spans, only_spans), (tiers.EXACT, tiers.PARTIAL)):
        return found, True
    if len(found) >= room:
        return found, False
    for t in terms:
        t.find_near(index)
    return found, not take(_runs(terms, True, skip_spans, only_spans), (tiers.SIMILAR,))


def _closest(index: Index, terms: list[Term], skip_spans: Sequence[Span],
             only_spans: Sequence[Span]) -> dict[int, Hit]:
    """The ``CLOSEST_LIMIT`` folders whose own name words look most like the query's, for when nothing else matched."""
    by_length = index.vocabulary()
    per_term: list[dict[int, float]] = []
    for t in terms:
        best: dict[int, float] = {}
        for word, similarity in tiers.closest_words(t.folded, by_length):
            for fid in index.word_ids(word)[:CLOSEST_IDS]:
                best[fid] = max(similarity, best.get(fid, 0.0))
                t.named.add(fid)
        per_term.append(best)
    skip = merge_spans(skip_spans)
    scores: dict[int, float] = {}
    for fid in {fid for best in per_term for fid in best}:
        if in_spans(skip, fid) or (only_spans and not in_spans(only_spans, fid)):
            continue
        scores[fid] = round(sum(best.get(fid, 0.0) for best in per_term) / len(terms), 3)
    top = sorted(scores, key=lambda fid: (-scores[fid], fid))[:tiers.CLOSEST_LIMIT]
    return {fid: (tiers.CLOSEST, 0, scores[fid]) for fid in top}


def _legacy(index: Index, terms: list[Term], skip_spans: Sequence[Span],
            only_spans: Sequence[Span]) -> tuple[dict[int, Hit], bool]:
    """Folders whose stored path holds every word, by a scan of the paths: when no word can be looked up by name."""
    rows, truncated = index.match_folders([t.exact for t in terms], CANDIDATES, skip_spans, only_spans)
    return {fid: (tiers.EXACT, len(terms), 1.0) for fid, _ in rows}, truncated


def search_root(root: Root, index: Index, words: list[str], prune: bool, skip: int,
                skip_spans: Sequence[Span] = (), room: int = LIMIT,
                only_spans: Sequence[Span] = (), closest: bool = False) -> dict[str, Any]:
    """One root's matching folders, each with its tier and score, plus whether a cap cut the list.

    With ``closest`` it lists the folders most like the query instead (asked
    when no root found anything). With ``only_spans`` the search keeps to
    those subtrees, and a match folds only into a folder of people inside
    them, so no result lies outside.
    """
    if not index.searchable():
        return {"results": [], "truncated": False, "needs_rescan": True}
    terms = [Term(w, index) for w in words]
    if closest:
        found, truncated = (_closest(index, terms, skip_spans, only_spans) if index.folds_accents() else {}), False
    elif any(t.spans is not None for t in terms):
        found, truncated = _tiered(index, terms, room, skip_spans, only_spans)
    else:
        found, truncated = _legacy(index, terms, skip_spans, only_spans)
    owners = index.people_folders() if prune else []
    if only_spans:
        owners = [o for o in owners if in_spans(only_spans, o[0])]
    folded = _fold(list(found), owners) if owners else {fid: fid for fid in found}
    # A folder of people ranks as its best-matching folder.
    best: dict[int, Hit] = {}
    for fid, hit in found.items():
        held = best.get(folded[fid])
        if held is None or tiers.sort_key(*hit) < tiers.sort_key(*held):
            best[folded[fid]] = hit
    people = {o[2]: o[3] for o in owners}
    results = []
    for r in index.folder_rows(sorted(best)):
        fid = int(r["id"])
        name = fold(r["name"])
        tier, matched, score = best[fid]
        results.append({
            "root": root.key, "root_name": root.name, "kind": root.kind, "id": fid, "name": r["name"],
            "size": int(r["size"]), "disk": int(r["alloc"]), "files": int(r["files"]),
            "online_files": int(r["online_files"]), "people": people.get(fid, 0), "tier": tier, "score": score,
            "depth": int(r["depth"]),
            # Within one tier and score, a word in the folder's own name ranks above one in its ancestry, then a shallower folder.
            "_rank": (*tiers.sort_key(tier, matched, score),
                      -sum(1 for t in terms if t.folded in name or fid in t.named), int(r["depth"])),
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

    A folder two roots reach is listed once (``repeat_spans``); with
    ``onedrive_prevails`` off, a scanned OneDrive folder is also listed under
    the drive that holds it. The left-out copies are excluded from the query
    itself, so the total, the candidate cap and the folding of folders of
    people all see the same list.
    """
    terms = parse_terms(text)
    if not terms:
        return {"results": [], "total": 0, "truncated": False, "needs_rescan": [], "missing": []}
    merged: list[tuple[dict[str, Any], Root, Index]] = []
    truncated = False
    stale = []
    held: dict[str, set[str]] = {}
    searched: list[tuple[Root, Index, list[Span]]] = []
    for root, index in indexes:
        only: Optional[list[Span]] = None
        if within:
            only, held[root.key] = within_spans(root, index, within)
            if only == []:
                continue
        searched.append((root, index, only or []))
    repeats, pairs = repeat_spans([(root, index) for root, index, _ in searched], onedrive_prevails, held)
    if pairs:
        logger.info("ℹ️ folder search: roots reaching the same folders, listed once: %s",
                    "; ".join("%s left out in favour of %s" % (dropped.name, kept.name) for dropped, kept in pairs))
    scopes = [(root, index, repeats.get(root.key, []), only) for root, index, only in searched]
    for root, index, spans, only in scopes:
        part = search_root(root, index, terms, prune, skip, spans, limit, only)
        merged.extend((hit, root, index) for hit in part["results"])
        truncated = truncated or part["truncated"]
        if part["needs_rescan"]:
            stale.append(root.name)
    closest = not merged
    if closest:
        # Nothing matched anywhere, so the folders most like the query are listed, labelled as closest.
        for root, index, spans, only in scopes:
            merged.extend((hit, root, index) for hit in
                          search_root(root, index, terms, prune, skip, spans, limit, only, closest=True)["results"])
    merged.sort(key=lambda x: (*x[0]["_rank"], -x[0]["size"]))
    if closest:
        merged = merged[:tiers.CLOSEST_LIMIT]
    # Paths are rebuilt only for the results shown: each costs one lookup per level.
    shown = [_with_path(hit, root, index, skip) for hit, root, index in merged[:limit]]
    return {"results": shown, "total": len(merged), "truncated": truncated or len(merged) > limit,
            "needs_rescan": stale, "missing": [p for p in within if not any(p in found for found in held.values())]}
