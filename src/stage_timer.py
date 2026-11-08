"""Per-stage timings for one run, logged as one info line so a slow PC can send its numbers.

Only milliseconds and counts go in the line, never a subject, a sender or a path.
``process_age_ms`` reads how long this process has been alive, so the first stage
also shows what the interpreter's start-up and imports cost before any code ran.
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Optional


def process_age_ms() -> Optional[float]:
    """Milliseconds since Windows created this process, or None where that can't be read."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        # A private handle on kernel32, so these prototypes never change another module's calls.
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        filetime_p = ctypes.POINTER(wintypes.FILETIME)
        kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE, filetime_p, filetime_p, filetime_p, filetime_p]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.GetSystemTimePreciseAsFileTime.argtypes = [filetime_p]
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel32.GetProcessTimes(kernel32.GetCurrentProcess(), ctypes.byref(created), ctypes.byref(exited),
                                        ctypes.byref(kernel), ctypes.byref(user)):
            return None
        now = wintypes.FILETIME()
        kernel32.GetSystemTimePreciseAsFileTime(ctypes.byref(now))

        def ticks(ft: wintypes.FILETIME) -> int:
            return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

        # FILETIME counts 100-nanosecond ticks.
        return (ticks(now) - ticks(created)) / 10_000
    except (AttributeError, OSError):
        return None


class StageTimer:
    """Marks the end of each stage; ``log`` writes ``name: stage=ms ...`` in the order they ran."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._last = time.perf_counter()
        self.stages: list[tuple[str, float]] = []
        self.counts: dict[str, int] = {}

    def mark(self, stage: str) -> float:
        """End ``stage`` now; returns its milliseconds."""
        now = time.perf_counter()
        ms = (now - self._last) * 1000
        self._last = now
        self.stages.append((stage, ms))
        return ms

    def add(self, stage: str, ms: float) -> None:
        """Record a stage measured elsewhere (the process's start-up) without moving the clock."""
        self.stages.append((stage, ms))

    def count(self, what: str, n: int) -> None:
        self.counts[what] = n

    def total_ms(self) -> float:
        return sum(ms for _, ms in self.stages)

    def line(self) -> str:
        bits = ["%s=%.0f" % (stage, ms) for stage, ms in self.stages]
        bits.append("total=%.0f" % self.total_ms())
        bits += ["%s=%d" % kv for kv in self.counts.items()]
        return "%s ms: %s" % (self.name, " ".join(bits))

    def log(self, logger: logging.Logger) -> None:
        logger.info("⏱️ %s", self.line())
