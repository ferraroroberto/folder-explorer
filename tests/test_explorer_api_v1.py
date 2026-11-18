"""The versioned window API: the front File Explorer folder for a caller on this PC, each failure its own typed answer.

The window read is replaced in every test; no real window is ever asked.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.webapp.routers import explorer_v1
from app.webapp.server import app
from src.explorer_window import ExplorerUnavailableError
from tests.conftest import write_config

LOCAL = ("127.0.0.1", 50000)
URL = "/api/v1/explorer/folder"


@pytest.fixture()
def api(isolated: Path) -> TestClient:
    write_config(isolated, roots=[])
    with TestClient(app, client=LOCAL) as client:
        yield client


def _front(monkeypatch: pytest.MonkeyPatch, answer: object) -> list[int]:
    """Fake the window read to answer a folder, ``None`` or an exception; the list counts the reads."""
    reads: list[int] = []

    def read() -> object:
        reads.append(1)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(explorer_v1, "get_current_explorer_folder", read)
    return reads


def test_the_front_folder_is_answered_whatever_the_folder_is(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = str(Path("D:/Invented/Elsewhere/Project"))  # outside every email folder
    _front(monkeypatch, folder)

    resp = api.get(URL)

    assert resp.status_code == 200
    assert resp.json() == {"schema_version": 1, "state": "ok", "folder_path": folder}


def test_no_open_folder_window_is_not_an_unreadable_shell(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _front(monkeypatch, None)
    none = api.get(URL)
    _front(monkeypatch, ExplorerUnavailableError("the shell did not answer"))
    cannot = api.get(URL)

    assert none.status_code == 404
    assert none.json()["code"] == "no_explorer_folder" and none.json()["state"] == "error" and none.json()["reason"]
    assert cannot.status_code == 502
    assert cannot.json()["code"] == "explorer_unavailable" and "the shell did not answer" in cannot.json()["reason"]
    assert none.json()["schema_version"] == cannot.json()["schema_version"] == 1


def test_a_caller_off_this_pc_is_refused_and_no_window_is_read(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    reads = _front(monkeypatch, str(Path("D:/Invented/Elsewhere")))
    monkeypatch.setattr(explorer_v1, "is_pc_itself", lambda _request: False)

    resp = api.get(URL)

    assert resp.status_code == 403
    assert resp.json() == {"schema_version": 1, "state": "error", "code": "not_this_pc", "reason": resp.json()["reason"]}
    assert resp.json()["reason"] and reads == []
