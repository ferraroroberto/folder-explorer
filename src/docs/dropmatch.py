"""Which file on this PC a dropped document is, when the scan does not have it exactly.

A drop gives the page the file's bytes, name, size and modified time, never its
path; the page also sends a SHA-256 of the bytes. The lookup, in order:

1. the one indexed file of that name, size and modified time (the index only);
2. an indexed file of that name edited since the scan: each is read through the
   reader (a fresh listing, the size and cloud checks) and hashed;
3. a file the scan never saw: a fresh listing of the scanned folders, those that
   hold documents first (newest first), until one holds the name; its bytes are
   hashed too, and it is given a lasting number (``store.remember_found``).

Exactly one equal file opens. Every candidate comes from the index or a listing
of a folder the index names, so none can leave a configured root, and a
cloud-only file is never downloaded to compare. Steps 2 and 3 share one budget of
time and folders. The outcome is ``{match}`` or ``{match: None, why}`` with ``why``
one of ``several`` (more than one equal file), ``not_found`` (none, or the budget ran out) or
``not_confirmed`` (a cloud-only candidate, no hash, or a caller off this PC).
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any, Optional, Sequence

from src import store
from src.docs import listing, reader
from src.folder_search import full_path, item_path

logger = logging.getLogger(__name__)

# A phone-sized wait for the one search, and the folders one search may list; both are logged when reached.
LOOKUP_SECONDS = 4.0
LOOKUP_FOLDERS = 3000

_clock = time.monotonic


class _Budget:
    """The time and folder allowance of one lookup."""

    def __init__(self) -> None:
        self.started = _clock()
        self.folders = 0
        self.cloud_only = 0
        self.candidates = 0

    def spent(self) -> bool:
        return self.folders >= LOOKUP_FOLDERS or _clock() - self.started >= LOOKUP_SECONDS

    def elapsed_ms(self) -> int:
        return int((_clock() - self.started) * 1000)


def _same(path: str, size: int, digest: str, budget: _Budget) -> bool:
    """Whether the document at ``path`` holds exactly the dropped bytes; a cloud-only one is counted, never read."""
    budget.candidates += 1
    try:
        data, _mtime = reader.read_document_listed(path, False, False, cap=size)
    except reader.Refused as exc:
        if exc.reason == "cloud_only":
            budget.cloud_only += 1
        return False
    return hashlib.sha256(data).hexdigest() == digest


def _key(path: str) -> str:
    """One physical file under any root that lists it (a synced library sits on a local drive too)."""
    return os.path.normcase(os.path.normpath(path))


def _edited(sources: Sequence[listing.Source], name: str, size: int, digest: str,
            budget: _Budget) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Step 2: the equal files among the indexed ones of that name, by path, and the folders they were looked for in."""
    equal: dict[str, dict[str, Any]] = {}
    looked: set[str] = set()
    for root, scan, index, lock in sources:
        with lock:
            found = [(h["id"], item_path(root, index, "file", h["id"])) for h in index.files_called(name)]
        for item_id, path in found:
            key = _key(path)
            looked.add(os.path.dirname(key))
            if key in equal or budget.spent():
                continue
            if _same(path, size, digest, budget):
                equal[key] = {"root": root.key, "scan_id": scan["id"], "id": item_id}
    return equal, looked


def _folder_order(sources: Sequence[listing.Source]) -> list[tuple[int, int]]:
    """``(source, folder id)`` in the order to list them: folders with documents newest first, then the rest as scanned."""
    withdocs: list[tuple[int, int, int]] = []
    for at, (_root, _scan, index, lock) in enumerate(sources):
        with lock:
            withdocs.extend((-mtime, at, fid) for fid, mtime in index.doc_folders(listing.EXTS))
    withdocs.sort()
    order = [(at, fid) for _m, at, fid in withdocs]
    for at, (_root, _scan, index, lock) in enumerate(sources):
        with lock:
            order.extend((at, fid) for fid in index.folders_without(listing.EXTS, LOOKUP_FOLDERS))
    return order


def _unscanned(sources: Sequence[listing.Source], name: str, size: int, digest: str, looked: set[str],
               budget: _Budget) -> dict[str, tuple[int, int, str]]:
    """Step 3: the equal files found by listing scanned folders, by path, as ``(source, folder id, name as listed)``."""
    equal: dict[str, tuple[int, int, str]] = {}
    seen = set(looked)
    for at, fid in _folder_order(sources):
        if budget.spent() or len(equal) > 1:
            break
        root, _scan, index, lock = sources[at]
        with lock:
            folder = full_path(root, [p["name"] for p in index.path(fid)][1:])
        key = os.path.normcase(os.path.normpath(folder))
        if key in seen:
            continue
        seen.add(key)
        budget.folders += 1
        entry = reader.find_document(folder, name)
        if entry is None or entry.size != size:
            continue
        path = os.path.join(folder, entry.name)
        if _same(path, size, digest, budget):
            equal[_key(path)] = (at, fid, entry.name)
    return equal


def _remembered(sources: Sequence[listing.Source], at: int, fid: int, name: str) -> dict[str, Any]:
    """A file the scan missed, by the lasting negative id of its scanned folder and name."""
    root, scan, index, lock = sources[at]
    with lock:
        hit = index.find_file(fid, name)
    item_id = hit["id"] if hit is not None else -store.remember_found(root.key, scan["id"], fid, name)
    return {"root": root.key, "scan_id": scan["id"], "id": item_id}


def _done(budget: _Budget, name: str, outcome: str, step: str) -> None:
    """The one log line a lookup leaves: what it answered, by which step, and what it cost (never a name or a path)."""
    logger.info("ℹ️ doc drop lookup: a %s file, %s (%s), %d candidates read, %d folders listed, %d cloud-only skipped, %d ms",
                os.path.splitext(name)[1].lower(), outcome, step, budget.candidates, budget.folders, budget.cloud_only,
                budget.elapsed_ms())


def lookup(sources: Sequence[listing.Source], name: str, size: int, modified_ms: int, digest: Optional[str],
           on_pc: bool) -> dict[str, Any]:
    """The indexed or found document a dropped file is: ``{match: {root, scan_id, id}}`` or ``{match: None, why}``.

    ``digest`` is the page's SHA-256 (lower-case hex) of the dropped bytes, ``on_pc`` whether the
    caller is this PC; without both only the index is used and nothing on disk is read.
    """
    budget = _Budget()
    exact = listing.dropped_matches(sources, name, size, modified_ms)
    if len(exact) == 1:
        _done(budget, name, "match", "index")
        return {"match": exact[0]}
    if not (digest and on_pc):
        why = "several" if exact else "not_confirmed"
        _done(budget, name, why, "index only")
        return {"match": None, "why": why}
    equal, looked = _edited(sources, name, size, digest, budget)
    if equal:
        found = list(equal.values())
        outcome = {"match": found[0]} if len(found) == 1 else {"match": None, "why": "several"}
        _done(budget, name, "match" if len(found) == 1 else "several", "edited")
        return outcome
    new = _unscanned(sources, name, size, digest, looked, budget)
    if len(new) == 1:
        _done(budget, name, "match", "unscanned")
        return {"match": _remembered(sources, *next(iter(new.values())))}
    why = "several" if new else ("not_confirmed" if budget.cloud_only else "not_found")
    _done(budget, name, why, "unscanned")
    return {"match": None, "why": why}
