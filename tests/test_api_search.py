"""The search API (parts status, folder and email search, one mail, Show all, PC-only actions, saved searches) and the folders CLI."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.cli.__main__ import main as cli
from app.webapp.routers import search as search_routes
from app.webapp.server import app
from app.webapp.state import EMAILS
from src import config
from src.email.body import Body
from tests.conftest import write_config
from tests.email_fixture import build_email_index
from tests.fixture_tree import build_tree

LOCAL = ("127.0.0.1", 50000)
REMOTE = ("100.64.0.7", 50000)


@pytest.fixture()
def setup(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scanned invented drive with an email folder inside it, and an email index over that folder."""
    drive = build_tree(isolated / "Invented drive")
    mail = drive / "Mail"
    index = build_email_index(isolated / "emails.db", mail)
    write_config(isolated, roots=[str(drive)], email={"index_path": str(index), "roots": [str(mail)]})
    EMAILS.close()
    with TestClient(app, client=LOCAL) as c:
        assert c.post("/api/scan", json={}).status_code == 202
        for _ in range(100):
            if not c.get("/api/scan").json()["scanning"]:
                break
            time.sleep(0.05)
    monkeypatch.setattr("src.email.settings.reader_installed", lambda: True)
    yield isolated
    EMAILS.close()


def test_status_says_why_a_part_is_off(isolated: Path) -> None:
    EMAILS.close()
    with TestClient(app, client=LOCAL) as c:
        body = c.get("/api/search/status").json()
        assert body["emails"]["ready"] is False and "email.roots" in body["emails"]["reason"]
        assert body["folders"]["ready"] is False and body["pc"] is True
        assert c.get("/api/search/emails", params={"q": "x"}).status_code == 503


def test_status_and_searches_when_set_up(setup: Path) -> None:
    with TestClient(app, client=LOCAL) as c:
        status = c.get("/api/search/status").json()
        assert status["emails"]["ready"] and status["emails"]["count"] == 5 and status["folders"]["ready"]
        folders = c.get("/api/search/folders", params={"q": "harbour"}).json()
        assert [r["name"] for r in folders["results"]] == ["harbour-trip"]
        hits = c.get("/api/search/emails", params={"q": "budget", "sort": "newest"}).json()
        assert hits["total"] == 3 and hits["facets"]["flagged"] == 1
        assert c.get("/api/search/emails", params={"q": "x", "sort": "bogus"}).status_code == 400


def test_one_mail_carries_its_place_in_the_tree(setup: Path) -> None:
    with TestClient(app, client=LOCAL) as c:
        first = c.get("/api/search/emails", params={"q": "invoice"}).json()["hits"][0]
        mail = c.get("/api/emails/%d" % first["id"], params={"q": "invoice"}).json()
        assert mail["readable"] is True and mail["location"]["folder"] > 0
        assert mail["location"]["file"]["name"].endswith(".msg")
        assert c.get("/api/emails/999").status_code == 404


def test_show_all_reads_only_inside_the_email_folders(setup: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    read: list[str] = []
    monkeypatch.setattr(search_routes, "fetch_body", lambda p: read.append(p) or Body(True, text="Full text"))
    with TestClient(app, client=LOCAL) as c:
        row = c.get("/api/search/emails").json()["hits"][0]["id"]
        assert c.get("/api/emails/%d/body" % row).json() == {"text": "Full text", "html": ""}
        assert len(read) == 1 and read[0].endswith(".msg")
        config.save({"email": {"index_path": str(setup / "emails.db"), "roots": [str(setup / "Invented drive" / "Photos")]}})
        refused = c.get("/api/emails/%d/body" % row)
        assert refused.status_code == 403 and refused.json()["error"] == "outside" and len(read) == 1


def test_show_all_reports_a_timeout_as_its_own_status(setup: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search_routes, "fetch_body", lambda p: Body(False, error="timeout", message="timed out"))
    with TestClient(app, client=LOCAL) as c:
        row = c.get("/api/search/emails").json()["hits"][0]["id"]
        r = c.get("/api/emails/%d/body" % row)
        assert r.status_code == 504 and r.json()["error"] == "timeout"


def test_pc_only_actions_refuse_other_devices(setup: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOLDER_EXPLORER_PASSWORD", "invented-password")
    monkeypatch.setenv("FOLDER_EXPLORER_TOKEN", "invented-token")
    opened: list[str] = []
    monkeypatch.setattr(search_routes, "open_in_outlook", opened.append)
    auth = {"Authorization": "Bearer invented-token"}
    with TestClient(app, client=REMOTE) as c:
        row = c.get("/api/search/emails", headers=auth).json()["hits"][0]["id"]
        assert c.post("/api/emails/%d/open" % row, headers=auth).status_code == 403
        assert c.post("/api/folders/open", json={"root": "x", "id": 0}, headers=auth).status_code == 403
        assert c.get("/api/search/status", headers=auth).json()["pc"] is False
    with TestClient(app, client=LOCAL) as c:
        assert c.post("/api/emails/%d/open" % row).json() == {"status": "opened"}
    assert len(opened) == 1


def test_find_in_mailbox_asks_the_bridge_by_message_id_on_the_pc_only(setup: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOLDER_EXPLORER_PASSWORD", "invented-password")
    monkeypatch.setenv("FOLDER_EXPLORER_TOKEN", "invented-token")
    calls: list[list[str]] = []
    monkeypatch.setattr(search_routes, "bridge", lambda args, timeout: calls.append(args) or {"ok": True, "folder": "Archive"})
    auth = {"Authorization": "Bearer invented-token"}
    with TestClient(app, client=REMOTE) as c:
        row = c.get("/api/search/emails", headers=auth).json()["hits"][0]["id"]
        assert c.post("/api/emails/%d/find" % row, headers=auth).status_code == 403
        assert c.get("/api/search/status", headers=auth).json()["archive"]["ready"] is False
    with TestClient(app, client=LOCAL) as c:
        assert c.post("/api/emails/%d/find" % row).json() == {"ok": True, "folder": "Archive"}
        assert c.post("/api/emails/99999/find").status_code == 404
    assert len(calls) == 1 and calls[0][0] == "show" and calls[0][1].startswith("--message-id=msg-")


def test_saved_searches_round_trip(isolated: Path) -> None:
    with TestClient(app, client=LOCAL) as c:
        saved = c.post("/api/searches", json={"mode": "emails", "name": "Flagged lately", "query": "is:flagged newer_than:6m"}).json()
        again = c.post("/api/searches", json={"mode": "emails", "name": "Renamed", "query": "is:flagged newer_than:6m"}).json()
        assert again["id"] == saved["id"] and again["name"] == "Renamed"
        assert c.post("/api/searches", json={"mode": "files", "query": "x"}).status_code == 400
        assert [s["query"] for s in c.get("/api/searches").json()["searches"]] == ["is:flagged newer_than:6m"]
        assert c.delete("/api/searches/%d" % saved["id"]).json() == {"deleted": True}
        assert c.delete("/api/searches/%d" % saved["id"]).status_code == 404


def test_config_validates_the_search_settings(isolated: Path) -> None:
    with TestClient(app, client=LOCAL) as c:
        ok = c.post("/api/config", json={"folder_search": {"skip_levels": 3, "prune_people": False}})
        assert ok.json()["config"]["folder_search"] == {"skip_levels": 3, "prune_people": False}
        assert c.post("/api/config", json={"folder_search": {"skip_levels": 99}}).status_code == 400
        assert c.post("/api/config", json={"email": {"index_path": 5}}).status_code == 400
        assert c.post("/api/config", json={"email": {"bogus": 1}}).status_code == 400


def test_the_folders_cli_prints_one_document(setup: Path, capsys: pytest.CaptureFixture[str]) -> None:
    capsys.readouterr()
    assert cli(["folders", "search", "--query", "harbour", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["schema_version"] == 1 and doc["query"] == "harbour" and doc["total"] == 1
    hit = doc["results"][0]
    assert hit["name"] == "harbour-trip" and Path(hit["path"]) == setup / "Invented drive" / "Photos" / "2024" / "harbour-trip"
    assert cli(["folders", "search", "--query", "2024", "--limit", "1", "--json"]) == 0
    one = json.loads(capsys.readouterr().out)
    assert len(one["results"]) == 1 and one["total"] > 1 and one["truncated"] is True
    assert cli(["folders", "search", "--query", " ; ", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "bad_query"


def test_the_folders_cli_says_when_nothing_is_scanned(isolated: Path, capsys: pytest.CaptureFixture[str]) -> None:
    capsys.readouterr()
    assert cli(["folders", "search", "--query", "harbour", "--json"]) == 4
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": 1, "error": {"code": "not_set_up", "message": "no root has a finished scan yet"}}
