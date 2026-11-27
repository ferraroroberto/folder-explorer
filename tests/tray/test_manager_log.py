"""A second spawn with no ``stop()`` between (the watchdog's respawn) releases the first ``webapp.log`` handle."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.webapp import manager as manager_module
from src import store


@pytest.mark.parametrize("oversized", [False, True], ids=["small-log", "oversized-log"])
def test_a_respawn_closes_the_previous_log_handle(
    isolated: Path, monkeypatch: pytest.MonkeyPatch, oversized: bool
) -> None:
    handles: list[Any] = []

    def fake_popen(_cmd: object, **kwargs: Any) -> SimpleNamespace:
        handles.append(kwargs["stdout"])
        return SimpleNamespace(pid=100 + len(handles))

    monkeypatch.setattr(manager_module.subprocess, "Popen", fake_popen)
    # The named start lock is shared with a tray running on this PC.
    monkeypatch.setattr(manager_module, "cross_process_lock", lambda _name: nullcontext())
    monkeypatch.setattr(manager_module, "_LOG_MAX_BYTES", 10)
    manager = manager_module.WebappManager()

    manager.spawn()
    handles[0].write(b"x" * (100 if oversized else 1))
    handles[0].flush()
    try:
        # Windows refuses to unlink an open file, so an oversized log whose handle is still held raises here.
        assert manager.spawn() == 102
        assert handles[0].closed
        assert not handles[1].closed
        expected = 0 if oversized else 1
        assert (store.data_dir() / "webapp.log").stat().st_size == expected
    finally:
        for handle in handles:
            handle.close()
