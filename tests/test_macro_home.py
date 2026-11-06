"""The macro sheet's setup line: the primary checkout (never a worktree), the user variable's state, the PC-only route."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.webapp.server import app
from src import macro_home

LOCAL = ("127.0.0.1", 50000)
REMOTE = ("100.64.0.7", 50000)


def test_a_plain_checkout_is_its_own_primary(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    assert macro_home.primary_checkout(tmp_path) == tmp_path


def test_a_worktree_resolves_to_the_primary_checkout(tmp_path: Path) -> None:
    primary = tmp_path / "projects" / "sample-app"
    worktree = tmp_path / "projects" / "sample-app-wt-9"
    (primary / ".git" / "worktrees" / "sample-app-wt-9").mkdir(parents=True)
    worktree.mkdir()
    gitdir = primary / ".git" / "worktrees" / "sample-app-wt-9"
    (worktree / ".git").write_text(f"gitdir: {gitdir.as_posix()}\n", encoding="utf-8")
    assert macro_home.primary_checkout(worktree) == primary


@pytest.mark.parametrize("content", ["", "not a pointer\n", "gitdir: /elsewhere/.git/modules/x\n"])
def test_an_unrecognised_git_file_keeps_the_running_folder(tmp_path: Path, content: str) -> None:
    (tmp_path / ".git").write_text(content, encoding="utf-8")
    assert macro_home.primary_checkout(tmp_path) == tmp_path


@pytest.mark.parametrize(
    ("current", "state"),
    [
        (None, macro_home.STATE_UNKNOWN),
        ("", macro_home.STATE_UNSET),
        ("   ", macro_home.STATE_UNSET),
        (r"D:\Work\Sample-App", macro_home.STATE_OK),
        ("d:/work/sample-app/", macro_home.STATE_OK),
        (r'"D:\Work\Sample-App"', macro_home.STATE_OK),
        (r"D:\Work\Other", macro_home.STATE_DIFFERENT),
    ],
)
def test_the_state_tells_unset_from_unknown_from_another_folder(current: str | None, state: str) -> None:
    assert macro_home.state_for(current, r"D:\Work\Sample-App") == state


def test_the_setup_is_a_setx_line_for_the_primary_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(macro_home, "read_user_variable", lambda: "")
    doc = macro_home.setup(tmp_path)
    assert doc["command"] == f'setx FOLDER_EXPLORER_HOME "{tmp_path}"'
    assert doc["home"] == str(tmp_path) and doc["current"] is None and doc["state"] == "unset"


def test_the_setup_never_writes_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(macro_home, "read_user_variable", lambda: None)
    monkeypatch.delenv(macro_home.VARIABLE, raising=False)
    assert macro_home.setup(tmp_path)["state"] == "unknown"
    assert macro_home.VARIABLE not in os.environ


def test_the_route_gives_the_setup_to_this_pc_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(macro_home, "read_user_variable", lambda: "")
    with TestClient(app, client=LOCAL) as c:
        doc = c.get("/api/archive/macro").json()
    assert doc["setup"]["command"].startswith("setx FOLDER_EXPLORER_HOME ") and doc["setup"]["state"] == "unset"
    with TestClient(app, client=REMOTE) as c:
        remote = c.get("/api/archive/macro", headers={"Authorization": "Bearer nope"})
    assert remote.status_code in (200, 401, 403)
    if remote.status_code == 200:
        assert remote.json()["setup"] is None
