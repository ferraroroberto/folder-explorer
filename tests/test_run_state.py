"""The run state: every observation of a run's row and lock, the impossible ones, and the lock probe."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import pytest

from src import runtime_files
from src.run_state import MEANING, STOPPED_EARLY, Derived, RunState, derive_run_state
from src.scan_lock import ScanBusy, ScanLock, probe_lock

T, F, U = True, False, None

# (row status, row message, lock held, any run, state, reason)
TABLE: list[tuple[Optional[str], Optional[str], Optional[bool], bool, RunState, Optional[str]]] = [
    # a row's own question: how did it end
    ("running", None, T, False, RunState.RUNNING, None),
    ("running", None, F, False, RunState.INTERRUPTED, STOPPED_EARLY),
    ("running", "stopped by a restart", F, False, RunState.INTERRUPTED, "stopped by a restart"),
    ("running", None, U, False, RunState.UNKNOWN, "the lock could not be read"),
    ("ok", None, F, False, RunState.OK, None),
    ("ok", "3 new", T, False, RunState.OK, None),
    ("ok", None, U, False, RunState.OK, None),
    ("failed", "disk gone", F, False, RunState.FAILED, "disk gone"),
    ("failed", None, T, False, RunState.FAILED, "the run failed"),
    ("failed", "disk gone", U, False, RunState.FAILED, "disk gone"),
    ("interrupted", None, F, False, RunState.INTERRUPTED, STOPPED_EARLY),
    ("interrupted", "the scan stopped before it finished", U, False, RunState.INTERRUPTED,
     "the scan stopped before it finished"),
    # no row yet: the lock alone says whether something is starting
    (None, None, F, False, RunState.IDLE, None),
    (None, None, T, False, RunState.RUNNING, None),
    (None, None, U, False, RunState.UNKNOWN, "the lock could not be read"),
    # is any run active: a held lock is a run, even over a settled row (between two roots)
    ("ok", None, T, True, RunState.RUNNING, None),
    ("failed", "disk gone", T, True, RunState.RUNNING, None),
    ("ok", None, F, True, RunState.OK, None),
    ("failed", "disk gone", F, True, RunState.FAILED, "disk gone"),
    ("ok", None, U, True, RunState.UNKNOWN, "the lock could not be read"),
    ("running", None, F, True, RunState.INTERRUPTED, STOPPED_EARLY),
    (None, None, F, True, RunState.IDLE, None),
]


@pytest.mark.parametrize("row,message,lock,any_run,state,reason", TABLE)
def test_every_observation_has_one_state(row: Optional[str], message: Optional[str], lock: Optional[bool], any_run: bool,
                                         state: RunState, reason: Optional[str]) -> None:
    assert derive_run_state(row, message, lock, any_run=any_run) == Derived(state, reason)


def test_the_state_set_is_closed_and_each_state_says_what_it_means() -> None:
    assert {s.value for s in RunState} == {"idle", "running", "ok", "failed", "interrupted", "unknown"}
    assert set(MEANING) == set(RunState) and all(MEANING.values())


def test_a_row_naming_a_state_the_app_never_writes_is_unknown_not_a_guess() -> None:
    # state-machine-proof: run-state
    for odd in ("paused", "Running", "idle", "unknown", ""):
        for lock in (T, F, U):
            got = derive_run_state(odd, None, lock)
            assert got.state is RunState.UNKNOWN and odd in (got.reason or "")


def test_a_dead_process_cannot_stay_running_and_an_unreadable_lock_is_never_idle() -> None:
    # state-machine-proof: run-state
    for any_run in (False, True):
        assert derive_run_state("running", None, F, any_run=any_run).state is RunState.INTERRUPTED
        assert derive_run_state(None, None, U, any_run=any_run).state is RunState.UNKNOWN
    assert derive_run_state("running", None, U).state is RunState.UNKNOWN


def test_failed_interrupted_and_unknown_always_carry_a_reason() -> None:
    for row in (None, "running", "ok", "failed", "interrupted"):
        for lock in (T, F, U):
            for any_run in (False, True):
                got = derive_run_state(row, None, lock, any_run=any_run)
                if got.state in (RunState.FAILED, RunState.INTERRUPTED, RunState.UNKNOWN):
                    assert got.reason
                else:
                    assert got.reason is None


def test_probe_lock_tells_free_held_and_unreadable_apart(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = isolated / "data" / "probe.lock"
    assert probe_lock(path) is False
    with ScanLock(path):
        assert probe_lock(path) is True
        with pytest.raises(ScanBusy) as busy:
            ScanLock(path).acquire()
        assert busy.value.held is True
    assert probe_lock(path) is False

    def refuse(*_a: Any, **_k: Any) -> Any:
        raise PermissionError("invented: the lock file is not readable")

    monkeypatch.setattr(runtime_files, "open_file", refuse)
    assert probe_lock(path) is None
