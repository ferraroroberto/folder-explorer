"""The email stage on invented mail: what it reads, skips, defers, remembers and purges."""

from __future__ import annotations

import sqlite3
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from src.email import reader
from src.email.manifest import Manifest, MsgFile
from src.email.reader import ERR_PARSE, ReadError
from src.email.schema import import_index, open_writer
from src.email.stage import Stage
from tests.email_fixture import build_email_index

MB = 1024 * 1024
FOLDER = str(Path("/invented/Mail"))


def msg(name: str, size: int = 1000, mtime: int = 100, online: bool = False) -> MsgFile:
    return MsgFile(str(Path(FOLDER) / name), size, mtime, online)


class FakeReader:
    """Answers each path with invented facts, or the error it was told to raise; counts every read."""

    def __init__(self, fail: tuple[str, ...] = ()) -> None:
        self.fail = fail
        self.reads: list[str] = []

    def __call__(self, path: str, preview: int) -> dict[str, Any]:
        self.reads.append(Path(path).name)
        if Path(path).name in self.fail:
            raise ReadError(ERR_PARSE, "not a readable mail")
        return {"subject": "About " + Path(path).stem, "sender": "Ana <ana@example.com>", "recipients": "leo@example.org",
                "to_addrs": "leo@example.org", "cc_addrs": "", "date_sent": "2026-01-02T03:04:05+01:00",
                "body_preview": "Invented text.", "flag_status": 0, "message_id": "id-" + Path(path).stem,
                "attachment_names": "", "has_attachments": 0}


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_writer(tmp_path / "emails.sqlite3")


def run(conn: sqlite3.Connection, files: list[MsgFile], read: FakeReader, budget: int = 10 * MB,
        complete: bool = True) -> dict[str, int]:
    return Stage(conn, budget, read).run([Manifest(FOLDER, files, complete)]).as_dict()


def test_new_files_are_read_and_unchanged_ones_never_touched(conn: sqlite3.Connection) -> None:
    files = [msg("001 - a.msg"), msg("002 - b.msg")]
    first = FakeReader()
    assert run(conn, files, first)["new"] == 2 and len(first.reads) == 2
    again = FakeReader()
    counts = run(conn, files, again)
    assert again.reads == [] and counts["unchanged"] == 2
    row = conn.execute("SELECT subject, folder_path, size, to_addrs FROM emails WHERE filename = '001 - a.msg'").fetchone()
    assert row["subject"] == "About 001 - a" and row["folder_path"] == FOLDER and row["size"] == 1000
    changed = FakeReader()
    assert run(conn, [msg("001 - a.msg", mtime=200), files[1]], changed)["changed"] == 1 and changed.reads == ["001 - a.msg"]


def test_a_failed_file_is_not_read_again_until_it_changes(conn: sqlite3.Connection) -> None:
    bad = msg("003 - broken.msg")
    assert run(conn, [bad], FakeReader(fail=("003 - broken.msg",)))["failed"] == 1
    later = FakeReader()
    assert run(conn, [bad], later)["skipped_failed"] == 1 and later.reads == []
    fixed = FakeReader()
    assert run(conn, [msg("003 - broken.msg", size=2000)], fixed)["new"] == 1 and fixed.reads == ["003 - broken.msg"]
    assert conn.execute("SELECT COUNT(*) FROM email_failures").fetchone()[0] == 0


def test_online_files_download_only_within_the_budget(conn: sqlite3.Connection) -> None:
    files = [msg("a.msg", size=3 * MB, online=True), msg("b.msg", size=3 * MB, online=True), msg("c.msg", size=5 * MB)]
    read = FakeReader()
    counts = run(conn, files, read, budget=4 * MB)
    assert counts["downloaded"] == 1 and counts["downloaded_bytes"] == 3 * MB
    assert counts["waiting"] == 1 and counts["waiting_bytes"] == 3 * MB
    # A local file never counts against the budget, whatever its size.
    assert "c.msg" in read.reads
    assert run(conn, files, FakeReader(), budget=0)["waiting"] == 1


def test_gone_files_are_purged_only_after_a_complete_listing(conn: sqlite3.Connection) -> None:
    run(conn, [msg("a.msg"), msg("b.msg")], FakeReader())
    assert run(conn, [msg("a.msg")], FakeReader(), complete=False)["purged"] == 0
    assert conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0] == 2
    assert run(conn, [msg("a.msg")], FakeReader())["purged"] == 1
    assert [r[0] for r in conn.execute("SELECT filename FROM emails")] == ["a.msg"]


def test_an_imported_index_gets_one_re_read_for_the_new_fields(tmp_path: Path) -> None:
    old = build_email_index(tmp_path / "old.db", tmp_path / "Mail", write_files=False)
    dest = tmp_path / "data" / "emails.sqlite3"
    assert import_index(old, dest) == 5
    with pytest.raises(FileExistsError):
        import_index(old, dest)
    conn = open_writer(dest)
    rows = conn.execute("SELECT file_path, file_mtime FROM emails").fetchall()
    files = [MsgFile(r[0], 1000, int(r[1]), False) for r in rows]
    read = FakeReader()
    counts = Stage(conn, 0, read).run([Manifest(str(tmp_path / "Mail"), files)]).as_dict()
    assert counts["reread"] == 5 and len(read.reads) == 5
    assert conn.execute("SELECT COUNT(*) FROM emails WHERE to_addrs IS NULL").fetchone()[0] == 0
    # The consumers' layout holds: the same FTS columns, searchable straight away.
    assert conn.execute("SELECT COUNT(*) FROM emails_fts WHERE emails_fts MATCH 'about'").fetchone()[0] == 5


class _FakeMsg:
    """Stands in for extract_msg.Message: raises UnicodeDecodeError unless read as Windows-1252."""

    opened: list[str | None] = []

    def __init__(self, path: str, overrideEncoding: str | None = None) -> None:
        _FakeMsg.opened.append(overrideEncoding)
        self.encoding = overrideEncoding

    def __enter__(self) -> "_FakeMsg":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    @property
    def body(self) -> str:
        if self.encoding != "cp1252":
            raise UnicodeDecodeError("utf-16-le", b"\x00", 0, 1, "invented")
        return "Text"

    subject, sender, to, cc, date, messageId, attachments = "Re: Hello.msg", "Ana", "Leo", "", None, "<x@y>", []

    def getPropertyVal(self, key: str) -> int:
        return 2


def test_only_a_unicode_error_gets_the_windows_1252_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.ModuleType("extract_msg")
    fake.Message = _FakeMsg  # type: ignore[attr-defined]
    exc_mod = types.ModuleType("extract_msg.exceptions")
    exc_mod.InvalidFileFormatError = type("InvalidFileFormatError", (Exception,), {})  # type: ignore[attr-defined]
    exc_mod.UnrecognizedMSGTypeError = type("UnrecognizedMSGTypeError", (Exception,), {})  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "extract_msg", fake)
    monkeypatch.setitem(sys.modules, "extract_msg.exceptions", exc_mod)
    _FakeMsg.opened = []
    facts = reader.read_facts("invented.msg", 500)
    assert _FakeMsg.opened == [None, "cp1252"]
    assert facts["subject"] == "Hello" and facts["message_id"] == "x@y" and facts["flag_status"] == 2
    _FakeMsg.opened = []
    assert reader.read_message("invented.msg")["text"] == "Text" and _FakeMsg.opened == [None, "cp1252"]

    def broken(path: str, overrideEncoding: str | None = None) -> _FakeMsg:
        _FakeMsg.opened.append(overrideEncoding)
        raise exc_mod.InvalidFileFormatError("bad")  # type: ignore[attr-defined]

    fake.Message = broken  # type: ignore[attr-defined]
    _FakeMsg.opened = []
    with pytest.raises(ReadError) as err:
        reader.read_facts("invented.msg", 500)
    assert err.value.code == ERR_PARSE and _FakeMsg.opened == [None]
