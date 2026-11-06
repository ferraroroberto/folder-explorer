"""The indexed preview keeps readable text only: link targets and image placeholders never reach it (synthetic bodies)."""

from __future__ import annotations

import sqlite3
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.email import reader
from src.email.manifest import Manifest, MsgFile
from src.email.preview import clean_preview
from src.email.schema import open_writer
from src.email.stage import Stage
from src.outlook.client import OutlookClient

LOGO = "<https://assets.shop.example/image/upload/f_auto,q_auto/Brand/Logo>"
RECEIPT = (LOGO + "\r\n\r\n\r\nRECEIPT COPY  \r\nOrder 1234 for Ana Lopez\r\n[cid:logo001@01D5A.7B2C]\r\n"
           "https://track.shop.example/o/abc123?u=77\r\nThank you for your purchase.")
NEWSLETTER = ("[image: Spring sale] <https://news.example/b/1> Welcome back, Ana!\n"
              "Read the guide <https://news.example/guide> or see our offers <https://news.example/offers?x=1> today.\n"
              "[image]\nQuestions? Write to help <mailto:help@news.example>.")
PLAIN = "Hi Leo,\n\nThe invented report is attached. Call me at 5 if Ana <ana@example.com> is out.\n\nThanks,\nAna"


def test_a_receipt_starts_at_its_first_real_words() -> None:
    out = clean_preview(RECEIPT)
    assert out == "RECEIPT COPY\nOrder 1234 for Ana Lopez\n\nThank you for your purchase."
    assert "http" not in out and "cid:" not in out and "<" not in out


def test_a_newsletter_keeps_its_link_text_and_loses_its_targets() -> None:
    assert clean_preview(NEWSLETTER) == (
        "Welcome back, Ana!\nRead the guide or see our offers today.\n\nQuestions? Write to help.")


def test_a_plain_mail_is_left_as_written() -> None:
    assert clean_preview(PLAIN) == PLAIN
    assert clean_preview("") == "" and clean_preview("   \n ") == ""


def test_a_target_cut_off_by_the_old_500_character_limit_is_dropped() -> None:
    assert clean_preview("Order shipped. Track it <https://carrier.example/t/12345?lang=en&amp") == "Order shipped. Track it"


def test_a_name_with_an_address_in_angle_brackets_stays() -> None:
    assert clean_preview("From: Ana <ana@example.com>") == "From: Ana <ana@example.com>"


class _Msg:
    """Stands in for an opened extract_msg.Message with an Outlook-style plain-text body."""

    subject, sender, to, cc, date, messageId, attachments = "Receipt", "Ana", "Leo", "", None, "<m@x>", []
    body = RECEIPT

    def getPropertyVal(self, key: str) -> int:
        return 0

    def __enter__(self) -> "_Msg":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_the_msg_reader_cleans_before_it_cuts(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.ModuleType("extract_msg")
    fake.Message = lambda path, overrideEncoding=None: _Msg()  # type: ignore[attr-defined]
    exc_mod = types.ModuleType("extract_msg.exceptions")
    exc_mod.InvalidFileFormatError = type("InvalidFileFormatError", (Exception,), {})  # type: ignore[attr-defined]
    exc_mod.UnrecognizedMSGTypeError = type("UnrecognizedMSGTypeError", (Exception,), {})  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "extract_msg", fake)
    monkeypatch.setitem(sys.modules, "extract_msg.exceptions", exc_mod)
    # 60 characters would hold only the logo target and the start of the receipt if the cut came first.
    preview = reader.read_facts("invented.msg", 60)["body_preview"]
    assert preview.startswith("RECEIPT COPY\nOrder 1234 for Ana Lopez") and "http" not in preview and len(preview) <= 60


def test_the_live_outlook_read_cleans_before_it_cuts() -> None:
    item = SimpleNamespace(Body=RECEIPT, Subject="Receipt", EntryID="e1", Attachments=SimpleNamespace(Count=0))
    mail = OutlookClient.__new__(OutlookClient)._read_mail(item, 60)
    assert mail.body_preview.startswith("RECEIPT COPY\nOrder 1234 for Ana Lopez")
    assert "http" not in mail.body_preview and len(mail.body_preview) <= 60


# ---- rows already in the index --------------------------------------------------------------------------------

FOLDER = str(Path("/invented/Mail"))


class _NoRead:
    """A reader that fails the test if the stage opens a file."""

    def __call__(self, path: str, preview: int) -> dict[str, Any]:
        raise AssertionError("an unchanged mail must not be read: " + path)


def _row(conn: sqlite3.Connection, name: str, preview: str) -> MsgFile:
    path = str(Path(FOLDER) / name)
    conn.execute(
        "INSERT INTO emails (file_path, folder_path, filename, subject, sender, recipients, date_sent, body_preview, "
        "file_mtime, size, to_addrs) VALUES (?, ?, ?, 'Receipt', 'Ana', 'Leo', '2026-01-02', ?, 100, 1000, 'Leo')",
        (path, FOLDER, name, preview))
    conn.commit()
    return MsgFile(path, 1000, 100, False)


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_writer(tmp_path / "emails.sqlite3")


def _fts(conn: sqlite3.Connection, query: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM emails_fts WHERE emails_fts MATCH ?", (query,)).fetchone()[0]


def test_a_run_cleans_stored_previews_without_opening_any_file(conn: sqlite3.Connection) -> None:
    dirty = _row(conn, "001 - receipt.msg", RECEIPT)
    clean = _row(conn, "002 - plain.msg", PLAIN)
    assert _fts(conn, "assets") == 1
    counts = Stage(conn, 0, _NoRead()).run([Manifest(FOLDER, [dirty, clean])]).as_dict()
    assert counts["unchanged"] == 2 and counts["cleaned"] == 1
    stored = dict(conn.execute("SELECT filename, body_preview FROM emails").fetchall())
    assert stored["001 - receipt.msg"].startswith("RECEIPT COPY") and "http" not in stored["001 - receipt.msg"]
    assert stored["002 - plain.msg"] == PLAIN
    # The search index follows the row: the link's words are gone, the mail's words are still found.
    assert _fts(conn, "assets") == 0 and _fts(conn, '"receipt copy"') == 1 and _fts(conn, "purchase") == 1 and _fts(conn, "lopez") == 1
    again = Stage(conn, 0, _NoRead()).run([Manifest(FOLDER, [dirty, clean])]).as_dict()
    assert again["cleaned"] == 0
