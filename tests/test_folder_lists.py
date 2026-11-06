"""The folder lists Settings edits: config round-trip, the path checks on save and the email excludes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.webapp.server import app
from src import config, folder_lists, scan_runner
from src.email import indexer
from tests.conftest import write_config
from tests.test_email_indexer import indexed_names, mail_tree
from tests.test_email_stage import FakeReader

LOCAL = ("127.0.0.1", 50000)


@pytest.fixture()
def drive(isolated: Path) -> Path:
    """An invented drive saved as the one scan root, with its Mail folder as the email folder."""
    base = mail_tree(isolated / "Drive")
    (isolated / "Elsewhere").mkdir()
    write_config(isolated, roots=[str(base)], email={"roots": [str(base / "Mail")]})
    return base


def refused(update: dict, words: str) -> None:
    with pytest.raises(ValueError, match=words):
        folder_lists.save(update)


def email(**change: object) -> dict:
    return {"email": {**config.load()["email"], **change}}


def test_a_legacy_config_loads_unchanged_and_round_trips_email_exclude(isolated: Path) -> None:
    legacy = {"roots": ["Z:\\Invented"], "exclude": ["Z:\\Invented\\Skip"],
              "email": {"roots": ["Z:\\Invented\\Mail"], "download_budget_mb": 512}}
    write_config(isolated, **legacy)
    cfg = config.load()
    assert cfg["roots"] == legacy["roots"] and cfg["exclude"] == legacy["exclude"]
    assert cfg["email"]["roots"] == legacy["email"]["roots"] and cfg["email"]["download_budget_mb"] == 512
    assert cfg["email"]["exclude"] == []
    # A save that touches another key leaves the stale paths as they are.
    folder_lists.save({"schedule": "hourly"})
    saved = json.loads((isolated / "config.json").read_text(encoding="utf-8"))
    assert saved["roots"] == legacy["roots"] and saved["email"] == legacy["email"]


def test_adding_and_removing_each_list(drive: Path) -> None:
    mail = drive / "Mail"
    cfg = folder_lists.save({"roots": [str(drive), str(drive.parent / "Elsewhere")]})
    assert cfg["roots"] == [str(drive), str(drive.parent / "Elsewhere")]
    cfg = folder_lists.save({"exclude": [str(drive / "Photos")]})
    assert cfg["exclude"] == [str(drive / "Photos")]
    cfg = folder_lists.save(email(roots=[str(mail / "Clients"), str(mail / "Suppliers")]))
    assert cfg["email"]["roots"] == [str(mail / "Clients"), str(mail / "Suppliers")]
    cfg = folder_lists.save(email(roots=[str(mail)]))
    cfg = folder_lists.save(email(exclude=[str(mail / "Old")]))
    assert cfg["email"]["exclude"] == [str(mail / "Old")]
    assert json.loads(config.config_path().read_text(encoding="utf-8"))["email"]["exclude"] == [str(mail / "Old")]
    cfg = folder_lists.save(email(exclude=[]))
    assert cfg["email"]["exclude"] == []
    cfg = folder_lists.save({"roots": [str(drive)]})
    assert cfg["roots"] == [str(drive)]


def test_a_bad_new_path_is_refused_with_its_reason(drive: Path) -> None:
    mail = drive / "Mail"
    refused({"roots": [str(drive), str(drive / "Missing")]}, "Folder not found")
    refused({"roots": [str(drive), "Relative\\Folder"]}, "full folder path")
    refused({"roots": [str(drive), str(drive / "Photos")]}, "is inside")
    refused({"roots": [str(drive), str(drive.parent)]}, "holds")
    refused({"roots": []}, "at least one folder")
    refused({"exclude": [str(drive.parent / "Elsewhere")]}, "not inside any of folders to scan")
    refused(email(roots=[str(mail), str(mail)]), "Already in the list")
    refused(email(roots=[str(mail), str(drive.parent / "Elsewhere")]), "not inside any of folders to scan")
    refused(email(exclude=[str(drive / "Photos")]), "not inside any of email folders")
    assert config.load()["email"]["exclude"] == []


def test_a_folder_that_holds_an_entry_below_it_is_removed_last(drive: Path) -> None:
    mail = drive / "Mail"
    folder_lists.save({"roots": [str(drive), str(drive.parent / "Elsewhere")]})
    refused({"roots": [str(drive.parent / "Elsewhere")]}, "Remove .*Mail from email folders first")
    folder_lists.save(email(exclude=[str(mail / "Old")]))
    refused(email(roots=[]), "Remove .*Old from leave out of the email index first")
    folder_lists.save(email(roots=[], exclude=[]))
    assert folder_lists.save({"roots": [str(drive.parent / "Elsewhere")]})["roots"] == [str(drive.parent / "Elsewhere")]


def test_a_stale_saved_entry_can_still_be_removed(drive: Path, isolated: Path) -> None:
    write_config(isolated, roots=[str(drive)], exclude=[str(drive / "Gone"), str(drive / "Photos")])
    assert folder_lists.save({"exclude": [str(drive / "Gone")]})["exclude"] == [str(drive / "Gone")]


def test_the_api_answers_a_refusal_with_400_and_the_sentence(drive: Path) -> None:
    with TestClient(app, client=LOCAL) as c:
        r = c.post("/api/config", json={"roots": [str(drive), str(drive / "Missing")]})
        assert r.status_code == 400 and r.json()["detail"].startswith("Folder not found")
        r = c.post("/api/config", json=email(exclude=[str(drive / "Mail" / "Old")]))
        assert r.status_code == 200 and r.json()["config"]["email"]["exclude"] == [str(drive / "Mail" / "Old")]


def test_the_email_index_leaves_out_email_exclude_folders(drive: Path, isolated: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(isolated, roots=[str(drive)],
                 email={"roots": [str(drive / "Mail")], "exclude": [str(drive / "Mail" / "Suppliers")]})
    monkeypatch.setattr("src.email.settings.reader_installed", lambda: True)
    # The disk scan does not skip an email exclude, so the run from its index must drop it.
    scan_runner.run_scans()
    assert indexer.run(read=FakeReader())["counts"]["new"] == 3
    assert indexed_names() == ["001 - Kickoff.msg", "002 - Budget.msg", "004 - Skipped.msg"]
    assert indexer.run(read=FakeReader(), fresh=True)["counts"]["files"] == 3
