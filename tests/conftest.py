"""Point every test at a throwaway data folder and config before anything imports the app.

Set at import time, not in a fixture, because ``app.webapp.server`` builds its
usage-log router (and so resolves the data folder) when it is first imported.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

_SESSION_DIR = Path(tempfile.mkdtemp(prefix="folder-explorer-tests-"))
os.environ["FOLDER_EXPLORER_DATA_DIR"] = str(_SESSION_DIR / "data")
os.environ["FOLDER_EXPLORER_CONFIG"] = str(_SESSION_DIR / "config.json")
os.environ.pop("FOLDER_EXPLORER_PASSWORD", None)
os.environ.pop("FOLDER_EXPLORER_TOKEN", None)


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh data folder and config file for one test; returns the temp folder."""
    monkeypatch.setenv("FOLDER_EXPLORER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FOLDER_EXPLORER_CONFIG", str(tmp_path / "config.json"))
    return tmp_path


def write_config(tmp_path: Path, **values: object) -> None:
    (tmp_path / "config.json").write_text(json.dumps(values), encoding="utf-8")


@pytest.fixture(autouse=True)
def isolated_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reads the live ``.env``: its password and Telegram keys stay out of the run."""
    from src import secrets_env

    monkeypatch.setattr(secrets_env, "env_path", lambda: tmp_path / ".env")


@pytest.fixture(autouse=True)
def fresh_lockout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new lock-out per test, and no real notifier, so no test sends a Telegram message."""
    from app.webapp import auth

    monkeypatch.setattr(auth, "_lockout", auth.make_lockout())
    monkeypatch.setattr(auth, "alert_notifier", lambda: None)
