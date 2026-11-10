"""The email index this app writes: ``emails.sqlite3`` in the runtime-data folder.

Its ``emails`` table and ``emails_fts`` mirror keep the layout every consumer
already reads (task-os, life-os, email-archiver's own verbs): the same
columns, the FTS column order ``subject, sender, recipients, body_preview``
their ``bm25(emails_fts, 10, 3, 3, 1)`` weights assume, and ``recipients``
still filled with the To line. The split To and Cc, attachment names, an
attachment flag and the file size sit alongside. ``email_failures`` remembers
each file that would not read, with the size and modified time it had, so it
is not read again until it changes.

The email stage (``src/email/stage.py``) is the only writer that reads a ``.msg``;
filing, ``revert`` and ``renumber`` also write the row of a file they just changed
(``src/email/repository.py``). Every reader opens the file ``mode=ro``.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from src import atomic_write, runtime_files, store

logger = logging.getLogger(__name__)

INDEX_NAME = "emails.sqlite3"

# How long a writer waits on another writer before "database is locked".
BUSY_TIMEOUT_S = 30.0

_DDL = """
CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT UNIQUE NOT NULL,
    folder_path TEXT NOT NULL,
    filename TEXT NOT NULL,
    subject TEXT, sender TEXT, recipients TEXT, date_sent TEXT, body_preview TEXT,
    file_mtime REAL NOT NULL,
    indexed_at TEXT NOT NULL DEFAULT (datetime('now')),
    flag_status INTEGER, message_id TEXT,
    to_addrs TEXT, cc_addrs TEXT, attachment_names TEXT, has_attachments INTEGER, size INTEGER
);
CREATE INDEX IF NOT EXISTS idx_emails_folder ON emails(folder_path);
CREATE INDEX IF NOT EXISTS idx_emails_mtime ON emails(file_mtime);
CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(
    subject, sender, recipients, body_preview,
    content = emails, content_rowid = id, tokenize = 'unicode61 remove_diacritics 1'
);
CREATE TRIGGER IF NOT EXISTS emails_ai AFTER INSERT ON emails BEGIN
    INSERT INTO emails_fts(rowid, subject, sender, recipients, body_preview)
    VALUES (new.id, new.subject, new.sender, new.recipients, new.body_preview);
END;
CREATE TRIGGER IF NOT EXISTS emails_ad AFTER DELETE ON emails BEGIN
    INSERT INTO emails_fts(emails_fts, rowid, subject, sender, recipients, body_preview)
    VALUES ('delete', old.id, old.subject, old.sender, old.recipients, old.body_preview);
END;
CREATE TRIGGER IF NOT EXISTS emails_au AFTER UPDATE ON emails BEGIN
    INSERT INTO emails_fts(emails_fts, rowid, subject, sender, recipients, body_preview)
    VALUES ('delete', old.id, old.subject, old.sender, old.recipients, old.body_preview);
    INSERT INTO emails_fts(rowid, subject, sender, recipients, body_preview)
    VALUES (new.id, new.subject, new.sender, new.recipients, new.body_preview);
END;
CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_path TEXT UNIQUE NOT NULL,
    last_updated TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS email_failures (
    file_path TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    file_mtime REAL NOT NULL,
    reason TEXT NOT NULL,
    failed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# Columns an index written by the older scanner lacks; ALTERed in on open.
ADDED_COLUMNS = {
    "flag_status": "INTEGER", "message_id": "TEXT", "to_addrs": "TEXT", "cc_addrs": "TEXT",
    "attachment_names": "TEXT", "has_attachments": "INTEGER", "size": "INTEGER",
}
# The message-id index answers "already filed?"; the case-blind subject index answers the
# suggester's exact-subject lookup, which is otherwise a scan of every row.
_POST_MIGRATION = """
CREATE INDEX IF NOT EXISTS idx_emails_message_id ON emails(message_id);
CREATE INDEX IF NOT EXISTS idx_emails_subject_nocase ON emails(subject COLLATE NOCASE);
"""


def own_index_path() -> Path:
    """This app's email index, in the runtime-data folder."""
    return store.data_dir() / INDEX_NAME


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Create what is missing and add the newer columns; returns the columns added."""
    conn.executescript(_DDL)
    have = {r[1] for r in conn.execute("PRAGMA table_info(emails)")}
    added = [name for name in ADDED_COLUMNS if name not in have]
    for name in added:
        conn.execute("ALTER TABLE emails ADD COLUMN %s %s" % (name, ADDED_COLUMNS[name]))
    conn.executescript(_POST_MIGRATION)
    conn.commit()
    if added:
        logger.info("ℹ️ email index: added column(s) %s", ", ".join(added))
    return added


def open_writer(path: Path) -> sqlite3.Connection:
    """The one writer's connection: WAL, a busy wait, the schema made current."""
    runtime_files.make_dirs(path.parent)
    conn = runtime_files.connect(path, timeout=BUSY_TIMEOUT_S, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    migrate(conn)
    return conn


def import_index(source: Path, dest: Path) -> int:
    """Copy another app's email index to ``dest`` as a consistent snapshot, then migrate it.

    Refuses when ``dest`` already exists: the import is a one-time move, and
    overwriting would drop what the stage has indexed since. Returns the rows.
    """
    if dest.exists():
        raise FileExistsError("the email index already exists: " + dest.name)
    part = dest.with_name(dest.name + ".part")
    part.unlink(missing_ok=True)
    src = runtime_files.connect_read_only(source, timeout=BUSY_TIMEOUT_S)
    try:
        if not {"emails", "emails_fts"} <= {r[0] for r in src.execute("SELECT name FROM sqlite_master")}:
            raise ValueError("not an email index: " + source.name)
        runtime_files.make_dirs(dest.parent)
        out = runtime_files.connect(part)
        try:
            src.backup(out)
            migrate(out)
            rows = int(out.execute("SELECT COUNT(*) FROM emails").fetchone()[0])
        finally:
            out.close()
    finally:
        src.close()
    atomic_write.swap_in(part, dest)
    logger.info("✅ imported %d emails into %s", rows, dest.name)
    return rows
