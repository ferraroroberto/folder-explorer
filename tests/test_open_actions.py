"""Opening an item on this PC: the verb mapping, the off switch, and every refusal of ``POST /api/open``.

No test here launches anything: the two launch seams in ``src.desktop`` are
replaced by recorders, and the off-switch test makes ``subprocess.Popen`` and
``os.startfile`` fail loudly if they are ever reached.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.webapp.routers.archive import with_locations
from app.webapp.server import app
from app.webapp.state import locate_folder
from src import config, desktop
from src.roots import resolve_roots
from tests.conftest import write_config
from tests.fixture_tree import build_tree

LOCAL = ("127.0.0.1", 50000)
REMOTE = ("100.64.0.7", 50000)
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def launched(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(desktop, "_spawn", lambda command: calls.append(("spawn", command)))
    monkeypatch.setattr(desktop, "_shell", lambda path, verb: calls.append(("shell", (path, verb))))
    return calls


def test_each_verb_maps_to_its_launch(launched: list[tuple[str, Any]]) -> None:
    desktop.open_item(r"X:\Invented\Photos", "explore")
    desktop.open_item(r"X:\Invented\notes.txt", "open")
    desktop.open_item(r"X:\Invented\notes.txt", "edit")
    desktop.open_item(r"X:\Invented folder\notes.txt", "reveal")
    explore, opened, edited, revealed = launched
    assert explore == ("shell", (r"X:\Invented\Photos", "explore"))
    assert opened == ("shell", (r"X:\Invented\notes.txt", "open"))
    assert edited == ("shell", (r"X:\Invented\notes.txt", "edit"))
    assert revealed[0] == "spawn" and revealed[1].endswith(r'explorer.exe" /select,"X:\Invented folder\notes.txt"')
    with pytest.raises(ValueError):
        desktop.open_item(r"X:\Invented\notes.txt", "delete")


def test_the_off_switch_refuses_before_anything_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    def never(*_a: Any, **_k: Any) -> None:
        raise AssertionError("a launch was reached")

    monkeypatch.setattr(subprocess, "Popen", never)
    monkeypatch.setattr(os, "startfile", never, raising=False)
    monkeypatch.setenv(desktop.DESKTOP_ENV, "off")
    for verb in ("explore", "open", "edit", "reveal"):
        with pytest.raises(FileNotFoundError, match="off"):
            desktop.open_item(r"X:\Invented\notes.txt", verb)


def test_only_the_desktop_module_launches_anything() -> None:
    offenders = []
    for folder in ("src", "app"):
        for path in (ROOT / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if ("startfile" in text or "explorer.exe" in text) and path != ROOT / "src" / "desktop.py":
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


@pytest.fixture()
def scanned(isolated: Path) -> dict[str, Any]:
    write_config(isolated, roots=[str(build_tree(isolated / "Invented root"))])
    with TestClient(app, client=LOCAL) as c:
        assert c.post("/api/scan", json={}).status_code == 202
        for _ in range(100):
            if not c.get("/api/scan").json()["scanning"]:
                break
            time.sleep(0.05)
        root = c.get("/api/roots").json()["roots"][0]
        body = c.get("/api/folder", params={"root": root["key"]}).json()
    ids = {it["name"]: it["id"] for it in body["items"]}
    return {"root": root["key"], "path": root["path"], "scan_id": body["scan_id"], "ids": ids,
            "can_open": body["can_open"]}


def _ask(c: TestClient, s: dict[str, Any], kind: str, name: str, verb: str, **over: Any) -> Any:
    payload = {"root": s["root"], "scan_id": s["scan_id"], "kind": kind, "id": s["ids"][name], "verb": verb, **over}
    return c.post("/api/open", json=payload)


def test_open_runs_the_verb_on_the_item_looked_up_by_id(scanned: dict[str, Any], launched: list[tuple[str, Any]]) -> None:
    assert scanned["can_open"] is True
    with TestClient(app, client=LOCAL) as c:
        assert _ask(c, scanned, "folder", "Photos", "explore").json() == {"status": "opened"}
        assert _ask(c, scanned, "file", "notes.txt", "open").status_code == 200
        assert _ask(c, scanned, "file", "notes.txt", "edit").status_code == 200
        assert _ask(c, scanned, "file", "notes.txt", "reveal").status_code == 200
    notes = os.path.join(scanned["path"], "notes.txt")
    assert launched[0] == ("shell", (os.path.join(scanned["path"], "Photos"), "explore"))
    assert launched[1] == ("shell", (notes, "open")) and launched[2] == ("shell", (notes, "edit"))
    assert launched[3][1].endswith('/select,"' + notes + '"')


def test_open_refuses_remote_callers_and_bad_requests(scanned: dict[str, Any], launched: list[tuple[str, Any]],
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOLDER_EXPLORER_PASSWORD", "invented-password")
    monkeypatch.setenv("FOLDER_EXPLORER_TOKEN", "invented-token")
    auth = {"Authorization": "Bearer invented-token"}
    with TestClient(app, client=REMOTE) as c:
        assert c.get("/api/folder", params={"root": scanned["root"]}, headers=auth).json()["can_open"] is False
        payload = {"root": scanned["root"], "scan_id": scanned["scan_id"], "kind": "folder",
                   "id": scanned["ids"]["Photos"], "verb": "explore"}
        assert c.post("/api/open", json=payload, headers=auth).status_code == 403
    with TestClient(app, client=LOCAL) as c:
        assert _ask(c, scanned, "folder", "Photos", "open").status_code == 400
        assert _ask(c, scanned, "file", "notes.txt", "explore").status_code == 400
        assert _ask(c, scanned, "file", "notes.txt", "run").status_code == 400
        assert _ask(c, scanned, "drive", "notes.txt", "open").status_code == 400
        assert _ask(c, scanned, "file", "notes.txt", "open", root="elsewhere").status_code == 404
        assert _ask(c, scanned, "file", "notes.txt", "open", id=999_999).status_code == 404
        assert _ask(c, scanned, "folder", "Photos", "explore", id=999_999).status_code == 404
        assert _ask(c, scanned, "file", "notes.txt", "open", id=True).status_code == 404
        assert _ask(c, scanned, "file", "notes.txt", "open", id="1").status_code == 404
        assert _ask(c, scanned, "file", "notes.txt", "open", scan_id=scanned["scan_id"] + 1).status_code == 409
    assert launched == []


def test_open_says_why_nothing_opened(scanned: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def no_verb(path: str, verb: str) -> None:
        raise OSError("No application is associated with this action")

    monkeypatch.setattr(desktop, "_shell", no_verb)
    with TestClient(app, client=LOCAL) as c:
        refused = _ask(c, scanned, "file", "notes.txt", "edit")
        assert refused.status_code == 422 and "edit" in refused.json()["detail"]
        monkeypatch.setenv(desktop.DESKTOP_ENV, "off")
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("a launch was reached"))
        off = _ask(c, scanned, "folder", "Photos", "explore")
        assert off.status_code == 503 and off.json()["detail"] == desktop.OFF_REASON
        os.remove(os.path.join(scanned["path"], "notes.txt"))
        assert _ask(c, scanned, "file", "notes.txt", "open").status_code == 410


def test_an_offered_archive_folder_opens_by_its_place_in_the_scan(scanned: dict[str, Any], launched: list[tuple[str, Any]]) -> None:
    roots = resolve_roots(config.load()["roots"])
    # The separator the stored path uses does not matter: Windows paths mix them.
    wanted = scanned["path"].replace("\\", "/") + "/Photos/2024"
    loc = locate_folder(roots, wanted)
    assert loc is not None and loc["root"] == scanned["root"] and loc["scan_id"] == scanned["scan_id"]
    assert locate_folder(roots, os.path.join(scanned["path"], "Made after the scan")) is None
    doc = with_locations({"ok": True, "suggestions": [{"folder_path": wanted}, {"folder_path": "Z:/elsewhere"}]})
    assert doc["suggestions"][0]["location"] == loc and doc["suggestions"][1]["location"] is None
    with TestClient(app, client=LOCAL) as c:
        r = c.post("/api/open", json={"kind": "folder", "verb": "explore", **loc})
    assert r.json() == {"status": "opened"}
    assert launched[0] == ("shell", (os.path.join(scanned["path"], "Photos", "2024"), "explore"))
