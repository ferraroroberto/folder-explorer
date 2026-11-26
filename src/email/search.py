"""Search an email index read-only: hits, facets and one mail's indexed fields.

The index is the ``emails`` table and its FTS5 mirror ``emails_fts(subject,
sender, recipients, body_preview)``. Another process may be writing it, so the
connection is ``mode=ro`` (never ``immutable``) and every answer reads a
consistent snapshot. Relevance is ``bm25(emails_fts, 10, 3, 3, 1)``: a subject
match counts most; ties go to the newest mail.

A query with fewer exact matches than one page also lists the lower tiers of
``src.search_tiers`` (``src/email/relaxed.py``), so one wrong or misspelled
word never empties the list; each hit carries its ``tier`` and ``score``.

Matches come back wrapped in ``MARK_START`` / ``MARK_END`` (private-use
characters no mail contains), so the page escapes the text first and only then
turns the markers into ``<mark>``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date
from pathlib import Path
from typing import Any, Optional

from src import runtime_files
from src import search_tiers as tiers
from src.email import relaxed
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
        # The index's words with the data version they were read at.
        self._words: Optional[tuple[int, relaxed.Vocabulary]] = None

    def vocabulary(self) -> relaxed.Vocabulary:
        """Every word the index holds, read again only after another connection has written to it."""
        version = int(self.conn.execute("PRAGMA data_version").fetchone()[0])
        if self._words is None or self._words[0] != version:
            self.conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.emails_vocab USING fts5vocab(main, emails_fts, 'row')")
            words = relaxed.Vocabulary(r[0] for r in self.conn.execute("SELECT term FROM temp.emails_vocab"))
            self._words = (version, words)
        return self._words[1]

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

    def _rows(self, match: Optional[str], body: str, args: list[Any], order: str, limit: int,
              offset: int) -> list[sqlite3.Row]:
        """The hit rows of one FROM + WHERE, with the words of ``match`` marked."""
        extra = ""
        if match:
            extra = ", %s AS rank, %s AS hl_subject, %s AS hl_sender, snippet(emails_fts, %d, '%s', '%s', '…', %d) AS snip" % (
                _BM25, _hl(FTS_SUBJECT), _hl(FTS_SENDER), FTS_PREVIEW, MARK_START, MARK_END, SNIPPET_TOKENS)
        return self.conn.execute("SELECT " + self._select + extra + body + " ORDER BY " + order + " LIMIT ? OFFSET ?",
                                 args + [limit, offset]).fetchall()

    def _tiered_page(self, q: Compiled, exact: list[sqlite3.Row], lower: relaxed.Lower, offset: int,
                     limit: int) -> list[dict[str, Any]]:
        """One page of the exact rows followed by the lower tiers' mails, each marked with the words that found it."""
        listed = {r["id"]: self._hit(r, bool(q.match)) for r in exact}
        page = ([r["id"] for r in exact] + lower.ids)[offset:offset + limit]
        wanted = [i for i in page if i not in listed]
        if wanted:
            body = (" FROM emails_fts JOIN emails e ON e.id = emails_fts.rowid WHERE emails_fts MATCH ?"
                    " AND e.id IN (SELECT value FROM json_each(?))")
            for r in self._rows(lower.match, body, [lower.match, json.dumps(wanted)], "e.id", len(wanted), 0):
                listed[r["id"]] = self._hit(r, True, *lower.hits[r["id"]])
        return [listed[i] for i in page if i in listed]

    def search(self, text: str, sort: str = "relevance", offset: int = 0, limit: int = PAGE,
               today: Optional[date] = None, facets: bool = True) -> dict[str, Any]:
        """One page of hits for ``text``, the total, the facets (first page only) and any notes on skipped operators.

        With fewer exact matches than ``limit`` the lower tiers follow them, and the total and the facets count
        what is listed; ``truncated`` says their cap cut it. Facets cost most of a broad query's time, so a
        caller that shows none passes ``facets=False``.
        """
        if sort not in SORTS:
            raise ValueError("sort must be relevance or newest")
        q = compile_query(text, self.columns, today or date.today())
        body, args = self._from(q)
        if q.match:
            order = "rank, e.date_sent DESC" if sort == "relevance" else "e.date_sent DESC, rank"
        else:
            order = "e.date_sent DESC, e.id DESC"
        limit, offset = max(1, min(limit, MAX_PAGE)), max(0, offset)
        try:
            total = int(self.conn.execute("SELECT COUNT(*)" + body, args).fetchone()[0])
            lower = relaxed.Lower()
            exact: list[sqlite3.Row] = []
            if q.loose and total < limit:
                exact = self._rows(q.match, body, args, order, limit, 0)
                lower = relaxed.lower_tiers(self.conn, q, [r["id"] for r in exact], limit, sort == "newest",
                                            self.vocabulary)
            if lower.ids:
                hits = self._tiered_page(q, exact, lower, offset, limit)
                listed = [r["id"] for r in exact] + lower.ids
                total = len(listed)
                body, args = " FROM emails e WHERE e.id IN (SELECT value FROM json_each(?))", [json.dumps(listed)]
            else:
                hits = [self._hit(r, bool(q.match)) for r in self._rows(q.match, body, args, order, limit, offset)]
            chips = self._facets(body, args) if facets and offset == 0 else None
        except sqlite3.OperationalError as exc:
            # A query FTS5 refuses (rare: the terms are always quoted) answers empty with the reason.
            return {"hits": [], "total": 0, "facets": None, "notes": ["the search could not run: " + str(exc)],
                    "words": q.words, "truncated": False}
        return {"hits": hits, "total": total, "facets": chips, "notes": q.notes, "words": q.words,
                "truncated": lower.truncated}

    def flagged(self, offset: int = 0, limit: int = PAGE) -> dict[str, Any]:
        """One page of the flagged mails, oldest first, with the count of all of them.

        Oldest first so a caller draining a backlog sees mail in arrival order, whatever is added meanwhile.
        Raises ``ValueError`` on an index without ``flag_status``: that is "can't tell", never "none flagged".
        """
        if "flag_status" not in self.columns:
            raise ValueError("the email index has no flag_status column")
        limit = max(1, min(limit, MAX_PAGE))
        where = " FROM emails e WHERE e.flag_status = ?"
        rows = self.conn.execute("SELECT " + self._select + where + " ORDER BY e.date_sent, e.id LIMIT ? OFFSET ?",
                                 (FLAG_FLAGGED, limit, max(0, offset))).fetchall()
        total = int(self.conn.execute("SELECT COUNT(*)" + where, (FLAG_FLAGGED,)).fetchone()[0])
        return {"mails": [self._flagged_mail(r) for r in rows], "total": total}

    @staticmethod
    def _flagged_mail(r: sqlite3.Row) -> dict[str, Any]:
        name, addr = split_address(r["sender"] or "")
        return {"id": r["id"], "path": r["file_path"], "filename": r["filename"] or "", "folder": r["folder_path"] or "",
                "subject": r["subject"] or "", "sender": r["sender"] or "", "from_name": name, "from_addr": addr,
                "date": r["date_sent"] or "", "preview": r["body_preview"] or ""}

    def _hit(self, r: sqlite3.Row, matched: bool, tier: str = tiers.EXACT, score: float = 1.0) -> dict[str, Any]:
        keys = r.keys()
        name, addr = split_address(r["sender"] or "")
        preview = r["body_preview"] or ""
        hit = {
            "id": r["id"],
            "subject": (r["hl_subject"] if matched else r["subject"]) or "",
            "from_name": name, "from_addr": addr,
            "recipients": r["recipients"] or "",
            "date": r["date_sent"] or "",
            "folder": r["folder_path"] or "",
            "path": r["file_path"],
            "filename": r["filename"] or "",
            "snippet": (r["snip"] if matched and r["snip"] else preview[:200]) or "",
            "flagged": r["flag_status"] == FLAG_FLAGGED,
            "tier": tier,
            "score": score,
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

    def _marked(self, row_id: int, match: str) -> Optional[sqlite3.Row]:
        """One mail's subject and preview with ``match`` marked, or ``None`` when it does not satisfy it."""
        return self.conn.execute(
            "SELECT %s, %s FROM emails_fts WHERE emails_fts MATCH ? AND rowid = ?" % (_hl(FTS_SUBJECT), _hl(FTS_PREVIEW)),
            (match, row_id)).fetchone()

    def email(self, row_id: int, text: str = "", today: Optional[date] = None) -> dict[str, Any]:
        """One mail's indexed fields, with the query's words marked in the subject and preview.

        A mail listed below the exact tier is marked with the words that found it, which ``words`` then carries too.
        """
        r = self.conn.execute("SELECT " + self._select + " FROM emails e WHERE e.id = ?", (row_id,)).fetchone()
        if r is None:
            raise KeyError("no such email in the index")
        q = compile_query(text, self.columns, today or date.today())
        subject, preview = r["subject"] or "", r["body_preview"] or ""
        words = q.words
        if q.match:
            marked = self._marked(row_id, q.match)
            if marked is None:
                for match, extra in relaxed.markings(q, self.vocabulary):
                    marked = self._marked(row_id, match)
                    if marked is not None:
                        words = q.words + extra
                        break
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
            "words": words,
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
