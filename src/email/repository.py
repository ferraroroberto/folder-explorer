"""What the batch verbs ask of the email index: lookups by Message-ID, folder rows, and folder scores.

The verbs open this app's own ``emails.sqlite3`` (``src/email/schema.py``)
through ``open_index``. Besides the email stage, two verbs write to it, and
only to keep it true about files they just changed: ``revert`` drops the row
of a ``.msg`` it deleted, and ``renumber`` points a row at the name it gave
the file. Neither indexes a new mail; that stays the stage's job.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Optional, TypeVar

from src.email.archive_settings import index_path
from src.email.schema import open_writer

_T = TypeVar("_T")

logger = logging.getLogger(__name__)

# Ceilings on one ``suggest_folders`` call; they bound the work one suggestion does.
# FTS_ROW_CAP: best-scoring FTS rows read before they are grouped by folder.
# FOLDER_POOL_CAP: floor on the best folders kept after grouping, each costing
# one sample-subjects query; the pool grows to ``max_results`` when asked for more.
FTS_ROW_CAP = 1000
FOLDER_POOL_CAP = 20

# A write still meeting "database is locked" after SQLite's own busy wait is
# retried this many more times, sleeping 1, 2, 4, ... seconds, so a write that
# overlaps the email stage queues instead of failing the verb.
LOCK_RETRIES = 5
LOCK_RETRY_BASE_S = 1.0


def open_index(cfg: dict[str, Any]) -> sqlite3.Connection:
    """This app's email index, created and migrated when missing."""
    return open_writer(index_path(cfg))


@dataclass
class EmailRecord:
    """One index row, as ``renumber`` reads a folder's rows."""

    file_path: str
    folder_path: str
    filename: str
    date_sent: str = ""
    # Internet Message-ID without angle brackets; "" means the .msg carried none.
    message_id: str = ""


@dataclass
class FolderSuggestion:
    folder_path: str
    score: float
    match_count: int
    sample_subjects: list[str] = field(default_factory=list)


def _build_fts_query(text: str) -> str:
    """Free text as a safe FTS5 MATCH: each word of 3+ letters quoted, joined with OR, at most 30."""
    tokens = re.findall(r"[A-Za-z0-9À-ɏ]{3,}", text)
    unique = list(dict.fromkeys(t.lower() for t in tokens))
    return " OR ".join('"%s"' % t for t in unique[:30])


class EmailRepository:
    """A thin repository over one connection; one per thread, as SQLite connections are not shared."""

    def __init__(self, conn: sqlite3.Connection, sleep: Callable[[float], None] = time.sleep) -> None:
        self._conn = conn
        self._sleep = sleep

    def _retry_locked(self, op: Callable[[], _T], what: str) -> _T:
        """Run a write, retrying with backoff while another writer holds the lock."""
        for attempt in range(LOCK_RETRIES + 1):
            try:
                return op()
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc) or attempt == LOCK_RETRIES:
                    raise
                delay = LOCK_RETRY_BASE_S * 2 ** attempt
                logger.info("⚠️ email index locked during %s; retry %d/%d in %.0fs",
                            what, attempt + 1, LOCK_RETRIES, delay)
                self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    def update_path(self, old_path: str, new_path: str) -> int:
        """Point one row at a renamed file; ``file_mtime`` stays, so the stage still skips it. Caller commits."""
        return self._retry_locked(lambda: self._conn.execute(
            "UPDATE emails SET file_path = ?, filename = ? WHERE file_path = ?",
            (new_path, os.path.basename(new_path), old_path)).rowcount, "update_path")

    def commit(self) -> None:
        self._retry_locked(self._conn.commit, "commit")

    def delete_by_path(self, file_path: str) -> int:
        """Drop the row of a file just deleted, so ``plan`` stops reporting it archived. Caller commits."""
        return self._retry_locked(lambda: self._conn.execute(
            "DELETE FROM emails WHERE file_path = ?", (file_path,)).rowcount, "delete_by_path")

    def find_path_by_message_id(self, message_id: str) -> Optional[str]:
        """The archived ``.msg`` for a Message-ID, the most recently indexed one; "" never matches."""
        if not message_id:
            return None
        row = self._conn.execute(
            "SELECT file_path FROM emails WHERE message_id = ? ORDER BY indexed_at DESC LIMIT 1",
            (message_id,)).fetchone()
        return row["file_path"] if row else None

    def find_in_folder(self, folder_path: str) -> list[EmailRecord]:
        """Every row filed directly in ``folder_path``, matched case-blind, with ``file_path`` as stored.

        Windows spells one folder several ways (the config's casing,
        ``resolve()``'s, a caller's), and the index holds whichever the walk
        used; a case-sensitive match would look like a folder never indexed.
        """
        rows = self._conn.execute(
            "SELECT file_path, folder_path, filename, date_sent, message_id FROM emails "
            "WHERE folder_path = ? COLLATE NOCASE", (folder_path,)).fetchall()
        return [EmailRecord(file_path=r["file_path"], folder_path=r["folder_path"], filename=r["filename"],
                            date_sent=r["date_sent"] or "", message_id=r["message_id"] or "") for r in rows]

    def folders_with_subject(self, subject: str) -> set[str]:
        """The folders holding a mail whose stored (cleaned) subject is exactly ``subject``, case-blind."""
        if not subject:
            return set()
        rows = self._conn.execute(
            "SELECT DISTINCT folder_path FROM emails WHERE subject = ? COLLATE NOCASE", (subject,)).fetchall()
        return {r["folder_path"] for r in rows}

    def suggest_folders(self, subject: str, sender: str, recipients: str, max_results: int = 3,
                        min_score: float = 0.05) -> list[FolderSuggestion]:
        """Folders ranked for an incoming mail, each scored by its single best-matching mail.

        Scoring a folder by its best match, not the sum over its mails, keeps
        volume out of the ranking: a folder with hundreds of loosely related
        mails no longer outranks the one folder holding this very thread.
        The pool is ``max(FOLDER_POOL_CAP, max_results)``.
        """
        fts_query = _build_fts_query("%s %s %s" % (subject, sender, recipients))
        if not fts_query:
            logger.warning("⚠️ empty FTS query for subject %r", subject)
            return []
        best, counts = self._score_folders(fts_query)
        if not best:
            return []
        top = max(best.values()) or 1.0
        ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)[:max(FOLDER_POOL_CAP, max_results)]
        out: list[FolderSuggestion] = []
        for folder, raw in ranked:
            score = raw / top
            if score >= min_score:
                out.append(FolderSuggestion(folder, score, counts[folder], self.sample_subjects(folder)))
        return out[:max_results]

    def count_from_sender(self, folder_path: str, sender: str) -> int:
        """How many of the folder's mails came from ``sender``, case-blind; 0 for a blank sender."""
        if not sender:
            return 0
        row = self._conn.execute("SELECT COUNT(*) FROM emails WHERE folder_path = ? AND sender = ? COLLATE NOCASE",
                                 (folder_path, sender)).fetchone()
        return int(row[0])

    def sample_subjects(self, folder_path: str) -> list[str]:
        """The folder's five newest distinct subjects."""
        rows = self._conn.execute(
            "SELECT DISTINCT subject FROM emails WHERE folder_path = ? ORDER BY date_sent DESC LIMIT 5",
            (folder_path,)).fetchall()
        return [r["subject"] for r in rows if r["subject"]]

    def _score_folders(self, fts_query: str) -> tuple[dict[str, float], dict[str, int]]:
        """Best match score and match count per folder; both empty when nothing matches.

        Two steps, not a JOIN: ``bm25()`` only resolves when the FTS table is
        the outermost table of its query. The weights are subject 10, sender
        and recipients 3, body 1; bm25 is negative, more negative is better,
        so it is inverted here.
        """
        try:
            fts_rows = self._conn.execute(
                "SELECT rowid, bm25(emails_fts, 10.0, 3.0, 3.0, 1.0) AS rank FROM emails_fts "
                "WHERE emails_fts MATCH ? ORDER BY rank LIMIT ?", (fts_query, FTS_ROW_CAP)).fetchall()
        except sqlite3.OperationalError as exc:
            logger.warning("⚠️ FTS query failed (%s) for %r", exc, fts_query)
            return {}, {}
        if not fts_rows:
            return {}, {}
        row_score = {r[0]: -r[1] for r in fts_rows}
        marks = ",".join("?" * len(row_score))
        best: dict[str, float] = {}
        counts: dict[str, int] = {}
        for row in self._conn.execute("SELECT id, folder_path FROM emails WHERE id IN (%s)" % marks, list(row_score)):
            folder = row["folder_path"]
            best[folder] = max(best.get(folder, 0.0), row_score[row["id"]])
            counts[folder] = counts.get(folder, 0) + 1
        return best, counts
