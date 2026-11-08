"""The per-stage timing line: milliseconds and counts only, in the order the stages ran."""

from __future__ import annotations

import logging
import sys

import pytest

from src.stage_timer import StageTimer, process_age_ms


def test_the_line_lists_each_stage_its_total_and_the_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter([0.0, 0.010, 0.036])
    monkeypatch.setattr("src.stage_timer.time.perf_counter", lambda: next(clock))
    timer = StageTimer("archive")
    timer.add("startup", 120.0)
    assert round(timer.mark("attach")) == 10 and round(timer.mark("read_mail")) == 26
    timer.count("suggested", 3)
    assert timer.line() == "archive ms: startup=120 attach=10 read_mail=26 total=156 suggested=3"


def test_log_writes_one_info_line(caplog: pytest.LogCaptureFixture) -> None:
    timer = StageTimer("archive ranking")
    timer.mark("fts_pool")
    with caplog.at_level(logging.INFO):
        timer.log(logging.getLogger("t"))
    assert len(caplog.records) == 1 and caplog.records[0].getMessage().startswith("⏱️ archive ranking ms: fts_pool=")


@pytest.mark.skipif(sys.platform != "win32", reason="the process's creation time is read from Windows")
def test_the_process_age_is_a_positive_number_of_milliseconds() -> None:
    age = process_age_ms()
    assert age is not None and 0 < age < 24 * 3600 * 1000
