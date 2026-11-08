"""One scan at a time across every process: the app, its schedule and the CLI.

The lock is an OS byte-range lock on ``scan.lock`` in the runtime-data folder,
taken without waiting. The operating system drops it when the holding process
exits, crashed or not, so a dead scan can never leave the lock stuck.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import TracebackType
from typing import IO, Optional

from src import runtime_files


class ScanBusy(RuntimeError):
    """Another scan holds the lock."""


class ScanLock:
    """Non-blocking exclusive lock; ``with ScanLock(path):`` raises ``ScanBusy`` when held."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: Optional[IO[bytes]] = None

    def acquire(self) -> None:
        runtime_files.make_dirs(self.path.parent)
        fh = runtime_files.open_file(self.path, "a+b")
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            fh.close()
            raise ScanBusy("another scan is running") from exc
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()).encode("ascii"))
        fh.flush()
        self._fh = fh

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            fh.close()

    @property
    def held(self) -> bool:
        return self._fh is not None

    def __enter__(self) -> "ScanLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: Optional[type[BaseException]], exc: Optional[BaseException], tb: Optional[TracebackType]) -> None:
        self.release()


def is_locked(path: Path) -> bool:
    """True when some process holds the scan lock right now."""
    probe = ScanLock(path)
    try:
        probe.acquire()
    except ScanBusy:
        return True
    probe.release()
    return False
