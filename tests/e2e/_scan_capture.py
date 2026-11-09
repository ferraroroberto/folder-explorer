"""Run a child to completion and capture its output as text, whatever code page the parent has."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_utf8(args: Sequence[str], *, cwd: Path, env: Mapping[str, str], timeout: float) -> subprocess.CompletedProcess[str]:
    """Capture stdout and stderr as UTF-8, which the child writes under ``PYTHONUTF8=1``.

    Without an explicit encoding the parent decodes with the Windows locale code page, so a
    byte cp1252 leaves undefined raises in the reader thread and the output is lost.
    """
    return subprocess.run(list(args), cwd=str(cwd), env=dict(env), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, creationflags=NO_WINDOW)
