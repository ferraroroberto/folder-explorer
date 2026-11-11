"""The run state: whether a disk scan or an email-stage run is running, finished, failed or died.

A run leaves two traces that can disagree: the ``status`` of its row in
``state.sqlite3`` (``scans`` / ``email_runs``) and the OS lock its process holds
while it works. A process that dies leaves a ``running`` row and no lock. Every
reader (the Scans tab, the app's status routes, the v1 search status) asks
:func:`derive_run_state` instead of comparing the row's text itself, so two
readers cannot answer differently about the same run. ``docs/run-state.md``
holds the state table and the readers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class RunState(str, Enum):
    """The closed set of run states; a row's ``status`` column stores only ``running``, ``ok``, ``failed`` and ``interrupted``."""

    IDLE = "idle"
    RUNNING = "running"
    OK = "ok"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


MEANING = {
    RunState.IDLE: "nothing has run yet and no run holds the lock",
    RunState.RUNNING: "a run holds its lock and is working",
    RunState.OK: "the newest run finished and its result is in place",
    RunState.FAILED: "the newest run ended with an error, which the reason carries",
    RunState.INTERRUPTED: "the process died before the newest run finished, so its row never settled",
    RunState.UNKNOWN: "the lock could not be read, so a running run cannot be told from a dead one",
}

STOPPED_EARLY = "the run stopped before it finished"
LOCK_UNREADABLE = "the lock could not be read"

_RECORDED = {RunState.RUNNING.value: RunState.RUNNING, RunState.OK.value: RunState.OK,
             RunState.FAILED.value: RunState.FAILED, RunState.INTERRUPTED.value: RunState.INTERRUPTED}


_ATTENTION = frozenset({RunState.FAILED, RunState.INTERRUPTED, RunState.UNKNOWN})


@dataclass(frozen=True)
class Derived:
    """One answer: the state, and why when it is ``failed``, ``interrupted`` or ``unknown``."""

    state: RunState
    reason: Optional[str] = None

    @property
    def running(self) -> bool:
        return self.state is RunState.RUNNING

    @property
    def attention(self) -> bool:
        """Whether the run ended badly or cannot be told: ``failed``, ``interrupted`` or ``unknown``."""
        return self.state in _ATTENTION

    def as_dict(self) -> dict[str, Optional[str]]:
        return {"state": self.state.value, "reason": self.reason}


def derive_run_state(row_status: Optional[str], row_message: Optional[str], lock_held: Optional[bool],
                     *, any_run: bool = False) -> Derived:
    """The state of one run from its newest row and its lock; the only code that maps the two to a mode.

    ``row_status`` is the row's stored text (``None`` before any row); ``lock_held`` is whether a process
    holds the lock now, ``None`` when the lock file could not be read. A ``running`` row with a free lock
    is ``interrupted``; an unreadable lock is ``unknown``, never ``idle`` or ``running``.

    ``any_run`` asks whether any run is active rather than how this row ended: a held lock then means
    ``running`` whatever the newest row says (between two roots, or just after the lock is taken and
    before the run writes its row), and an unreadable lock is ``unknown`` even over a settled row.
    """
    recorded = _RECORDED.get(row_status) if row_status is not None else None
    if row_status is not None and recorded is None:
        return Derived(RunState.UNKNOWN, "the stored status %r is not one this app writes" % row_status)
    if any_run or recorded in (None, RunState.RUNNING):
        if lock_held is None:
            return Derived(RunState.UNKNOWN, LOCK_UNREADABLE)
        if lock_held:
            return Derived(RunState.RUNNING)
        if recorded is None:
            return Derived(RunState.IDLE)
        if recorded is RunState.RUNNING:
            return Derived(RunState.INTERRUPTED, row_message or STOPPED_EARLY)
    if recorded is RunState.OK:
        return Derived(RunState.OK)
    if recorded is RunState.FAILED:
        return Derived(RunState.FAILED, row_message or "the run failed")
    return Derived(RunState.INTERRUPTED, row_message or STOPPED_EARLY)
