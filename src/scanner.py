"""Walk one root and write its index: folders, files and per-type totals, metadata only.

The walk is depth first, so folder ids come out in pre-order and every folder's
subtree is the contiguous id range ``[lo, hi]``. Every "under this folder"
question the app asks then becomes a range scan over ``folder_id``.

Files stream into the index in batches as they are listed; folders and their
totals are written once the walk ends, because a folder's subtree totals are
only known then. The index is built as ``<name>.part`` and renamed into place
only when the walk finishes, so a crash leaves no half-written index behind.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src import winfs

logger = logging.getLogger(__name__)

# Files per INSERT batch: large enough to amortise the call, small enough
# that a multi-million-file drive never holds them all in memory.
_FILE_BATCH = 20_000

# How often the progress callback fires during a walk.
_PROGRESS_SECONDS = 1.0

INDEX_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE folders (
    id INTEGER PRIMARY KEY, parent INTEGER, name TEXT NOT NULL, depth INTEGER NOT NULL,
    lo INTEGER NOT NULL, hi INTEGER NOT NULL,
    size INTEGER NOT NULL, alloc INTEGER NOT NULL, files INTEGER NOT NULL, folders INTEGER NOT NULL,
    online_files INTEGER NOT NULL, online_size INTEGER NOT NULL, errors INTEGER NOT NULL,
    hidden INTEGER NOT NULL
);
CREATE TABLE files (
    folder_id INTEGER NOT NULL, name TEXT NOT NULL, ext TEXT NOT NULL,
    size INTEGER NOT NULL, alloc INTEGER NOT NULL, attrs INTEGER NOT NULL, mtime INTEGER NOT NULL,
    hidden INTEGER NOT NULL
);
CREATE TABLE folder_ext (
    folder_id INTEGER NOT NULL, ext TEXT NOT NULL,
    size INTEGER NOT NULL, alloc INTEGER NOT NULL, files INTEGER NOT NULL,
    online_files INTEGER NOT NULL, online_size INTEGER NOT NULL
);
CREATE TABLE folder_paths (id INTEGER PRIMARY KEY, lpath TEXT NOT NULL);
CREATE TABLE folder_people (id INTEGER PRIMARY KEY, people INTEGER NOT NULL);
"""

# A folder named like an email address is one person's folder; a folder
# holding such folders is a "folder of people", which folder search can show
# as one result. A dotted top-level domain keeps names like "cl@ve" out.
PERSON_FOLDER_RE = re.compile(r"^[^@\s\\/]+@[^@\s\\/]+\.[A-Za-z]{2,}$")

# The ``hidden`` column of folders and files. A hidden folder hides what it
# holds, so an item inside one is left out with it even without the attribute.
VISIBLE = 0
HIDDEN_ITSELF = 1
HIDDEN_INSIDE = 2


def hidden_state(attrs: int, parent_hidden: int) -> int:
    """The ``hidden`` value of an item with ``attrs`` in a folder whose own value is ``parent_hidden``."""
    if winfs.is_hidden(attrs):
        return HIDDEN_ITSELF
    return HIDDEN_INSIDE if parent_hidden != VISIBLE else VISIBLE


def exclude_key(path: str) -> str:
    """How an excluded folder is compared: absolute, case-folded, no trailing separator."""
    return os.path.normcase(os.path.abspath(path)).rstrip("\\/")


def exclude_keys(paths: list[str]) -> frozenset[str]:
    return frozenset(exclude_key(os.path.expandvars(os.path.expanduser(p))) for p in paths)


def search_key(path: str) -> str:
    """How folder search compares a path: case-folded, with ``/`` as the one separator."""
    return path.casefold().replace("\\", "/")

INDEX_INDEXES = """
CREATE INDEX folders_parent ON folders(parent);
CREATE INDEX files_folder ON files(folder_id);
CREATE INDEX files_size ON files(size);
CREATE INDEX files_alloc ON files(alloc);
CREATE INDEX folder_ext_ext ON folder_ext(ext, folder_id);
CREATE INDEX folder_ext_folder ON folder_ext(folder_id);
"""


def extension(name: str) -> str:
    """Lower-case extension without the dot, or ``""``; dot-files have none."""
    stem, dot, ext = name.rpartition(".")
    return ext.lower() if dot and stem and len(ext) <= 16 else ""


@dataclass
class Counters:
    """Running totals, reported as progress and stored with the scan."""

    folders: int = 0
    files: int = 0
    size: int = 0
    alloc: int = 0
    online_files: int = 0
    online_size: int = 0
    skipped_links: int = 0
    errors: int = 0
    # Folders left out because the settings exclude them.
    excluded: int = 0

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


@dataclass
class _Folders:
    """Per-folder columns, one list each, indexed by folder id."""

    parent: list[int] = field(default_factory=list)
    name: list[str] = field(default_factory=list)
    depth: list[int] = field(default_factory=list)
    size: list[int] = field(default_factory=list)
    alloc: list[int] = field(default_factory=list)
    files: list[int] = field(default_factory=list)
    online_files: list[int] = field(default_factory=list)
    online_size: list[int] = field(default_factory=list)
    errors: list[int] = field(default_factory=list)
    hidden: list[int] = field(default_factory=list)
    # Folder id -> how many of its direct subfolders are named like an email address.
    people: dict[int, int] = field(default_factory=dict)

    def add(self, parent: int, name: str, depth: int, hidden: int = VISIBLE) -> int:
        self.parent.append(parent)
        self.name.append(name)
        self.depth.append(depth)
        self.hidden.append(hidden)
        for col in (self.size, self.alloc, self.files, self.online_files, self.online_size, self.errors):
            col.append(0)
        return len(self.parent) - 1


Lister = Callable[[str], Iterator[winfs.Entry]]


def _open_index(path: Path) -> sqlite3.Connection:
    if path.exists():
        path.unlink()
    # runtime-write-exempt: the caller passes an index path under store.index_dir(), in the runtime-data folder
    conn = sqlite3.connect(str(path))
    # The file is private until it is renamed into place, so durability
    # mid-build buys nothing; a crash just leaves a .part to delete.
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.executescript(INDEX_SCHEMA)
    return conn


def _write_paths(conn: sqlite3.Connection, f: _Folders, root_path: str) -> None:
    """Each folder's full path as folder search compares it, plus the folders of people."""
    keys = [search_key(root_path).rstrip("/")]
    for i in range(1, len(f.parent)):
        keys.append(keys[f.parent[i]] + "/" + f.name[i].casefold())
    conn.executemany("INSERT INTO folder_paths VALUES (?, ?)", enumerate(keys))
    conn.executemany("INSERT INTO folder_people VALUES (?, ?)", f.people.items())


def _write_folders(conn: sqlite3.Connection, f: _Folders) -> None:
    n = len(f.parent)
    hi = list(range(n))
    tot = {k: list(getattr(f, k)) for k in ("size", "alloc", "files", "online_files", "online_size", "errors")}
    sub_folders = [0] * n
    # Children always have larger ids than their parent, so one descending
    # pass rolls every subtree up into its parent.
    for i in range(n - 1, 0, -1):
        p = f.parent[i]
        if hi[i] > hi[p]:
            hi[p] = hi[i]
        sub_folders[p] += sub_folders[i] + 1
        for k, col in tot.items():
            col[p] += col[i]
    conn.executemany(
        "INSERT INTO folders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            (i, f.parent[i] if i else None, f.name[i], f.depth[i], i, hi[i], tot["size"][i], tot["alloc"][i],
             tot["files"][i], sub_folders[i], tot["online_files"][i], tot["online_size"][i], tot["errors"][i],
             f.hidden[i])
            for i in range(n)
        ),
    )


class _Walk:
    """One walk's state: the folder columns, the pending rows and the counters."""

    def __init__(self, conn: sqlite3.Connection, lister: Lister, exclude: frozenset[str] = frozenset()) -> None:
        self.conn = conn
        self.lister = lister
        self.exclude = exclude
        self.counters = Counters()
        self.folders = _Folders()
        self.batch: list[tuple[int, str, str, int, int, int, int, int]] = []
        self.ext_rows: list[tuple[int, str, int, int, int, int, int]] = []

    def flush(self) -> None:
        self.conn.executemany("INSERT INTO files VALUES (?,?,?,?,?,?,?,?)", self.batch)
        self.conn.executemany("INSERT INTO folder_ext VALUES (?,?,?,?,?,?,?)", self.ext_rows)
        self.batch.clear()
        self.ext_rows.clear()

    def visit(self, path: str, parent: int, name: str, depth: int,
              hidden: int = VISIBLE) -> list[tuple[str, int, str, int, int]]:
        """List one folder, record its files, and return its subfolders to walk."""
        f, c = self.folders, self.counters
        fid = f.add(max(parent, 0), name, depth, hidden)
        c.folders += 1
        try:
            entries = list(self.lister(path))
        except OSError as exc:
            if fid == 0:
                raise FileNotFoundError("cannot list the root: " + str(exc)) from exc
            f.errors[fid] += 1
            c.errors += 1
            return []
        per_ext: dict[str, list[int]] = {}
        subdirs = []
        for e in entries:
            if winfs.is_link(e):
                c.skipped_links += 1
            elif e.is_dir:
                if self.exclude and exclude_key(os.path.join(path, e.name)) in self.exclude:
                    c.excluded += 1
                else:
                    subdirs.append((e.name, hidden_state(e.attrs, hidden)))
            else:
                self._add_file(fid, e, per_ext)
        people = sum(1 for sub, _ in subdirs if PERSON_FOLDER_RE.match(sub))
        if people:
            f.people[fid] = people
        c.files += f.files[fid]
        c.size += f.size[fid]
        c.alloc += f.alloc[fid]
        c.online_files += f.online_files[fid]
        c.online_size += f.online_size[fid]
        self.ext_rows.extend((fid, ext, *a) for ext, a in per_ext.items())
        if len(self.batch) >= _FILE_BATCH:
            self.flush()
        # Reversed so the first-listed child is walked first; the order only
        # affects ids, never totals.
        return [(os.path.join(path, sub), fid, sub, depth + 1, h) for sub, h in reversed(subdirs)]

    def _add_file(self, fid: int, e: winfs.Entry, per_ext: dict[str, list[int]]) -> None:
        f = self.folders
        ext = extension(e.name)
        self.batch.append((fid, e.name, ext, e.size, e.alloc, e.attrs, e.mtime, hidden_state(e.attrs, f.hidden[fid])))
        f.size[fid] += e.size
        f.alloc[fid] += e.alloc
        f.files[fid] += 1
        agg = per_ext.setdefault(ext, [0, 0, 0, 0, 0])
        agg[0] += e.size
        agg[1] += e.alloc
        agg[2] += 1
        if winfs.is_online_only(e.attrs):
            f.online_files[fid] += 1
            f.online_size[fid] += e.size
            agg[3] += 1
            agg[4] += e.size


def scan_to_index(
    root_path: str,
    index_path: Path,
    *,
    root_name: str = "",
    lister: Lister = winfs.list_dir,
    exclude: frozenset[str] = frozenset(),
    on_progress: Optional[Callable[[dict[str, int]], None]] = None,
) -> Counters:
    """Walk ``root_path`` and write its complete index to ``index_path``.

    Raises ``FileNotFoundError`` when the root itself cannot be listed; a
    folder below it that cannot be listed (access denied, vanished mid-walk)
    is counted in ``errors`` and the walk goes on. Junctions, symlinks and
    mount points are counted in ``skipped_links`` and never followed. A folder
    whose ``exclude_key`` is in ``exclude`` is counted in ``excluded`` and not walked.
    ``root_name`` labels the top folder (the root's display name).
    """
    part = index_path.with_name(index_path.name + ".part")
    conn = _open_index(part)
    walk = _Walk(conn, lister, exclude)
    started = last_report = time.monotonic()
    stack: list[tuple[str, int, str, int, int]] = [(root_path, -1, root_name or root_path, 0, VISIBLE)]
    try:
        while stack:
            stack.extend(walk.visit(*stack.pop()))
            now = time.monotonic()
            if on_progress is not None and now - last_report >= _PROGRESS_SECONDS:
                last_report = now
                on_progress(walk.counters.as_dict())
        walk.flush()
        _write_folders(conn, walk.folders)
        _write_paths(conn, walk.folders, root_path)
        conn.executescript(INDEX_INDEXES)
        conn.executemany(
            "INSERT INTO meta VALUES (?, ?)",
            [("root_path", root_path), ("scanned_at", str(time.time())),
             ("duration_s", "%.2f" % (time.monotonic() - started))],
        )
        conn.execute("ANALYZE")
        conn.commit()
    except BaseException:
        conn.close()
        part.unlink(missing_ok=True)
        raise
    conn.close()
    # must-use-exempt: renames an index SQLite built and closed in place; there is no text to write
    os.replace(part, index_path)
    return walk.counters
