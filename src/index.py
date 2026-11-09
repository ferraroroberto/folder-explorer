"""Read one root's index: folder trees, contents, largest files, percentiles and file types.

Every question takes a ``basis``: ``size`` (the logical length, what the
cloud stores) or ``disk`` (the bytes allocated on this PC), and an optional
``ext`` that narrows it to one file type. Subtree questions are range scans
over the pre-order folder ids (see ``src/scanner.py``).
"""

from __future__ import annotations

import bisect
import math
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence

from src import runtime_files
from src.folder_match import edits, unpack_ids
from src.winfs import ONLINE_ONLY_ATTRS

BASES = ("size", "disk")
NO_EXT = ""

# Caps on what one response carries; a folder with 100,000 subfolders
# still answers fast and the page stays light on a phone.
MAX_CHILDREN = 300
MAX_CONTENTS = 200
MAX_TYPES = 60

# Values per ``IN (...)``, well under SQLite's bound-parameter limit.
_SQL_CHUNK = 900
# Near words kept per query word, the shortest first; each widens the paths read.
MAX_NEAR = 40
# Above every character, so ``word < prefix + _PAST_PREFIX`` ends a prefix's key range.
_PAST_PREFIX = "\U0010ffff"


def column(basis: str) -> str:
    """The files/folders column a basis sorts and sums by."""
    if basis not in BASES:
        raise ValueError("basis must be size or disk")
    return "alloc" if basis == "disk" else "size"


def open_index(path: Path) -> sqlite3.Connection:
    """A read-only connection; an index file never changes once written."""
    conn = runtime_files.connect_read_only(path, immutable=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def share(part: int, whole: int) -> float:
    return round(part * 100.0 / whole, 3) if whole else 0.0


class Index:
    """Queries over one open index connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._types: dict[tuple[int, str], dict[str, Any]] = {}
        self._knows_hidden: Optional[bool] = None
        self._folds_accents: Optional[bool] = None

    def knows_hidden(self) -> bool:
        """Whether this index marks hidden items (one written before the ``hidden`` column does not)."""
        if self._knows_hidden is None:
            cols = {r[1] for r in self.conn.execute("PRAGMA table_info(folders)").fetchall()}
            self._knows_hidden = "hidden" in cols
        return self._knows_hidden

    def _visible(self, show_hidden: bool, alias: str = "") -> str:
        """The clause that leaves hidden rows out; empty when they show or the index can't tell."""
        return "" if show_hidden or not self.knows_hidden() else " AND " + alias + "hidden = 0"

    # ------------------------------------------------------------- folders

    def folder(self, fid: int) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM folders WHERE id = ?", (fid,)).fetchone()
        if row is None:
            raise KeyError("no such folder")
        return row

    def path(self, fid: int) -> list[dict[str, Any]]:
        """The folder's ancestors from the root down, itself last."""
        out = []
        cur: Optional[int] = fid
        while cur is not None:
            row = self.conn.execute("SELECT id, parent, name FROM folders WHERE id = ?", (cur,)).fetchone()
            if row is None:
                break
            out.append({"id": row["id"], "name": row["name"]})
            cur = row["parent"]
        return out[::-1]

    def totals(self, fid: int, ext: Optional[str]) -> dict[str, int]:
        """Size, on-disk, file, folder and online-only totals of a folder's subtree."""
        f = self.folder(fid)
        if ext is None:
            return {k: int(f[k]) for k in ("size", "alloc", "files", "folders", "online_files", "online_size", "errors")}
        row = self.conn.execute(
            "SELECT COALESCE(SUM(size),0) s, COALESCE(SUM(alloc),0) a, COALESCE(SUM(files),0) n, "
            "COALESCE(SUM(online_files),0) o, COALESCE(SUM(online_size),0) os, COUNT(DISTINCT folder_id) d "
            "FROM folder_ext WHERE ext = ? AND folder_id BETWEEN ? AND ?",
            (ext, f["lo"], f["hi"]),
        ).fetchone()
        return {"size": row["s"], "alloc": row["a"], "files": row["n"], "folders": row["d"],
                "online_files": row["o"], "online_size": row["os"], "errors": int(f["errors"])}

    def node(self, row: sqlite3.Row, ext: Optional[str], extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        t = extra if extra is not None else self.totals(int(row["id"]), ext)
        return {"id": row["id"], "name": row["name"], "size": t["size"], "disk": t["alloc"], "files": t["files"],
                "online_files": t["online_files"], "has_children": bool(t.get("has_children", row["hi"] > row["lo"])),
                "hidden": int(row["hidden"]) if "hidden" in row.keys() else 0}

    def children(self, fid: int, basis: str, ext: Optional[str], show_hidden: bool = True) -> dict[str, Any]:
        """Direct subfolders, heaviest first by ``basis``; with ``ext``, only those holding that type.

        Without ``show_hidden`` a hidden folder is left out, while every total still counts what it holds.
        """
        col = column(basis)
        if ext is None:
            rows = self.conn.execute(
                "SELECT * FROM folders WHERE parent = ?" + self._visible(show_hidden) + " ORDER BY " + col + " DESC", (fid,)
            ).fetchall()
            kids = [self.node(r, None, {**dict(r), "has_children": r["hi"] > r["lo"]}) for r in rows]
        else:
            rows = self.conn.execute(
                "SELECT f.id, f.name, f.lo, f.hi, " + ("f.hidden, " if self.knows_hidden() else "")
                + "SUM(e.size) size, SUM(e.alloc) alloc, SUM(e.files) files, "
                "SUM(e.online_files) online_files, SUM(CASE WHEN e.folder_id > f.lo THEN e.files ELSE 0 END) below "
                "FROM folders f JOIN folder_ext e ON e.ext = ? AND e.folder_id BETWEEN f.lo AND f.hi "
                "WHERE f.parent = ?" + self._visible(show_hidden, "f.") + " GROUP BY f.id ORDER BY " + col + " DESC",
                (ext, fid),
            ).fetchall()
            kids = [self.node(r, ext, {**dict(r), "has_children": r["below"] > 0}) for r in rows]
        return {"children": kids[:MAX_CHILDREN], "more": max(0, len(kids) - MAX_CHILDREN)}

    # --------------------------------------------------------------- files

    def _ext_clause(self, ext: Optional[str]) -> tuple[str, tuple[Any, ...]]:
        return (" AND ext = ?", (ext,)) if ext is not None else ("", ())

    def _file(self, r: sqlite3.Row, path: Optional[list[str]] = None) -> dict[str, Any]:
        keys = r.keys()
        out = {"name": r["name"], "ext": r["ext"], "size": r["size"], "disk": r["alloc"], "mtime": r["mtime"],
               "online": bool(r["attrs"] & ONLINE_ONLY_ATTRS), "hidden": int(r["hidden"]) if "hidden" in keys else 0}
        if "id" in keys:
            out["id"] = r["id"]
        if path is not None:
            out["path"] = path
        return out

    def _rel_path(self, folder_id: int, base_id: int, cache: dict[int, list[str]]) -> list[str]:
        """Folder names from just below ``base_id`` down to ``folder_id``."""
        if folder_id == base_id:
            return []
        if folder_id in cache:
            return cache[folder_id]
        row = self.conn.execute("SELECT parent, name FROM folders WHERE id = ?", (folder_id,)).fetchone()
        result = self._rel_path(int(row["parent"]), base_id, cache) + [row["name"]] if row and row["parent"] is not None else []
        cache[folder_id] = result
        return result

    def largest(self, fid: int, n: int, basis: str, ext: Optional[str], show_hidden: bool = True) -> list[dict[str, Any]]:
        """The ``n`` largest files anywhere under the folder, each with its path below it."""
        f = self.folder(fid)
        col = column(basis)
        clause, args = self._ext_clause(ext)
        rows = self.conn.execute(
            "SELECT rowid id, * FROM files WHERE folder_id BETWEEN ? AND ?" + clause + self._visible(show_hidden)
            + " ORDER BY " + col + " DESC LIMIT ?",
            (f["lo"], f["hi"], *args, n),
        ).fetchall()
        cache: dict[int, list[str]] = {}
        return [self._file(r, self._rel_path(int(r["folder_id"]), fid, cache)) for r in rows]

    def contents(self, fid: int, basis: str, ext: Optional[str], show_hidden: bool = True) -> dict[str, Any]:
        """The folder's own subfolders and files together, biggest first, and how many of them are hidden."""
        col = column(basis)
        clause, args = self._ext_clause(ext)
        shown = clause + self._visible(show_hidden)
        kids = self.children(fid, basis, ext, show_hidden)["children"]
        files = self.conn.execute(
            "SELECT rowid id, * FROM files WHERE folder_id = ?" + shown + " ORDER BY " + col + " DESC LIMIT ?",
            (fid, *args, MAX_CONTENTS),
        ).fetchall()
        items = [{"kind": "folder", **k} for k in kids] + [{"kind": "file", **self._file(r)} for r in files]
        key = "disk" if basis == "disk" else "size"
        items.sort(key=lambda it: it[key], reverse=True)
        direct = self.conn.execute(
            "SELECT COUNT(*) FROM files WHERE folder_id = ?" + shown, (fid, *args)
        ).fetchone()[0]
        return {"items": items[:MAX_CONTENTS], "total_items": len(kids) + int(direct),
                "hidden_items": self._hidden_here(fid, ext)}

    def _hidden_here(self, fid: int, ext: Optional[str]) -> Optional[int]:
        """How many of the folder's own subfolders and files are hidden; None when the index can't tell."""
        if not self.knows_hidden():
            return None
        clause, args = self._ext_clause(ext)
        files = self.conn.execute(
            "SELECT COUNT(*) FROM files WHERE folder_id = ? AND hidden != 0" + clause, (fid, *args)
        ).fetchone()[0]
        if ext is None:
            folders = self.conn.execute(
                "SELECT COUNT(*) FROM folders WHERE parent = ? AND hidden != 0", (fid,)
            ).fetchone()[0]
        else:
            folders = self.conn.execute(
                "SELECT COUNT(DISTINCT f.id) FROM folders f JOIN folder_ext e ON e.ext = ? "
                "AND e.folder_id BETWEEN f.lo AND f.hi WHERE f.parent = ? AND f.hidden != 0",
                (ext, fid),
            ).fetchone()[0]
        return int(files) + int(folders)

    def file_row(self, file_id: int) -> sqlite3.Row:
        """One file's folder and name by its id, the ``id`` every file record carries."""
        row = self.conn.execute("SELECT folder_id, name FROM files WHERE rowid = ?", (file_id,)).fetchone()
        if row is None:
            raise KeyError("no such file")
        return row

    def percentile(self, fid: int, p: float, basis: str, ext: Optional[str]) -> dict[str, Any]:
        """How much of the folder sits in its largest ``p`` percent of files, and where those files are."""
        f = self.folder(fid)
        t = self.totals(fid, ext)
        col = column(basis)
        whole = int(t["alloc" if basis == "disk" else "size"])
        n = int(t["files"])
        if n == 0:
            return {"p": p, "files": 0, "count": 0, "bytes": 0, "whole": whole, "share": 0.0, "threshold": 0, "where": []}
        k = max(1, math.ceil(n * p / 100.0))
        clause, args = self._ext_clause(ext)
        rows = self.conn.execute(
            "SELECT folder_id, " + col + " v FROM files WHERE folder_id BETWEEN ? AND ?" + clause
            + " ORDER BY " + col + " DESC LIMIT ?",
            (f["lo"], f["hi"], *args, k),
        ).fetchall()
        kids = self.conn.execute("SELECT id, name, lo FROM folders WHERE parent = ? ORDER BY lo", (fid,)).fetchall()
        los = [int(c["lo"]) for c in kids]
        groups: dict[str, list[int]] = {}
        top_bytes = 0
        for r in rows:
            top_bytes += int(r["v"])
            pos = bisect.bisect_right(los, int(r["folder_id"])) - 1
            name = kids[pos]["name"] if pos >= 0 and int(r["folder_id"]) != fid else ""
            g = groups.setdefault(name, [0, 0])
            g[0] += 1
            g[1] += int(r["v"])
        where = sorted(({"name": name, "files": g[0], "bytes": g[1]} for name, g in groups.items()),
                       key=lambda w: w["bytes"], reverse=True)[:5]
        return {"p": p, "files": n, "count": len(rows), "bytes": top_bytes, "whole": whole,
                "share": share(top_bytes, whole), "threshold": int(rows[-1]["v"]) if rows else 0, "where": where}

    # -------------------------------------------------------- folder search

    def searchable(self) -> bool:
        """Whether this index carries folder paths (an index from before folder search does not)."""
        return self.conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'folder_paths'").fetchone() is not None

    def folds_accents(self) -> bool:
        """Whether this index stores folded paths and the name words (one written before accent folding does not)."""
        if self._folds_accents is None:
            self._folds_accents = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'folder_words'").fetchone() is not None
        return self._folds_accents

    def near_words(self, folded_word: str) -> list[str]:
        """Folder-name words one edit from ``folded_word``, or starting with such a spelling, without the word itself.

        At most ``MAX_NEAR``, the shortest first: each is a key lookup or a
        short key-range scan of ``folder_words``, never a pass over the paths.
        """
        spellings = sorted(edits(folded_word))
        found: set[str] = set()
        for start in range(0, len(spellings), _SQL_CHUNK):
            chunk = spellings[start:start + _SQL_CHUNK]
            # One statement per chunk: a key-range seek per spelling, all inside SQLite. CROSS JOIN keeps the
            # spellings outermost; left to choose, the planner may scan every word once per spelling.
            found.update(r[0] for r in self.conn.execute(
                "WITH s(v) AS (VALUES " + ",".join(["(?)"] * len(chunk)) + ") "
                "SELECT DISTINCT w.word FROM s CROSS JOIN folder_words w ON w.word >= s.v AND w.word < s.v || ?",
                (*chunk, _PAST_PREFIX)))
        return sorted((w for w in found if folded_word not in w), key=lambda w: (len(w), w))[:MAX_NEAR]

    def word_spans(self, words: list[str]) -> tuple[set[int], list[tuple[int, int]]]:
        """The folders whose own name holds one of ``words``, and their subtrees as merged ``(lo, hi)`` id ranges."""
        named: set[int] = set()
        for start in range(0, len(words), _SQL_CHUNK):
            chunk = words[start:start + _SQL_CHUNK]
            for (blob,) in self.conn.execute(
                    "SELECT ids FROM folder_words WHERE word IN (%s)" % ",".join("?" * len(chunk)), chunk):
                named.update(unpack_ids(blob))
        ids = sorted(named)
        spans: list[tuple[int, int]] = []
        for start in range(0, len(ids), _SQL_CHUNK):
            batch = ids[start:start + _SQL_CHUNK]
            rows = self.conn.execute(
                "SELECT lo, hi FROM folders WHERE id IN (%s) ORDER BY lo" % ",".join("?" * len(batch)), batch)
            for lo, hi in rows:
                if spans and lo <= spans[-1][1]:
                    spans[-1] = (spans[-1][0], max(spans[-1][1], hi))
                else:
                    spans.append((lo, hi))
        return named, spans

    def paths_in(self, spans: list[tuple[int, int]]) -> Iterator[tuple[int, str]]:
        """``(id, path)`` of every folder in the ``(lo, hi)`` ranges, in id order, read a batch of ranges at a time as asked."""
        step = _SQL_CHUNK // 2
        for start in range(0, len(spans), step):
            chunk = spans[start:start + step]
            # The spans are sorted and disjoint and CROSS JOIN keeps them outermost, so rows come in id order.
            yield from ((int(r[0]), r[1]) for r in self.conn.execute(
                "WITH s(lo, hi) AS (VALUES " + ",".join(["(?, ?)"] * len(chunk)) + ") "
                "SELECT p.id, p.lpath FROM s CROSS JOIN folder_paths p ON p.id BETWEEN s.lo AND s.hi",
                [v for span in chunk for v in span]))

    def match_folders(self, groups: list[list[str]], cap: int, skip_spans: Sequence[tuple[int, int]] = (),
                      only_spans: Sequence[tuple[int, int]] = ()) -> tuple[list[tuple[int, str]], bool]:
        """``(id, path)`` of folders whose path holds one spelling of every group, and whether ``cap`` cut them.

        Each group is one query word's stored spellings (already search-keyed).
        A folder's id is the low end of its subtree's id range, so each
        ``(lo, hi)`` in ``skip_spans`` leaves out one folder and everything below it,
        and ``only_spans``, when given, keeps just those subtrees (an id-range read, not a pass over every path).
        """
        if not groups:
            return [], False
        where = ["(" + " OR ".join(["instr(lpath, ?) > 0"] * len(g)) + ")" for g in groups]
        where += ["id NOT BETWEEN ? AND ?"] * len(skip_spans)
        if only_spans:
            where.append("(" + " OR ".join(["id BETWEEN ? AND ?"] * len(only_spans)) + ")")
        rows = self.conn.execute(
            "SELECT id, lpath FROM folder_paths WHERE " + " AND ".join(where) + " LIMIT ?",
            (*(v for g in groups for v in g), *(v for span in skip_spans for v in span),
             *(v for span in only_spans for v in span), cap + 1),
        ).fetchall()
        return [(int(r[0]), r[1]) for r in rows[:cap]], len(rows) > cap

    def people_folders(self) -> list[tuple[int, int, int, int]]:
        """``(lo, hi, id, people)`` of every folder of people that no other one contains, by ``lo``."""
        rows = self.conn.execute(
            "SELECT f.lo, f.hi, f.id, p.people FROM folder_people p JOIN folders f ON f.id = p.id ORDER BY f.lo"
        ).fetchall()
        top: list[tuple[int, int, int, int]] = []
        for r in rows:
            if not top or int(r[0]) > top[-1][1]:
                top.append((int(r[0]), int(r[1]), int(r[2]), int(r[3])))
        return top

    def folder_rows(self, ids: list[int]) -> list[sqlite3.Row]:
        if not ids:
            return []
        return self.conn.execute(
            "SELECT id, name, depth, size, alloc, files, online_files FROM folders WHERE id IN (%s)" % ",".join("?" * len(ids)),
            ids,
        ).fetchall()

    def find_folder(self, names: list[str]) -> Optional[int]:
        """The id of the folder ``names`` leads to from the root, matched case-insensitively."""
        cur = 0
        for name in names:
            row = self.conn.execute(
                "SELECT id FROM folders WHERE parent = ? AND name = ? COLLATE NOCASE", (cur, name)
            ).fetchone()
            if row is None:
                return None
            cur = int(row[0])
        return cur

    def subtree_span(self, names: list[str]) -> Optional[tuple[int, int]]:
        """The ``(lo, hi)`` id range of the folder ``names`` leads to and everything below it, or ``None`` when it isn't indexed."""
        fid = self.find_folder(names)
        if fid is None:
            return None
        row = self.folder(fid)
        return int(row["lo"]), int(row["hi"])

    def find_file(self, folder_id: int, name: str) -> Optional[dict[str, Any]]:
        """One file's indexed size and cloud state, by its folder and name."""
        row = self.conn.execute(
            "SELECT * FROM files WHERE folder_id = ? AND name = ? COLLATE NOCASE", (folder_id, name)
        ).fetchone()
        return self._file(row) if row is not None else None

    def types(self, fid: int, basis: str) -> dict[str, Any]:
        """Bytes, file count and share per extension under the folder, heaviest first."""
        key = (fid, basis)
        if key not in self._types:
            self._types[key] = self._types_uncached(fid, basis)
        return self._types[key]

    def _types_uncached(self, fid: int, basis: str) -> dict[str, Any]:
        f = self.folder(fid)
        col = column(basis)
        rows = self.conn.execute(
            "SELECT ext, SUM(size) size, SUM(alloc) alloc, SUM(files) files FROM folder_ext "
            "WHERE folder_id BETWEEN ? AND ? GROUP BY ext ORDER BY SUM(" + col + ") DESC",
            (f["lo"], f["hi"]),
        ).fetchall()
        whole = sum(int(r[col]) for r in rows)
        out = [{"ext": r["ext"], "size": r["size"], "disk": r["alloc"], "files": r["files"],
                "share": share(int(r[col]), whole)} for r in rows[:MAX_TYPES]]
        return {"types": out, "count": len(rows), "whole": whole}


class IndexPool:
    """One open :class:`Index` per index file, shared by every request.

    An index file never changes once written, so a connection can stay open
    and its cached answers never go stale. Queries on one index run one at a
    time under its lock (a sqlite connection is not safe to share between
    threads concurrently). When a root's current index moves on, the old one
    is closed so the next prune can delete its file.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._open: dict[str, tuple[Index, threading.Lock]] = {}

    def get(self, path: Path) -> tuple[Index, threading.Lock]:
        with self._lock:
            entry = self._open.get(path.name)
            if entry is None:
                entry = (Index(open_index(path)), threading.Lock())
                self._open[path.name] = entry
            return entry

    def retire(self, keep: set[str]) -> None:
        """Close every open index whose file name is not in ``keep``."""
        with self._lock:
            for name in [n for n in self._open if n not in keep]:
                index, lock = self._open.pop(name)
                with lock:
                    index.conn.close()

    def close_all(self) -> None:
        self.retire(set())
