"""The email stage: bring the email index in line with the manifest, reading only what changed.

For each ``.msg`` the manifest lists, by path:

- the index has it with the same size and modified time, and the newer fields
  filled: skip it, the file is never touched;
- the index has it but older fields only (indexed before To/Cc, attachments
  and size were read): read it once more, the one forced re-read;
- new or changed and on this PC: read it;
- new or changed and online-only: read it (which downloads it) while this
  run's download budget lasts (no budget: always), else count it "waiting for
  budget";
- it failed before with the same size and modified time: skip it, so one bad
  file never downloads every night.

The run first decides every file's verdict (no file is opened), so it knows
how many files need a read (``to_index``) before the first read and can report
``done`` against it while it works.

Before any of that, a stored preview that still carries Outlook's link or image
markup is cleaned from its own text, without opening a file.

A row whose file the manifest no longer lists is purged, but only under a
folder whose manifest is complete: a listing that failed halfway proves
nothing is gone.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from src.email.manifest import Manifest, MsgFile
from src.email.preview import clean_preview
from src.email.reader import ReadError, read_facts
from src.email.settings import is_under

logger = logging.getLogger(__name__)

# Characters of the body kept as the indexed preview, as the older scanner did.
PREVIEW_CHARS = 500
# Rows written per commit, so a long run shows progress and a crash loses little.
COMMIT_EVERY = 200
# The fewest seconds between two progress reports, so a fast run is not one write per file.
PROGRESS_EVERY_S = 1.0
# Listing mtimes are whole seconds, the older scanner stored stat()'s float.
_MTIME_SLACK_S = 1.0

Reader = Callable[[str, int], dict[str, Any]]
# Told the run's counts so far while the stage works (see ``Stage.on_progress``).
Progress = Callable[[dict[str, int]], None]

_FIELDS = ("subject", "sender", "recipients", "to_addrs", "cc_addrs", "date_sent", "body_preview", "flag_status",
           "message_id", "attachment_names", "has_attachments")


@dataclass
class Counts:
    """One run's outcome, each figure on its own so the Scans tab can show them apart."""

    files: int = 0
    # Files that need a read (new, changed, re-read) and how many of those are settled
    # (read, failed or waiting for the budget): the progress bar is done / to_index.
    to_index: int = 0
    done: int = 0
    new: int = 0
    changed: int = 0
    reread: int = 0
    unchanged: int = 0
    downloaded: int = 0
    downloaded_bytes: int = 0
    waiting: int = 0
    waiting_bytes: int = 0
    failed: int = 0
    skipped_failed: int = 0
    purged: int = 0
    cleaned: int = 0

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


def _same(stored: float, listed: int) -> bool:
    return abs(float(stored) - listed) < _MTIME_SLACK_S


class Stage:
    """One run over an open writer connection."""

    def __init__(self, conn: sqlite3.Connection, budget_bytes: Optional[int], read: Reader = read_facts,
                 on_progress: Optional[Progress] = None) -> None:
        self.conn = conn
        # None: no limit on what one run downloads.
        self.budget = budget_bytes
        self.read = read
        # Called with the counts so far at most every PROGRESS_EVERY_S, and at the start and end of the reads.
        self.on_progress = on_progress
        self.counts = Counts()
        self._pending = 0
        self._last_report = 0.0

    def _known(self) -> dict[str, tuple[float, Optional[int], bool]]:
        return {r["file_path"]: (float(r["file_mtime"]), r["size"], r["to_addrs"] is not None)
                for r in self.conn.execute("SELECT file_path, file_mtime, size, to_addrs FROM emails")}

    def _failures(self) -> dict[str, tuple[int, float]]:
        return {r["file_path"]: (int(r["size"]), float(r["file_mtime"]))
                for r in self.conn.execute("SELECT file_path, size, file_mtime FROM email_failures")}

    def _verdict(self, f: MsgFile, known: dict[str, tuple[float, Optional[int], bool]],
                 failures: dict[str, tuple[int, float]]) -> Optional[str]:
        """Why ``f`` needs a read (``new``, ``changed``, ``reread``), or None to leave it."""
        k = known.get(f.path)
        if k is not None and _same(k[0], f.mtime) and (k[1] is None or k[1] == f.size):
            if k[2]:
                return None
            why = "reread"
        else:
            why = "new" if k is None else "changed"
        failed = failures.get(f.path)
        if failed is not None and failed[0] == f.size and _same(failed[1], f.mtime):
            self.counts.skipped_failed += 1
            return None
        return why

    def _write(self, f: MsgFile, facts: dict[str, Any]) -> None:
        row = {k: facts.get(k) for k in _FIELDS}
        cols = ["file_path", "folder_path", "filename", "file_mtime", "size", *_FIELDS]
        vals = [f.path, os.path.dirname(f.path), os.path.basename(f.path), float(f.mtime), f.size, *row.values()]
        self.conn.execute(
            "INSERT INTO emails (%s, indexed_at) VALUES (%s, datetime('now')) ON CONFLICT(file_path) DO UPDATE SET %s, "
            "indexed_at = datetime('now')" % (", ".join(cols), ", ".join("?" * len(cols)),
                                              ", ".join("%s = excluded.%s" % (c, c) for c in cols[1:])),
            vals,
        )
        self.conn.execute("DELETE FROM email_failures WHERE file_path = ?", (f.path,))

    def _fail(self, f: MsgFile, reason: str) -> None:
        self.counts.failed += 1
        self.conn.execute("INSERT OR REPLACE INTO email_failures (file_path, size, file_mtime, reason) VALUES (?, ?, ?, ?)",
                          (f.path, f.size, float(f.mtime), reason))
        logger.info("ℹ️ email stage: %s not indexed: %s", os.path.basename(f.path), reason)

    def _tick(self) -> None:
        self._pending += 1
        if self._pending >= COMMIT_EVERY:
            self.conn.commit()
            self._pending = 0

    def _one(self, f: MsgFile, why: str) -> None:
        if f.online and self.budget is not None and self.counts.downloaded_bytes + f.size > self.budget:
            self.counts.waiting += 1
            self.counts.waiting_bytes += f.size
            return
        try:
            facts = self.read(f.path, PREVIEW_CHARS)
        except ReadError as exc:
            self._fail(f, exc.code + ": " + str(exc))
            self._tick()
            return
        if f.online:
            self.counts.downloaded += 1
            self.counts.downloaded_bytes += f.size
        setattr(self.counts, why, getattr(self.counts, why) + 1)
        self._write(f, facts)
        self._tick()

    def _tidy_stored(self) -> None:
        """Clean the stored preview of every row that still carries link or image markup, from the stored text alone.

        Rows indexed before ``clean_preview`` existed hold Outlook's ``<url>`` and ``[image]`` tokens. Re-reading
        each file would download every online-only mail, so the row's own text is cleaned in place: no file is
        opened, and the FTS mirror follows through the update trigger. Only rows the cleaner changes are written.
        """
        rows = self.conn.execute(
            "SELECT id, body_preview FROM emails WHERE body_preview LIKE '%<%' OR body_preview LIKE '%[%'").fetchall()
        for row in rows:
            tidy = clean_preview(row["body_preview"])
            if tidy != row["body_preview"]:
                self.conn.execute("UPDATE emails SET body_preview = ? WHERE id = ?", (tidy, row["id"]))
                self.counts.cleaned += 1
        self.conn.commit()
        if self.counts.cleaned:
            logger.info("ℹ️ email stage: cleaned the stored preview of %d mail(s), no file opened", self.counts.cleaned)

    def _report(self, force: bool = False) -> None:
        now = time.monotonic()
        if self.on_progress is None or not (force or now - self._last_report >= PROGRESS_EVERY_S):
            return
        self._last_report = now
        self.on_progress(self.counts.as_dict())

    def run(self, manifests: list[Manifest]) -> Counts:
        self._tidy_stored()
        known, failures = self._known(), self._failures()
        listed: set[str] = set()
        todo: list[tuple[MsgFile, str]] = []
        for m in manifests:
            for f in m.files:
                self.counts.files += 1
                listed.add(f.path)
                why = self._verdict(f, known, failures)
                if why is None:
                    self.counts.unchanged += f.path in known
                    continue
                todo.append((f, why))
        self.counts.to_index = len(todo)
        self._report(force=True)
        for f, why in todo:
            self._one(f, why)
            self.counts.done += 1
            self._report()
        self._report(force=True)
        for m in manifests:
            if m.complete:
                self._purge(m.folder, known, listed)
        self.conn.commit()
        return self.counts

    def _purge(self, folder: str, known: dict[str, Any], listed: set[str]) -> None:
        gone = [p for p in known if p not in listed and is_under(p, folder)]
        for path in gone:
            self.conn.execute("DELETE FROM emails WHERE file_path = ?", (path,))
            self.conn.execute("DELETE FROM email_failures WHERE file_path = ?", (path,))
        self.counts.purged += len(gone)
