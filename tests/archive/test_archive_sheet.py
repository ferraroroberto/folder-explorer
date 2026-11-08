"""The Archive sheet's steps over a fake Outlook client: what it suggests and why, and filing (invented data only)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.email import archive_sheet
from src.email.archive_sheet import ERR_ALREADY, ERR_NO_ROOT, ERR_OUTSIDE, SheetError
from src.email.repository import open_index
from src.explorer_window import ExplorerUnavailableError
from tests.archive.batch_support import (  # noqa: F401
    FakeOutlookClient,
    _index_archived_file,
    _mail,
    _seed_index,
    archive_root,
    cfg,
)


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
    assert first["saved_as"] == {"auto": "001 - Kickoff agenda.msg", "on": "2026-03-14 - 001 - Kickoff agenda.msg",
                                 "off": "001 - Kickoff agenda.msg"}
    assert 0 < first["score"] <= 1 and first["similar"] >= 1 and first["example"] in {"Kickoff agenda", "Budget notes"}
    assert first["display_name"] == "archive\\Project Alpha"
    assert doc["mail"]["preview"].startswith("synthetic body")
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


def test_a_folder_offered_on_topic_alone_says_so_with_its_count_and_an_example(cfg: dict, archive_root: Path) -> None:
    _seed_index(cfg, archive_root, {"Gardening": ["Tulip bulbs order", "Tulip bulbs delivery"]})
    client, item = _client_with("Tulip bulbs invoice", sender="nobody@example.invalid")
    first = archive_sheet.suggest(client, cfg, item)["suggestions"][0]
    assert [c["label"] for c in first["why"]] == ["Topic"] and first["note"] == ""
    assert first["similar"] == 2 and first["example"].startswith("Tulip bulbs")


@pytest.mark.parametrize("choice, name", [("on", "2026-03-14 - 001 - Dated.msg"), ("off", "001 - Dated.msg")])
def test_the_date_prefix_choice_names_the_filed_mail(cfg: dict, archive_root: Path, choice: str, name: str) -> None:
    client, item = _client_with("Dated")
    doc = archive_sheet.file_mail(client, cfg, item, str(archive_root), None, date_prefix=choice)
    assert Path(doc["files"][0]).name == name


def test_an_unknown_date_prefix_choice_writes_nothing(cfg: dict, archive_root: Path) -> None:
    client, item = _client_with("Dated")
    with pytest.raises(ValueError):
        archive_sheet.file_mail(client, cfg, item, str(archive_root), None, date_prefix="sometimes")
    assert item.saved_as == []


def test_reopening_a_filed_mail_offers_its_folder_as_the_same_email(cfg: dict, archive_root: Path) -> None:
    """File, index the copy as the email stage would, reopen: already archived, and its folder leads at 100%."""
    _seed_index(cfg, archive_root, {"Elsewhere": ["Quarterly figures draft"]})
    client, item = _client_with("Quarterly figures")
    filed = archive_sheet.file_mail(client, cfg, item, str(archive_root / "Finance"), None)
    _index_archived_file(cfg, filed, message_id="kick@example.invalid", subject="Quarterly figures")

    doc = archive_sheet.suggest(client, cfg, item)

    first = doc["suggestions"][0]
    assert doc["already_archived"] == filed["files"][0]
    assert Path(first["folder_path"]) == Path(filed["folder_path"]) and first["score"] == 1.0
    assert [c["label"] for c in first["why"]][:2] == ["Same email", "Same subject"]
    assert first["note"] == "This email is already filed here" and first["example"] == "Quarterly figures"


def test_another_mail_with_the_same_subject_is_only_the_same_subject(cfg: dict, archive_root: Path) -> None:
    client, item = _client_with("Quarterly figures")
    filed = archive_sheet.file_mail(client, cfg, item, str(archive_root / "Finance"), None)
    _index_archived_file(cfg, filed, message_id="other@example.invalid", subject="Quarterly figures")

    doc = archive_sheet.suggest(client, cfg, item)

    first = doc["suggestions"][0]
    assert doc["already_archived"] is None and first["score"] < 1.0
    assert [c["label"] for c in first["why"]][0] == "Same subject" and first["note"] == "This conversation is filed here"


# ---------------------------------------------------------------- the folder open in File Explorer

def test_the_explorer_folder_inside_the_email_folders_is_offered(cfg: dict, archive_root: Path) -> None:
    (archive_root / "Project Alpha").mkdir()
    doc = archive_sheet.explorer_folder(cfg, lambda: str(archive_root / "Project Alpha"))
    assert doc == {"ok": True, "folder_path": str((archive_root / "Project Alpha").resolve())}


def test_no_explorer_folder_open_and_an_unreadable_shell_are_different_errors(cfg: dict) -> None:
    with pytest.raises(SheetError) as nothing:
        archive_sheet.explorer_folder(cfg, lambda: None)
    assert nothing.value.code == archive_sheet.ERR_NO_EXPLORER

    def unreachable() -> str:
        raise ExplorerUnavailableError("COM server unavailable")

    with pytest.raises(SheetError) as cannot:
        archive_sheet.explorer_folder(cfg, unreachable)
    assert cannot.value.code == archive_sheet.ERR_EXPLORER_UNAVAILABLE
    assert str(nothing.value) != str(cannot.value)
    assert str(cannot.value).startswith("Cannot read Explorer windows") and str(nothing.value).startswith("No Explorer folder")


def test_an_explorer_folder_outside_the_email_folders_is_refused_like_any_other(cfg: dict, tmp_path: Path) -> None:
    elsewhere = tmp_path / "Elsewhere"
    elsewhere.mkdir()
    with pytest.raises(SheetError) as refused:
        archive_sheet.explorer_folder(cfg, lambda: str(elsewhere))
    assert refused.value.code == ERR_OUTSIDE


def test_the_explorer_folder_asks_the_shell_module_by_default(cfg: dict, archive_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(archive_sheet, "get_current_explorer_folder", lambda: str(archive_root))
    assert archive_sheet.explorer_folder(cfg)["folder_path"] == str(archive_root.resolve())
