"""Seed the email index the batch verbs read, with invented rows only.

The product's repository never writes a new row (the email stage does), so
the tests that need archived mail in the index write it through this
subclass, in the stage's own columns.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from src.email import repository
from src.email.archive_settings import index_path
from src.email.schema import open_writer


@dataclass
class EmailRecord:
    file_path: str
    folder_path: str
    filename: str
    subject: str = ""
    sender: str = ""
    recipients: str = ""
    date_sent: str = ""
    body_preview: str = ""
    file_mtime: float = 0.0
    flag_status: int = 0
    message_id: str = ""
    id: Optional[int] = None


class EmailRepository(repository.EmailRepository):
    """The product repository plus the stage's upsert, for seeding."""

    def __init__(self, conn: sqlite3.Connection, **kw: Any) -> None:
        super().__init__(conn, **kw)
        self._seed_conn = conn

    def upsert_email(self, rec: EmailRecord) -> None:
        self._retry_locked(lambda: self._seed_conn.execute(
            "INSERT INTO emails (file_path, folder_path, filename, subject, sender, recipients, date_sent, "
            "body_preview, file_mtime, flag_status, message_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(file_path) DO UPDATE SET subject = excluded.subject, sender = excluded.sender, "
            "recipients = excluded.recipients, date_sent = excluded.date_sent, body_preview = excluded.body_preview, "
            "file_mtime = excluded.file_mtime, flag_status = excluded.flag_status, message_id = excluded.message_id, "
            "indexed_at = datetime('now')",
            (rec.file_path, rec.folder_path, rec.filename, rec.subject, rec.sender, rec.recipients, rec.date_sent,
             rec.body_preview, rec.file_mtime, rec.flag_status, rec.message_id)), "upsert_email")


def init_db(path: str | Path) -> sqlite3.Connection:
    return open_writer(Path(path))


def get_connection(path: str | Path, timeout: float = 30.0) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=timeout, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def open_index(cfg: dict[str, Any]) -> sqlite3.Connection:
    return init_db(index_path(cfg))
