"""The scan history and the current index of each root, in the runtime-data folder.

``state.sqlite3`` holds one row per scan: its root, trigger, live counters
while it runs, and the totals and index file once it finishes. Each finished
scan writes its own index file (``index/<root>-<scan id>.sqlite3``); the
root's current index is the newest scan that finished ``ok``. A failed or
interrupted scan therefore never replaces a good index, and a browser
reading the previous index is never pulled out from under.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from src.runtime_data import runtime_data_dir

logger = logging.getLogger(__name__)

APP = "folder-explorer"

STATUS_RUNNING = "running"
STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_INTERRUPTED = "interrupted"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY,
    root_key TEXT NOT NULL,
    root_path TEXT NOT NULL,
    trigger TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    folders INTEGER NOT NULL DEFAULT 0,
    files INTEGER NOT NULL DEFAULT 0,
    size INTEGER NOT NULL DEFAULT 0,
    alloc INTEGER NOT NULL DEFAULT 0,
    online_files INTEGER NOT NULL DEFAULT 0,
    online_size INTEGER NOT NULL DEFAULT 0,
    skipped_links INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    index_file TEXT,
    message TEXT
);
CREATE INDEX IF NOT EXISTS scans_root ON scans(root_key, id);
CREATE TABLE IF NOT EXISTS email_runs (
    id INTEGER PRIMARY KEY,
    trigger TEXT NOT NULL,
    scope TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    counts TEXT,
    message TEXT
);
CREATE TABLE IF NOT EXISTS saved_searches (
    id INTEGER PRIMARY KEY,
    mode TEXT NOT NULL,
    name TEXT NOT NULL,
    query TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE (mode, query)
);
"""

SEARCH_MODES = ("emails", "folders")

_COUNTERS = ("folders", "files", "size", "alloc", "online_files", "online_size", "skipped_links", "errors")


def data_dir() -> Path:
    """The app's runtime-data folder, created on first use."""
    return runtime_data_dir(APP, create=True)


def index_dir() -> Path:
    path = data_dir() / "index"
    path.mkdir(parents=True, exist_ok=True)
    return path


def index_name(root_key: str, scan_id: int) -> str:
    """The index file one scan of one root writes."""
    return "%s-%d.sqlite3" % (root_key, scan_id)


def state_path() -> Path:
    return data_dir() / "state.sqlite3"


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """One short-lived connection to the state database."""
    conn = sqlite3.connect(str(state_path()), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def begin_scan(root_key: str, root_path: str, trigger: str) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO scans (root_key, root_path, trigger, status, started_at) VALUES (?, ?, ?, ?, ?)",
            (root_key, root_path, trigger, STATUS_RUNNING, time.time()),
        )
        return int(cur.lastrowid or 0)


def update_progress(scan_id: int, counters: dict[str, int]) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE scans SET " + ", ".join(c + " = ?" for c in _COUNTERS) + " WHERE id = ?",
            [int(counters.get(c, 0)) for c in _COUNTERS] + [scan_id],
        )


def finish_scan(scan_id: int, status: str, counters: dict[str, int], index_file: Optional[str], message: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE scans SET status = ?, finished_at = ?, index_file = ?, message = ?, "
            + ", ".join(c + " = ?" for c in _COUNTERS) + " WHERE id = ?",
            [status, time.time(), index_file, message] + [int(counters.get(c, 0)) for c in _COUNTERS] + [scan_id],
        )


def settle_interrupted() -> int:
    """Mark scans left ``running`` by a process that died as ``interrupted``.

    Only call while holding the scan lock: then no scan can really be running.
    """
    with connect() as conn:
        cur = conn.execute(
            "UPDATE scans SET status = ?, finished_at = ?, message = ? WHERE status = ?",
            (STATUS_INTERRUPTED, time.time(), "the scan stopped before it finished", STATUS_RUNNING),
        )
        return cur.rowcount


def _row(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
    return dict(row) if row is not None else None


def latest_scan(root_key: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        return _row(conn.execute("SELECT * FROM scans WHERE root_key = ? ORDER BY id DESC LIMIT 1", (root_key,)).fetchone())


def current_index(root_key: str) -> Optional[dict[str, Any]]:
    """The newest scan of ``root_key`` that finished ``ok`` and whose index file exists."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM scans WHERE root_key = ? AND status = ? AND index_file IS NOT NULL ORDER BY id DESC LIMIT 3",
            (root_key, STATUS_OK),
        ).fetchall()
    for row in rows:
        if (index_dir() / row["index_file"]).exists():
            return dict(row)
    return None


def last_started() -> Optional[float]:
    """When the most recent scan of any root started, or ``None`` before the first."""
    with connect() as conn:
        row = conn.execute("SELECT MAX(started_at) AS t FROM scans").fetchone()
    return float(row["t"]) if row and row["t"] is not None else None


def running_scans() -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM scans WHERE status = ? ORDER BY id", (STATUS_RUNNING,))]


def prune_indexes(keep_per_root: int = 1) -> list[str]:
    """Delete index files older than each root's current one; returns the names removed.

    A file a reader still has open cannot be deleted on Windows; it stays and
    goes on the next prune. A running scan's file is kept too: the scan puts
    its finished index in place a moment before it records it.
    """
    with connect() as conn:
        keep = {
            r["index_file"]
            for r in conn.execute(
                "SELECT index_file FROM scans s WHERE status = ? AND index_file IS NOT NULL AND id IN "
                "(SELECT id FROM scans t WHERE t.root_key = s.root_key AND t.status = ? ORDER BY id DESC LIMIT ?)",
                (STATUS_OK, STATUS_OK, keep_per_root),
            )
        }
        keep |= {index_name(r["root_key"], int(r["id"]))
                 for r in conn.execute("SELECT id, root_key FROM scans WHERE status = ?", (STATUS_RUNNING,))}
    removed = []
    for path in index_dir().glob("*.sqlite3"):
        if path.name in keep:
            continue
        try:
            path.unlink()
            removed.append(path.name)
            logger.info("ℹ️ pruned a replaced index: %s", path.name)
        except OSError as exc:
            logger.info("ℹ️ old index still in use, kept for the next prune: %s (%s)", path.name, exc)
    return removed


def saved_searches() -> list[dict[str, Any]]:
    """Every saved search, oldest first; the query text keeps its relative dates as typed."""
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT id, mode, name, query FROM saved_searches ORDER BY id")]


def save_search(mode: str, name: str, query: str) -> dict[str, Any]:
    """Save ``query`` under ``name``; saving the same query again renames it."""
    if mode not in SEARCH_MODES:
        raise ValueError("mode must be emails or folders")
    name, query = name.strip(), query.strip()
    if not query:
        raise ValueError("a saved search needs a query")
    with connect() as conn:
        conn.execute(
            "INSERT INTO saved_searches (mode, name, query, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (mode, query) DO UPDATE SET name = excluded.name",
            (mode, name or query, query, time.time()),
        )
        row = conn.execute("SELECT id, mode, name, query FROM saved_searches WHERE mode = ? AND query = ?", (mode, query)).fetchone()
    return dict(row)


def delete_search(search_id: int) -> bool:
    with connect() as conn:
        return conn.execute("DELETE FROM saved_searches WHERE id = ?", (search_id,)).rowcount > 0


def begin_email_run(trigger: str, scope: str) -> int:
    with connect() as conn:
        cur = conn.execute("INSERT INTO email_runs (trigger, scope, status, started_at) VALUES (?, ?, ?, ?)",
                           (trigger, scope, STATUS_RUNNING, time.time()))
        return int(cur.lastrowid or 0)


def finish_email_run(run_id: int, status: str, counts: dict[str, int], message: str = "") -> None:
    with connect() as conn:
        conn.execute("UPDATE email_runs SET status = ?, finished_at = ?, counts = ?, message = ? WHERE id = ?",
                     (status, time.time(), json.dumps(counts), message, run_id))


def settle_email_runs() -> int:
    """Mark email runs a dead process left ``running`` as interrupted; call only while holding the email lock."""
    with connect() as conn:
        return conn.execute("UPDATE email_runs SET status = ?, finished_at = ?, message = ? WHERE status = ?",
                            (STATUS_INTERRUPTED, time.time(), "the run stopped before it finished", STATUS_RUNNING)).rowcount


def email_runs(limit: int = 10) -> list[dict[str, Any]]:
    """The newest email runs first, each with its counts decoded."""
    with connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM email_runs ORDER BY id DESC LIMIT ?", (limit,))]
    for r in rows:
        r["counts"] = json.loads(r["counts"]) if r["counts"] else {}
    return rows
