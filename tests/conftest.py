"""Point every test at a throwaway data folder and config before anything imports the app.

Set at import time, not in a fixture, because ``app.webapp.server`` builds its
usage-log router (and so resolves the data folder) when it is first imported.
"""

from __future__ import annotations

import json
import os
import tempfile
import tkinter as tk
from pathlib import Path
from typing import Iterator

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
def no_window_watch(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test lists this PC's Explorer windows or starts the thread that waits for one."""
    from src import window_front

    monkeypatch.setattr(window_front, "explorer_hwnds", set)
    monkeypatch.setattr(window_front, "follow_in_background", lambda *_a, **_k: None)


@pytest.fixture(autouse=True)
def fresh_lockout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new lock-out per test, and no real notifier, so no test sends a Telegram message."""
    from app.webapp import auth

    monkeypatch.setattr(auth, "_lockout", auth.make_lockout())
    monkeypatch.setattr(auth, "alert_notifier", lambda: None)


# ------------------------------------------------------------------ Tk windows

_MAPPED: list[str] = []


@pytest.fixture(scope="session")
def tk_root() -> Iterator[tk.Tk]:
    """The one Tk root every window test shares, kept withdrawn: Tk can't reliably be started twice in a process.

    A top-level the tests map is a real window on the owner's desktop, so every map is recorded
    and :func:`no_window_mapped` fails the test that caused it.
    """
    try:
        shared = tk.Tk()
    except tk.TclError as exc:
        pytest.skip("Tk can't open a window here: %s" % exc)
    shared.withdraw()

    def record(event: tk.Event) -> None:
        if isinstance(event.widget, (tk.Tk, tk.Toplevel)):
            _MAPPED.append(str(event.widget))

    shared.bind_all("<Map>", record)
    yield shared
    shared.destroy()


@pytest.fixture()
def mapped_windows() -> list[str]:
    """The top-level windows mapped since the test began; a test that expects one clears it."""
    return _MAPPED


@pytest.fixture(autouse=True)
def no_window_mapped() -> Iterator[None]:
    """A gate run opens no window on the desktop: a test that maps one fails here, whatever it asserts."""
    _MAPPED.clear()
    yield
    assert not _MAPPED, "the test mapped a window on the desktop: %s" % ", ".join(_MAPPED)
