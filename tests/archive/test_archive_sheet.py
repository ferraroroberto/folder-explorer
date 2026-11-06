"""The Archive sheet's steps over a fake Outlook client: what it suggests and why, and filing (invented data only)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.email import archive_sheet
from src.email.repository import open_index
from src.email.archive_sheet import ERR_ALREADY, ERR_NO_ROOT, ERR_OUTSIDE, SheetError
from tests.archive.batch_support import FakeOutlookClient, _index_archived_file, _mail, _seed_index, archive_root, cfg  # noqa: F401


def _client_with(subject: str, sender: str = "lead@example.invalid") -> tuple[FakeOutlookClient, object]:
    item = _mail("kick@example.invalid", subject, sender=sender)
    return FakeOutlookClient([item]), item


def test_suggest_ranks_the_thread_folder_first_and_says_why(cfg: dict, archive_root: Path) -> None:
    _seed_index(cfg, archive_root, {"Project Alpha": ["Kickoff agenda", "Budget notes"], "Internal": ["Team agenda"]})
    (archive_root / "Project Alpha").mkdir()
    client, item = _client_with("Kickoff agenda", sender="sender@example.invalid")
    doc = archive_sheet.suggest(client, cfg, item, "Inbox")
    assert doc["ok"] and doc["mail"]["subject"] == "Kickoff agenda" and doc["mail"]["where"] == "Inbox"
    first = doc["suggestions"][0]
    assert first["folder_path"] == str(archive_root / "Project Alpha")
    assert [c["label"] for c in first["why"]] == ["Same subject", "Sender"]
    assert first["note"] == "This conversation is filed here"
    assert first["saved_as"] == "001 - Kickoff agenda.msg"
    assert doc["already_archived"] is None and doc["roots"] == [str(archive_root.resolve())]


def test_suggest_reports_a_mail_already_filed(cfg: dict, archive_root: Path) -> None:
    client, item = _client_with("Filed before")
    filed = archive_root / "Done" / "001 - Filed before.msg"
    filed.parent.mkdir()
    filed.write_text("synthetic", encoding="utf-8")
    _index_archived_file(cfg, {"files": [str(filed)]}, message_id="kick@example.invalid", subject="Filed before")
    assert archive_sheet.suggest(client, cfg, item)["already_archived"] == str(filed)


def test_no_mounted_email_folder_is_its_own_error(cfg: dict, tmp_path: Path) -> None:
    cfg["email"]["roots"] = [str(tmp_path / "not-mounted")]
    client, item = _client_with("Anything")
    with pytest.raises(SheetError) as exc:
        archive_sheet.suggest(client, cfg, item)
    assert exc.value.code == ERR_NO_ROOT and "No archive root is mounted" in str(exc.value)


def test_filing_saves_the_mail_and_tags_it_without_moving_it(cfg: dict, archive_root: Path) -> None:
    client, item = _client_with("Kickoff agenda")
    dest = archive_root / "Project Alpha" / "2026"
    doc = archive_sheet.file_mail(client, cfg, item, str(dest), "Filed")
    assert doc["ok"] and doc["categorized"] and item.Categories == "Filed"
    assert Path(doc["files"][0]).name == "001 - Kickoff agenda.msg" and Path(doc["files"][0]).is_file()
    assert client.moves == [] and client.folders[None] == [item]


def test_filing_with_no_category_leaves_the_mail_untagged(cfg: dict, archive_root: Path) -> None:
    client, item = _client_with("Untagged")
    doc = archive_sheet.file_mail(client, cfg, item, str(archive_root), None)
    assert doc["ok"] and not doc["categorized"] and item.Categories == ""


def test_a_folder_outside_the_email_folders_is_refused_before_anything_is_written(cfg: dict, tmp_path: Path) -> None:
    client, item = _client_with("Elsewhere")
    outside = tmp_path / "elsewhere"
    with pytest.raises(SheetError) as exc:
        archive_sheet.file_mail(client, cfg, item, str(outside), "Filed")
    assert exc.value.code == ERR_OUTSIDE and not outside.exists() and item.saved_as == []


def test_a_mail_already_filed_is_not_filed_twice(cfg: dict, archive_root: Path) -> None:
    client, item = _client_with("Twice")
    first = archive_sheet.file_mail(client, cfg, item, str(archive_root), None)
    _index_archived_file(cfg, first, message_id="kick@example.invalid", subject="Twice")
    with pytest.raises(SheetError) as exc:
        archive_sheet.file_mail(client, cfg, item, str(archive_root), None)
    assert exc.value.code == ERR_ALREADY and "001 - Twice.msg" in str(exc.value)


def test_an_explicit_duplicate_files_a_numbered_second_copy_and_overwrites_nothing(cfg: dict, archive_root: Path) -> None:
    client, item = _client_with("Twice")
    first = archive_sheet.file_mail(client, cfg, item, str(archive_root), None)
    _index_archived_file(cfg, first, message_id="kick@example.invalid", subject="Twice")
    first_bytes = Path(first["files"][0]).read_bytes()
    other = archive_root / "Elsewhere"
    copy = archive_sheet.file_mail(client, cfg, item, str(other), None, duplicate=True)
    again = archive_sheet.file_mail(client, cfg, item, str(archive_root), None, duplicate=True)
    assert copy["ok"] and Path(copy["files"][0]).name == "001 - Twice.msg" and Path(copy["files"][0]).parent == other.resolve()
    assert Path(again["files"][0]).name == "002 - Twice.msg" and Path(first["files"][0]).read_bytes() == first_bytes
    _index_archived_file(cfg, copy, message_id="kick@example.invalid", subject="Twice")
    _index_archived_file(cfg, again, message_id="kick@example.invalid", subject="Twice")
    conn = open_index(cfg)
    try:
        rows = conn.execute("SELECT file_path FROM emails WHERE message_id = ?", ("kick@example.invalid",)).fetchall()
    finally:
        conn.close()
    assert len(rows) == 3
    assert archive_sheet.suggest(client, cfg, item)["already_archived"] in {r["file_path"] for r in rows}


def test_a_category_outlook_refuses_still_reports_the_files(cfg: dict, archive_root: Path) -> None:
    client, item = _client_with("No tag")
    client.category_should_fail = True
    doc = archive_sheet.file_mail(client, cfg, item, str(archive_root), "Filed")
    assert not doc["ok"] and doc["error"]["code"] == "category_failed" and Path(doc["files"][0]).is_file()
