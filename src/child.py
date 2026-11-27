"""Run one of this app's own modules as a child: windowless, from the repo root, in UTF-8 and unbuffered, with no stdin."""

from __future__ import annotations

import os
import subprocess
from typing import IO, Any, Optional

from src.config import PROJECT_ROOT
from src.no_window import NO_WINDOW


def child_env() -> dict[str, str]:
    """This process's environment, with the child's output as UTF-8 and unbuffered."""
    return dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1")


def run_child(cmd: list[str], timeout: float) -> "subprocess.CompletedProcess[str]":
    """Run ``cmd`` to its end, its stdout and stderr captured as UTF-8."""
    return subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=child_env(), capture_output=True, text=True,
                          encoding="utf-8", timeout=timeout, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)


def start_child(cmd: list[str], log: Optional[IO[bytes]] = None) -> "subprocess.Popen[Any]":
    """Start ``cmd`` and return at once.

    Without ``log`` the caller reads its stdout line by line as UTF-8, and stderr is dropped: the child
    logs to its own file, and an unread pipe would stall it. With ``log`` both streams are appended there.
    """
    if log is None:
        return subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), env=child_env(), stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                creationflags=NO_WINDOW)
    return subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), env=child_env(), stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
