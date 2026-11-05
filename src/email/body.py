"""Read one mail's full body on demand ("Show all"), in a child process with a timeout.

The web server never opens a ``.msg`` itself: reading an online-only file
downloads it, which can take a while or hang on a stalled sync client, so the
read runs as ``python -m src.email.reader`` in its own windowless process. The
caller hands over a path it took from the index by row id and checked against
the email folders; a path from a client is never read. Bodies are not stored or
cached: each Show all reads the file again.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from src.email.reader import ERR_GONE, ERR_NOT_SET_UP, ERR_PARSE, ERR_READ
from src.email.sanitize import sanitize_html
from src.no_window import NO_WINDOW

logger = logging.getLogger(__name__)

ERR_TIMEOUT = "timeout"
ERR_OUTSIDE = "outside"

# How long one read may take, download included, before the page says it timed out.
READ_TIMEOUT_S = 30.0

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# What the page shows for each failure: a title and one line on what to do.
MESSAGES = {
    ERR_TIMEOUT: "The download timed out. OneDrive didn't deliver the file within %d seconds; try again in a minute." % READ_TIMEOUT_S,
    ERR_GONE: "The file is no longer there. It was moved or deleted after the last scan.",
    ERR_PARSE: "This file couldn't be read as a mail. The indexed preview is all there is.",
    ERR_READ: "The file couldn't be read.",
    ERR_NOT_SET_UP: "Show all isn't set up here: extract-msg isn't installed (requirements-email.txt).",
    ERR_OUTSIDE: "This mail is outside the email folders, so it isn't read.",
}


@dataclass(frozen=True)
class Body:
    """A read's outcome: the text (and safe HTML) or an error code with its message."""

    ok: bool
    text: str = ""
    html: str = ""
    error: Optional[str] = None
    message: str = ""


Runner = Callable[[list[str], float], "subprocess.CompletedProcess[str]"]


def run_child(cmd: list[str], timeout: float) -> "subprocess.CompletedProcess[str]":
    """Run one of this app's own modules as a windowless child from the repo root, its stdout captured as UTF-8."""
    env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    return subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=env, capture_output=True, text=True, encoding="utf-8",
                          timeout=timeout, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)


def failed(code: str, detail: str = "") -> Body:
    return Body(False, error=code, message=MESSAGES.get(code, detail or "The mail couldn't be read."))


def fetch_body(path: str, timeout: float = READ_TIMEOUT_S, run: Runner = run_child) -> Body:
    """Read ``path`` through the reader child; every failure comes back as its own code."""
    name = os.path.basename(path)
    try:
        proc = run([sys.executable, "-m", "src.email.reader", path], timeout)
    except subprocess.TimeoutExpired:
        logger.warning("⚠️ show all: reading %s timed out after %.0f s", name, timeout)
        return failed(ERR_TIMEOUT)
    try:
        result = json.loads(proc.stdout or "")
    except ValueError:
        logger.warning("⚠️ show all: the reader for %s exited %s with no answer: %s", name, proc.returncode,
                       (proc.stderr or "")[-300:])
        return failed(ERR_READ)
    if not result.get("ok"):
        code = str(result.get("error") or ERR_READ)
        logger.warning("⚠️ show all: %s not read (%s): %s", name, code, result.get("message", ""))
        return failed(code, str(result.get("message", "")))
    text, html = str(result.get("text") or ""), str(result.get("html") or "")
    logger.info("ℹ️ show all: read %s (%d chars of text, %s)", name, len(text), "HTML" if html else "no HTML")
    return Body(True, text=text, html=sanitize_html(html) if html.strip() else "")
