"""The "Open with" launcher: a file named by its scanned folder, never a path; a file newer than the scan; the recent list.

The launcher's request goes to the app through a TestClient, and its window
opens through a recorder in place of ``src.desktop.open_app_window``, so
nothing starts on this PC.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import launcher
from app.webapp.server import app
from src import desktop
from src.docs.launch import Place
from tests.fixture_tree import DOCS_FOLDER

LOCAL = ("127.0.0.1", 50000)
REMOTE = ("100.64.0.7", 50000)


class Run:
    """One launch: what it sent the app, what it told the user, the window it asked for."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.sent: list[dict[str, Any]] = []
        self.told: list[str] = []
        self.windows: list[str] = []

    def send(self, place: Place) -> Any:
        self.sent.append(place.as_request())
        return self.client.post("/api/doc/find", json=place.as_request())

    def __call__(self, path: Path) -> int:
        return launcher.launch(str(path), send=self.send, tell=self.told.append)


@pytest.fixture()
def run(docs: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> Any:
    with TestClient(app, client=LOCAL) as client:
        r = Run(client)
        monkeypatch.setattr(desktop, "open_app_window", r.windows.append)
        yield r


def _opened(r: Run) -> dict[str, Any]:
    """The document the one window asked for, read back through the page's own route."""
    (url,) = r.windows
    assert url.startswith("http://127.0.0.1:") and "/doc?" in url
    return r.client.get("/api/doc?" + url.split("?", 1)[1]).json()


def test_an_indexed_file_opens_in_a_document_window(docs: dict[str, Any], run: Run) -> None:
    assert run(Path(docs["path"]) / DOCS_FOLDER / "guide.md") == 0
    assert run.told == []
    doc = _opened(run)
    assert doc["name"] == "guide.md" and doc["id"] == docs["ids"]["guide.md"]


def test_the_launcher_never_sends_a_path(docs: dict[str, Any], run: Run) -> None:
    run(Path(docs["path"]) / DOCS_FOLDER / "sub" / "watering.md")
    (request,) = run.sent
    assert set(request) == {"root", "scan_id", "folder", "name"}
    assert request["name"] == "watering.md" and isinstance(request["folder"], int)
    text = json.dumps(request)
    assert "\\" not in text and "/" not in text and DOCS_FOLDER not in text
    assert os.path.basename(docs["path"]) not in text


def test_a_file_newer_than_the_scan_opens_and_keeps_its_number(docs: dict[str, Any], run: Run) -> None:
    fresh = Path(docs["path"]) / DOCS_FOLDER / "fresh idea.md"
    fresh.write_text("# Fresh idea\n\nWritten after the scan.\n", encoding="utf-8")
    assert run(fresh) == 0
    doc = _opened(run)
    assert doc["name"] == "fresh idea.md" and doc["id"] < 0 and "Written after the scan" in doc["html"]
    assert doc["folder"][-1] == DOCS_FOLDER
    again = run.client.post("/api/doc/find", json=run.sent[0]).json()
    assert again["id"] == doc["id"]
    fresh.unlink()
    gone = run.client.get("/api/doc", params={"root": docs["root"], "scan": docs["scan"], "id": doc["id"]})
    assert gone.status_code == 410 and gone.json()["reason"] == "gone"


def test_find_answers_this_pc_only_and_markdown_only(docs: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    with TestClient(app, client=LOCAL) as c:
        text = c.post("/api/doc/find", json={"root": docs["root"], "scan_id": docs["scan"], "folder": 0, "name": "notes.txt"})
    assert text.status_code == 415 and text.json()["reason"] == "not_document"
    monkeypatch.setenv("FOLDER_EXPLORER_PASSWORD", "invented-password")
    monkeypatch.setenv("FOLDER_EXPLORER_TOKEN", "invented-token")
    with TestClient(app, client=REMOTE) as c:
        r = c.post("/api/doc/find", json={"root": docs["root"], "scan_id": docs["scan"], "folder": 0, "name": "notes.md"},
                   headers={"Authorization": "Bearer invented-token"})
    assert r.status_code == 403 and r.json()["reason"] == "pc_only"


@pytest.mark.parametrize("name", ["..\\guide.md", "../guide.md", "C:guide.md", "sub\\watering.md", "..", "", None, 7])
def test_find_takes_a_bare_name_only(docs: dict[str, Any], name: object) -> None:
    with TestClient(app, client=LOCAL) as c:
        r = c.post("/api/doc/find", json={"root": docs["root"], "scan_id": docs["scan"], "folder": 0, "name": name})
    assert r.status_code == 404 and r.json()["reason"] == "not_found"


def test_find_refuses_a_stale_scan_and_a_missing_file(docs: dict[str, Any]) -> None:
    with TestClient(app, client=LOCAL) as c:
        stale = c.post("/api/doc/find", json={"root": docs["root"], "scan_id": docs["scan"] - 1, "folder": 0, "name": "a.md"})
        missing = c.post("/api/doc/find", json={"root": docs["root"], "scan_id": docs["scan"], "folder": 0, "name": "nowhere.md"})
    assert stale.status_code == 409 and stale.json()["reason"] == "scan_changed"
    assert missing.status_code == 410 and missing.json()["reason"] == "gone"


def test_a_file_outside_every_root_or_not_markdown_is_told_not_sent(docs: dict[str, Any], run: Run, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere" / "loose.md"
    outside.parent.mkdir()
    outside.write_text("# Loose\n", encoding="utf-8")
    new_folder = Path(docs["path"]) / "Made later"
    new_folder.mkdir()
    (new_folder / "plan.md").write_text("# Plan\n", encoding="utf-8")
    assert run(outside) == 1
    assert run(Path(docs["path"]) / "notes.txt") == 1
    assert run(new_folder / "plan.md") == 1
    assert run.sent == [] and run.windows == []
    assert "no folder folder-explorer scans" in run.told[0]
    assert "Only Markdown" in run.told[1]
    assert "newer than the last scan" in run.told[2]


def test_an_app_that_is_not_running_is_said_so(docs: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    def down(_place: Place) -> Any:
        raise requests.ConnectionError("refused")

    told: list[str] = []
    monkeypatch.setattr(desktop, "open_app_window", lambda url: pytest.fail("a window was opened"))
    assert launcher.launch(str(Path(docs["path"]) / DOCS_FOLDER / "guide.md"), send=down, tell=told.append) == 1
    assert told == [launcher.NOT_RUNNING]


def _scan(c: TestClient) -> None:
    assert c.post("/api/scan", json={}).status_code == 202
    for _ in range(200):
        if not c.get("/api/scan").json()["scanning"]:
            return
        time.sleep(0.05)


def test_recent_lists_the_last_opened_and_survives_a_restart_and_a_rescan(docs: dict[str, Any]) -> None:
    q = {"root": docs["root"], "scan": docs["scan"]}
    with TestClient(app, client=LOCAL) as c:
        for name in ("guide.md", "watering.md", "guide.md", "journal.md"):
            assert c.get("/api/doc", params={**q, "id": docs["ids"][name]}).status_code == 200
        assert c.get("/api/doc", params={**q, "id": docs["ids"]["big.md"]}).status_code == 413
    with TestClient(app, client=LOCAL) as c:  # a new app process reads the same runtime data
        names = [d["name"] for d in c.get("/api/doc/recent").json()["recent"]]
        assert names == ["journal.md", "guide.md", "watering.md"]
        _scan(c)
        recent = c.get("/api/doc/recent").json()["recent"]
    assert recent[0]["scan_id"] > docs["scan"]
    assert recent[0]["folder"][-1] == DOCS_FOLDER


def test_open_app_window_uses_edge_or_the_default_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(desktop, "_spawn", lambda command: calls.append(("spawn", command)))
    monkeypatch.setattr(desktop, "_shell", lambda path, verb: calls.append(("shell", (path, verb))))
    monkeypatch.setattr(desktop, "_check", lambda: None)
    monkeypatch.setattr(desktop, "_app_exe", lambda exe: r"X:\Invented\msedge.exe" if exe == "msedge.exe" else None)
    desktop.open_app_window("http://127.0.0.1:1/doc?id=1")
    monkeypatch.setattr(desktop, "_app_exe", lambda exe: None)
    desktop.open_app_window("http://127.0.0.1:1/doc?id=2")
    assert calls == [("spawn", [r"X:\Invented\msedge.exe", "--app=http://127.0.0.1:1/doc?id=1"]),
                     ("shell", ("http://127.0.0.1:1/doc?id=2", "open"))]


def test_open_app_window_is_refused_when_opening_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(desktop.DESKTOP_ENV, "off")
    monkeypatch.setattr(desktop, "_app_exe", lambda exe: pytest.fail("looked for a browser"))
    with pytest.raises(FileNotFoundError):
        desktop.open_app_window("http://127.0.0.1:1/doc")
