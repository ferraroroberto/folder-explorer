"""The build readout's restart route is mounted behind the sign-in gate.

`src/self_restart.py` is vendored verbatim and tested in project-scaffolding;
this covers the app-side wiring. No test posts a restart from the PC itself,
because that would run the real `tray.bat --restart`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.webapp.server import app

LOCAL = ("127.0.0.1", 50000)
REMOTE = ("100.64.0.7", 50000)
ORIGIN = {"Origin": "http://testserver"}


def test_the_app_declares_the_tray_recipe_as_its_only_target(isolated: Path) -> None:
    with TestClient(app, client=LOCAL) as c:
        res = c.get("/api/restart")
    assert res.status_code == 200
    assert [t["id"] for t in res.json()["targets"]] == ["app"]


def test_a_remote_caller_without_the_token_cannot_read_or_start_a_restart(isolated: Path) -> None:
    with TestClient(app, client=REMOTE) as c:
        assert c.get("/api/restart").status_code == 401
        assert c.post("/api/restart", json={"target": "app"}, headers=ORIGIN).status_code == 401


def test_the_recipe_runs_the_repos_own_tray_bat() -> None:
    from src.self_restart import TRAY_RESTART

    assert TRAY_RESTART[-2:] == (".\\tray.bat", "--restart")
    assert (Path(__file__).resolve().parents[2] / "tray.bat").is_file()
