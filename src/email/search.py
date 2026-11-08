"""Search an email index read-only: hits, facets and one mail's indexed fields.

The index is the ``emails`` table and its FTS5 mirror ``emails_fts(subject,
sender, recipients, body_preview)``. Another process may be writing it, so the
connection is ``mode=ro`` (never ``immutable``) and every answer reads a
consistent snapshot. Relevance is ``bm25(emails_fts, 10, 3, 3, 1)``: a subject
match counts most; ties go to the newest mail.

Matches come back wrapped in ``MARK_START`` / ``MARK_END`` (private-use
characters no mail contains), so the page escapes the text first and only then
turns the markers into ``<mark>``.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import date
from pathlib import Path
from typing import Any, Optional

from src import runtime_files
from src.email.query import (
    FLAG_FLAGGED,
    FTS_PREVIEW,
    FTS_SENDER,
    FTS_SUBJECT,
    Compiled,
    compile_query,
)

MARK_START, MARK_END = chr(0xE000), chr(0xE001)
SORTS = ("relevance", "newest")

# One page of hits; the page asks for the next one when the list scrolls.
PAGE = 50
MAX_PAGE = 200
# Facet chips per group.
FACETS = 5
# Words around a match in a hit's snippet.
SNIPPET_TOKENS = 18

_BM25 = "bm25(emails_fts, 10.0, 3.0, 3.0, 1.0)"
_HIT_COLUMNS = ("id", "file_path", "folder_path", "filename", "subject", "sender", "recipients", "date_sent",
                "body_preview", "flag_status", "message_id")
_OPTIONAL = ("to_addrs", "cc_addrs", "attachment_names", "has_attachments", "size")


def open_ro(path: Path) -> sqlite3.Connection:
    conn = runtime_files.connect_read_only(path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _hl(column: int) -> str:
    return "highlight(emails_fts, %d, '%s', '%s')" % (column, MARK_START, MARK_END)


def split_address(sender: str) -> tuple[str, str]:
    """``("Ana Lopez", "ana@example.com")`` from ``Ana Lopez <ana@example.com>``; either may be empty."""
    s = (sender or "").strip()
    if s.endswith(">") and "<" in s:
        name, _, addr = s[:-1].rpartition("<")
        return name.strip().strip('"') or addr.strip(), addr.strip()
    return s, s if "@" in s else ""


class EmailIndex:
    """Queries over one open email index."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.lock = threading.Lock()
        self.columns = {r["name"] for r in conn.execute("PRAGMA table_info(emails)")}
        if not {"subject", "sender", "file_path"} <= self.columns:
            raise ValueError("not an email index: the emails table is missing")
        self._select = ", ".join("e." + c for c in _HIT_COLUMNS + _OPTIONAL if c in self.columns)

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0])

    # ------------------------------------------------------------------ hits

    def _from(self, q: Compiled) -> tuple[str, list[Any]]:
        """The FROM + WHERE every hit, count and facet query shares."""
        where = list(q.where)
        args: list[Any] = []
        if q.match:
            sql = " FROM emails_fts JOIN emails e ON e.id = emails_fts.rowid WHERE emails_fts MATCH ?"
            args.append(q.match)
        else:
            sql = " FROM emails e WHERE 1"
        for clause in where:
            sql += " AND " + clause
        return sql, args + list(q.args)

    def search(self, text: str, sort: str = "relevance", offset: int = 0, limit: int = PAGE,
               today: Optional[date] = None) -> dict[str, Any]:
        """One page of hits for ``text``, the total, the facets and any notes on skipped operators."""
        if sort not in SORTS:
            raise ValueError("sort must be relevance or newest")
        q = compile_query(text, self.columns, today or date.today())
        body, args = self._from(q)
        if q.match:
            extra = ", %s AS rank, %s AS hl_subject, %s AS hl_sender, snippet(emails_fts, %d, '%s', '%s', '…', %d) AS snip" % (
                _BM25, _hl(FTS_SUBJECT), _hl(FTS_SENDER), FTS_PREVIEW, MARK_START, MARK_END, SNIPPET_TOKENS)
            order = "rank, e.date_sent DESC" if sort == "relevance" else "e.date_sent DESC, rank"
        else:
            extra, order = "", "e.date_sent DESC, e.id DESC"
        limit = max(1, min(limit, MAX_PAGE))
        try:
            rows = self.conn.execute("SELECT " + self._select + extra + body + " ORDER BY " + order + " LIMIT ? OFFSET ?",
                                     args + [limit, max(0, offset)]).fetchall()
            total = int(self.conn.execute("SELECT COUNT(*)" + body, args).fetchone()[0])
            facets = self._facets(body, args) if offset == 0 else None
        except sqlite3.OperationalError as exc:
            # A query FTS5 refuses (rare: the terms are always quoted) answers empty with the reason.
            return {"hits": [], "total": 0, "facets": None, "notes": ["the search could not run: " + str(exc)], "words": q.words}
        return {"hits": [self._hit(r, bool(q.match)) for r in rows], "total": total, "facets": facets,
                "notes": q.notes, "words": q.words}

    def _hit(self, r: sqlite3.Row, matched: bool) -> dict[str, Any]:
        keys = r.keys()
        name, addr = split_address(r["sender"] or "")
        preview = r["body_preview"] or ""
        hit = {
            "id": r["id"],
            "subject": (r["hl_subject"] if matched else r["subject"]) or "",
            "from_name": name, "from_addr": addr,
            "date": r["date_sent"] or "",
            "folder": r["folder_path"] or "",
            "path": r["file_path"],
            "snippet": (r["snip"] if matched and r["snip"] else preview[:200]) or "",
            "flagged": r["flag_status"] == FLAG_FLAGGED,
        }
        if "attachment_names" in keys:
            hit["attachments"] = len([a for a in (r["attachment_names"] or "").split("\n") if a])
        if "size" in keys:
            hit["size"] = r["size"]
        return hit

    def _facets(self, body: str, args: list[Any]) -> dict[str, Any]:
        ids = "SELECT e.id" + body
        senders = self.conn.execute(
            "SELECT sender, COUNT(*) n FROM emails WHERE id IN (" + ids + ") AND COALESCE(sender, '') <> '' "
            "GROUP BY lower(sender) ORDER BY n DESC LIMIT ?", args + [FACETS]).fetchall()
        folders = self.conn.execute(
            "SELECT folder_path, COUNT(*) n FROM emails WHERE id IN (" + ids + ") GROUP BY folder_path ORDER BY n DESC LIMIT ?",
            args + [FACETS]).fetchall()
        years = self.conn.execute(
            "SELECT substr(date_sent, 1, 4) y, COUNT(*) n FROM emails WHERE id IN (" + ids + ") AND date_sent <> '' "
            "GROUP BY y ORDER BY y DESC LIMIT ?", args + [FACETS]).fetchall()
        flagged = self.conn.execute("SELECT COUNT(*) FROM emails WHERE flag_status = ? AND id IN (" + ids + ")",
                                    [FLAG_FLAGGED] + args).fetchone()[0]
        out: dict[str, Any] = {
            "senders": [{"name": split_address(r["sender"])[0], "value": split_address(r["sender"])[1] or r["sender"],
                         "count": r["n"]} for r in senders],
            "folders": [{"value": r["folder_path"], "count": r["n"]} for r in folders],
            "years": [{"value": r["y"], "count": r["n"]} for r in years],
            "flagged": int(flagged),
        }
        if "has_attachments" in self.columns:
            out["attachments"] = int(self.conn.execute(
                "SELECT COUNT(*) FROM emails WHERE has_attachments = 1 AND id IN (" + ids + ")", args).fetchone()[0])
        return out

    # ------------------------------------------------------------- one mail

    def email(self, row_id: int, text: str = "", today: Optional[date] = None) -> dict[str, Any]:
        """One mail's indexed fields, with the query's words marked in the subject and preview."""
        r = self.conn.execute("SELECT " + self._select + " FROM emails e WHERE e.id = ?", (row_id,)).fetchone()
        if r is None:
            raise KeyError("no such email in the index")
        q = compile_query(text, self.columns, today or date.today())
        subject, preview = r["subject"] or "", r["body_preview"] or ""
        if q.match:
            marked = self.conn.execute(
                "SELECT %s, %s FROM emails_fts WHERE emails_fts MATCH ? AND rowid = ?" % (_hl(FTS_SUBJECT), _hl(FTS_PREVIEW)),
                (q.match, row_id)).fetchone()
            if marked is not None:
                subject, preview = marked[0] or subject, marked[1] or preview
        keys = r.keys()
        name, addr = split_address(r["sender"] or "")
        out = {
            "id": r["id"], "subject": subject, "from_name": name, "from_addr": addr,
            "to": r["to_addrs"] if "to_addrs" in keys else (r["recipients"] or ""),
            "cc": r["cc_addrs"] if "cc_addrs" in keys else None,
            "date": r["date_sent"] or "", "folder": r["folder_path"] or "", "path": r["file_path"],
            "filename": r["filename"] or "", "preview": preview,
            "flagged": r["flag_status"] == FLAG_FLAGGED, "message_id": r["message_id"] or "",
            "attachments": [a for a in (r["attachment_names"] or "").split("\n") if a] if "attachment_names" in keys else None,
            "size": r["size"] if "size" in keys else None,
            "words": q.words,
        }
        return out

    def path_of(self, row_id: int) -> str:
        r = self.conn.execute("SELECT file_path FROM emails WHERE id = ?", (row_id,)).fetchone()
        if r is None:
            raise KeyError("no such email in the index")
        return str(r["file_path"])


class EmailIndexHolder:
    """The process's one open email index, reopened when the configured path changes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._path: Optional[Path] = None
        self._index: Optional[EmailIndex] = None

    def get(self, path: Path) -> EmailIndex:
        with self._lock:
            if self._index is None or self._path != path:
                self.close()
                self._index = EmailIndex(open_ro(path))
                self._path = path
            return self._index

    def close(self) -> None:
        if self._index is not None:
            self._index.conn.close()
        self._index, self._path = None, None
