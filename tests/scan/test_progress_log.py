"""The scan's heartbeat: a log line at a bounded interval while a root is walked, and each root's result as it ends."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from src import scan_runner, store
from src.roots import Root
from src.scanner import Counters
from tests.conftest import write_config
from tests.fixture_tree import build_tree


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _walk_of(clock: _Clock, ticks: int, step: float):
    """A stand-in walk that reports progress ``ticks`` times, ``step`` seconds apart on ``clock``."""

    def fake(root_path: str, index_path: Path, *, on_progress, **_: object) -> Counters:
        counters = Counters()
        for _i in range(ticks):
            clock.now += step
            counters.files += 1000
            counters.folders += 10
            on_progress(counters.as_dict())
        return counters

    return fake


def test_a_long_root_logs_one_progress_line_per_interval(isolated: Path, monkeypatch: pytest.MonkeyPatch,
                                                         caplog: pytest.LogCaptureFixture) -> None:
    clock = _Clock()
    # 300 reports, 1 s apart: five minutes of walking.
    monkeypatch.setattr(scan_runner, "scan_to_index", _walk_of(clock, ticks=300, step=1.0))
    root = Root(key="invented", name="Invented", path=str(isolated / "Invented"), kind="local")
    with caplog.at_level(logging.INFO, logger=scan_runner.logger.name):
        scan_runner._scan_root(root, "cli", clock=clock, log_every=60.0)
    progress = [r.getMessage() for r in caplog.records if "so far" in r.getMessage()]
    assert len(progress) == 5
    assert "root invented: " in progress[0] and "60 s elapsed" in progress[0] and "60000 files so far" in progress[0]
    assert "300 s elapsed" in progress[-1]
    assert str(isolated) not in caplog.text


def test_a_walk_shorter_than_the_interval_logs_no_progress_line(isolated: Path, monkeypatch: pytest.MonkeyPatch,
                                                                caplog: pytest.LogCaptureFixture) -> None:
    clock = _Clock()
    monkeypatch.setattr(scan_runner, "scan_to_index", _walk_of(clock, ticks=30, step=1.0))
    root = Root(key="invented", name="Invented", path=str(isolated / "Invented"), kind="local")
    with caplog.at_level(logging.INFO, logger=scan_runner.logger.name):
        scan_runner._scan_root(root, "cli", clock=clock, log_every=60.0)
    assert not [r for r in caplog.records if "so far" in r.getMessage()]


def test_each_root_result_is_logged_before_the_next_root_starts(isolated: Path, caplog: pytest.LogCaptureFixture) -> None:
    write_config(isolated, roots=[str(build_tree(isolated / "First")), str(build_tree(isolated / "Second"))])
    with caplog.at_level(logging.INFO, logger=scan_runner.logger.name):
        results = scan_runner.run_scans()
    assert [r["status"] for r in results] == [store.STATUS_OK, store.STATUS_OK]
    marks = [m for m in (r.getMessage() for r in caplog.records) if m.startswith(("ℹ️ scan", "✅ scan"))]
    kinds = [("start" if "started" in m else "done") for m in marks]
    assert kinds == ["start", "done", "start", "done"]
