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
