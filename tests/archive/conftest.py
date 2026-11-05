"""Every archive test gets its own data folder, config file and no mailbox registry.

The index the batch verbs open is this app's own, in the runtime-data folder,
so each test points that folder at its temp folder; ``mailboxes.json`` is
machine-local and must never change what a test does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.email import mailboxes


@pytest.fixture(autouse=True)
def _isolated_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOLDER_EXPLORER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FOLDER_EXPLORER_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setattr(mailboxes, "mailboxes_path", lambda: tmp_path / "no-mailboxes.json")
