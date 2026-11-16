"""Available features: each block's switch is stored on this machine, defaults to on, and a block that is off is refused or skipped everywhere it reaches."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.webapp.server import app
from src import config, features
from tests.conftest import write_config

LOCAL = ("127.0.0.1", 50000)


def switch(tmp: Path, **blocks: bool) -> None:
    write_config(tmp, features=blocks)


# ---------------------------------------------------------------- the setting itself


def test_every_block_is_on_by_default_and_listed(isolated: Path) -> None:
    assert config.load()["features"] == {"email": True, "archive": True, "documents": True, "screenshot": True}
    with TestClient(app, client=LOCAL) as c:
        listed = c.get("/api/features").json()["features"]
        assert [b["key"] for b in listed] == ["email", "archive", "documents", "screenshot"]
        assert all(b["enabled"] for b in listed)
        assert all(b["covers"] and b["title"] for b in listed)
        assert c.get("/api/config").json()["features"] == listed


def test_a_choice_survives_a_restart_and_leaves_the_other_keys(isolated: Path) -> None:
    write_config(isolated, port=9000, features={"documents": False})
    with TestClient(app, client=LOCAL) as c:
        body = c.post("/api/config", json={"features": {"email": False}}).json()
        assert body["config"]["features"] == {"email": False, "archive": True, "documents": False, "screenshot": True}
    saved = json.loads((isolated / "config.json").read_text(encoding="utf-8"))
    assert saved["features"] == {"documents": False, "email": False} and saved["port"] == 9000
    # A new process reads the file alone: nothing is held in memory.
    assert config.load()["features"]["email"] is False and config.load()["features"]["documents"] is False


def test_an_unknown_block_or_a_non_boolean_is_refused(isolated: Path) -> None:
    with TestClient(app, client=LOCAL) as c:
        for bad in ({"printer": False}, {"email": "off"}, {}, [], None):
            assert c.post("/api/config", json={"features": bad}).status_code == 400
    assert not (isolated / "config.json").exists()


def test_a_damaged_block_reads_as_on(isolated: Path) -> None:
    write_config(isolated, features={"email": "no", "archive": None, "documents": 0, "bogus": False})
    assert config.load()["features"] == features.DEFAULTS
    write_config(isolated, features=["email"])
    assert config.load()["features"] == features.DEFAULTS


def test_the_config_file_is_never_committed() -> None:
    ignored = (Path(config.__file__).resolve().parent.parent / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "config.json" in ignored


def test_a_missing_dependency_is_reported_not_hidden(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(features, "_installed", lambda module: module != "extract_msg")
    email = next(b for b in features.status(config.load()) if b["key"] == "email")
    assert email["enabled"] is True and email["available"] is False and "extract-msg" in email["reason"]


# ------------------------------------------------------------------------------ email


class _Stage:
    """A stand-in for the email stage's child process that records that it was started."""

    def __init__(self) -> None:
        self.started: list[str] = []

    def spawn(self, trigger: str, *_args: object, **_kwargs: object) -> "_Stage":
        self.started.append(trigger)
        return self

    def wait(self) -> int:
        return 0


@pytest.fixture()
def email_folder(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """One scan root with one email folder, the reader counted as installed, and the stage's child replaced."""
    folder = isolated / "Invented root"
    folder.mkdir()
    monkeypatch.setattr("src.email.settings.reader_installed", lambda: True)
    return folder


def test_email_off_skips_the_stage_and_answers_search(isolated: Path, email_folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src import scan_runner
    from src.email import indexer
    from src.email.settings import email_part
    from src.roots import resolve_roots

    stage = _Stage()
    monkeypatch.setattr(indexer, "spawn", stage.spawn)
    write_config(isolated, roots=[str(email_folder)], email={"roots": [str(email_folder)]}, features={"email": False})
    scan_runner.index_emails_after("test")
    assert stage.started == []
    part = email_part(config.load(), resolve_roots(config.load()["roots"]))
    assert part.stage_problem == part.problem == part.reader_problem == features.OFF_SENTENCE
    with TestClient(app, client=LOCAL) as c:
        status = c.get("/api/search/status").json()
        assert status["emails"]["ready"] is False and status["emails"]["reason"] == features.OFF_SENTENCE
        assert c.post("/api/email/index", json={}).status_code == 503
        assert c.get("/api/search/emails", params={"q": "x"}).status_code == 503
        assert c.get("/api/v1/search/status").json()["emails"]["state"] != "ok"


def test_email_on_again_restores_the_stage(isolated: Path, email_folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src import scan_runner
    from src.email import indexer

    stage = _Stage()
    monkeypatch.setattr(indexer, "spawn", stage.spawn)
    write_config(isolated, roots=[str(email_folder)], email={"roots": [str(email_folder)]}, features={"email": False})
    scan_runner.index_emails_after("test")
    assert stage.started == []
    with TestClient(app, client=LOCAL) as c:
        assert c.post("/api/config", json={"features": {"email": True}}).status_code == 200
    scan_runner.index_emails_after("test")
    assert stage.started == ["test"]


# --------------------------------------------------------------------------- archive


def test_archive_off_refuses_every_route_and_the_verbs(isolated: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from app.cli import __main__ as cli
    from app.cli import archive_gate
    from app.webapp.routers.archive import archive_status

    switch(isolated, archive=False)
    with TestClient(app, client=LOCAL) as c:
        assert c.get("/api/archive/macro").status_code == 404
        assert c.get("/api/archive/pending").status_code == 404
        assert c.post("/api/archive/current").status_code == 404
        assert c.post("/api/emails/1/find").status_code == 404
        assert c.post("/api/v1/archive/plan", json={}).status_code == 404
        assert c.get("/api/search/status").json()["archive"] == {"ready": False, "reason": features.OFF_SENTENCE}
    assert archive_status(config.load(), True)["reason"] == features.OFF_SENTENCE
    assert cli.main(["email", "batch", "mailboxes"]) == archive_gate.EXIT_CANNOT_START
    document = json.loads(capsys.readouterr().out)
    assert document["error"]["code"] == archive_gate.ERROR_OUTLOOK_UNAVAILABLE and "switched off" in document["error"]["message"]


def test_archive_on_again_reaches_its_routes(isolated: Path) -> None:
    switch(isolated, archive=False)
    with TestClient(app, client=LOCAL) as c:
        assert c.get("/api/archive/macro").status_code == 404
        c.post("/api/config", json={"features": {"archive": True}})
        assert c.get("/api/archive/macro").status_code != 404


def test_the_outlook_bridge_makes_no_call_while_archive_is_off(isolated: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from app import outlook

    switch(isolated, archive=False)
    assert outlook.main(["prepare", "--current"]) == outlook.EXIT_REFUSED
    assert json.loads(capsys.readouterr().out)["ok"] is False


# -------------------------------------------------------------------------- documents


def test_documents_off_refuses_the_viewer_search_and_association(isolated: Path) -> None:
    switch(isolated, documents=False)
    with TestClient(app, client=LOCAL) as c:
        assert c.get("/api/doc/recent").status_code == 404
        assert c.post("/api/doc/find", json={}).status_code == 404
        assert c.post("/api/doc/dropped/render", json={}).status_code == 404
        assert c.get("/api/search/docs").status_code == 404
        assert c.post("/api/md-assoc", json={"on": True}).status_code == 404
        assert c.get("/api/search/status").json()["docs"] == {"ready": False, "reason": features.OFF_SENTENCE}


def test_documents_on_again_restores_them(isolated: Path) -> None:
    switch(isolated, documents=False)
    with TestClient(app, client=LOCAL) as c:
        assert c.get("/api/doc/recent").status_code == 404
        c.post("/api/config", json={"features": {"documents": True}})
        assert c.get("/api/doc/recent").status_code == 200
        assert c.get("/api/search/docs").status_code == 200


# ------------------------------------------------------------------------- screenshot


def test_screenshot_off_never_loads_or_starts_the_service(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.tray import tray

    seen: list[str] = []
    switch(isolated, screenshot=False)
    assert tray.start_screenshot(seen.append) is None
    assert tray.screenshot_menu(None) == ()
    assert seen and "switched off" in seen[0]
    with TestClient(app, client=LOCAL) as c:
        assert c.post("/api/snap/open-folder").status_code == 404


def test_screenshot_on_starts_the_service_once(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.tray import tray
    from src.snap import service

    started: list[str] = []

    class FakeService:
        def start(self) -> None:
            started.append("start")

    monkeypatch.setattr(service, "SnapService", FakeService)
    monkeypatch.setattr(tray.threading, "Thread", lambda target, **_k: type("T", (), {"start": lambda self: target()})())
    snap: Any = tray.start_screenshot(lambda _m: None)
    assert isinstance(snap, FakeService) and started == ["start"]
