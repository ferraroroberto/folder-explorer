"""An invented email index for tests: email-archiver's schema, made-up mails, no real data.

``build_email_index(path)`` writes the schema today's email index has (the
``emails`` table, its ``emails_fts`` mirror and triggers); ``extra_fields``
adds the To/Cc, attachment and size columns a re-read fills.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT UNIQUE NOT NULL, folder_path TEXT NOT NULL, filename TEXT NOT NULL,
    subject TEXT, sender TEXT, recipients TEXT, date_sent TEXT, body_preview TEXT,
    file_mtime REAL NOT NULL, indexed_at TEXT NOT NULL DEFAULT (datetime('now')),
    flag_status INTEGER, message_id TEXT
);
CREATE VIRTUAL TABLE emails_fts USING fts5(
    subject, sender, recipients, body_preview,
    content = emails, content_rowid = id, tokenize = 'unicode61 remove_diacritics 1'
);
CREATE TRIGGER emails_ai AFTER INSERT ON emails BEGIN
    INSERT INTO emails_fts(rowid, subject, sender, recipients, body_preview)
    VALUES (new.id, new.subject, new.sender, new.recipients, new.body_preview);
END;
"""

_EXTRA = """
ALTER TABLE emails ADD COLUMN to_addrs TEXT;
ALTER TABLE emails ADD COLUMN cc_addrs TEXT;
ALTER TABLE emails ADD COLUMN attachment_names TEXT;
ALTER TABLE emails ADD COLUMN has_attachments INTEGER;
ALTER TABLE emails ADD COLUMN size INTEGER;
"""

# (folder below the email root, subject, sender, to, cc, date, preview, flag, attachments, size); all invented.
MAILS: tuple[tuple[str, str, str, str, str, str, str, int, tuple[str, ...], int], ...] = (
    ("Clients/Northwind", "Q4 budget draft", "Ana Lopez <ana@example.com>", "leo@example.org", "mia@example.net",
     "2025-10-14T09:12:00+02:00", "Here is the revised budget for the fourth quarter.", 2, ("plan.pdf", "sheet.xlsx"), 412_000),
    ("Clients/Northwind", "Re: budget notes", "Ana Lopez <ana@example.com>", "leo@example.org", "",
     "2025-10-02T16:40:00+02:00", "Notes from the call about the budget and the timeline.", 0, (), 18_000),
    ("Projects/Alpha", "Kickoff agenda", "Leo Marsh <leo@example.org>", "ana@example.com", "",
     "2026-01-20T08:00:00+01:00", "Agenda for the kickoff: scope, budget, people.", 0, ("agenda.docx",), 64_000),
    ("Projects/Alpha", "Site visit photos", "Mia Chen <mia@example.net>", "leo@example.org", "ana@example.com",
     "2026-03-05T11:30:00+01:00", "Photos from the site visit are attached.", 1, ("site-01.jpg", "site-02.jpg"), 6_800_000),
    ("Suppliers/Kestrel", "Invoice 0042", "Billing <billing@kestrel.example>", "ana@example.com", "",
     "2024-06-30T10:00:00+02:00", "Please find invoice 0042 attached.", 0, ("invoice-0042.pdf",), 95_000),
)


def build_email_index(path: Path, mail_root: Path, extra_fields: bool = False, write_files: bool = True) -> Path:
    """Write the invented index at ``path``; its mails point at ``.msg`` files under ``mail_root``."""
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    if extra_fields:
        conn.executescript(_EXTRA)
    for n, (folder, subject, sender, to, cc, sent, preview, flag, atts, size) in enumerate(MAILS, start=1):
        folder_path = mail_root / folder
        name = "%03d - %s.msg" % (n, subject.replace(":", ""))
        file_path = folder_path / name
        if write_files:
            folder_path.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(b"invented")
        recipients = "; ".join(x for x in (to, cc) if x)
        cols = ["file_path", "folder_path", "filename", "subject", "sender", "recipients", "date_sent", "body_preview",
                "file_mtime", "flag_status", "message_id"]
        vals: list[object] = [str(file_path), str(folder_path), name, subject, sender, recipients, sent, preview, 0.0, flag,
                              "msg-%d@example.invalid" % n]
        if extra_fields:
            cols += ["to_addrs", "cc_addrs", "attachment_names", "has_attachments", "size"]
            vals += [to, cc, "\n".join(atts), 1 if atts else 0, size]
        conn.execute("INSERT INTO emails (%s) VALUES (%s)" % (", ".join(cols), ", ".join("?" * len(cols))), vals)
    conn.commit()
    conn.close()
    return path
