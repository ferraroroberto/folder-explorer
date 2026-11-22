"""A throwaway folder-explorer over an invented tree, on a free loopback port.

The e2e suite (``tests/e2e/conftest.py``) and the design review's synthetic run
(``scripts/design_review_synthetic.py``) both boot the app here, so there is one copy
of the recipe: a temp data folder and config, the invented trees and mail index, a
fixture scan, a synthetic account and home (so the screenshot privacy check covers the account name), ``FOLDER_EXPLORER_OUTLOOK=off`` and ``FOLDER_EXPLORER_DESKTOP=off``.
Nothing is read from or written to the live app's data, port or config, and the temp
folder is removed on exit.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.e2e._scan_capture import NO_WINDOW, run_utf8  # noqa: E402
from tests.email_fixture import build_email_index  # noqa: E402
from tests.fixture_tree import add_docs, add_hidden, build_tree  # noqa: E402
from tests.host._synthetic_host import synthetic_host_env  # noqa: E402

_SCAN_TIMEOUT_S = 60
_LOG_TAIL_CHARS = 2000


class WebappBootError(RuntimeError):
    """The fixture scan or the webapp failed to come up; the message carries the output."""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/healthz", timeout=1) as r:
            return r.status == 200
    except OSError:
        return False


@contextlib.contextmanager
def disposable_webapp(*, roots_count: int = 2, startup_timeout: float = 20.0) -> Iterator[str]:
    """Yield the base URL of a scanned, running throwaway app; stop it and delete its folder on exit."""
    work = Path(tempfile.mkdtemp(prefix="folder-explorer-e2e-"))
    try:
        roots = [add_hidden(build_tree(work / "Cloud drive")), add_docs(build_tree(work / "Media disk"))][:roots_count]
        mail = roots[0] / "Mail"
        emails = build_email_index(work / "emails.db", mail)
        port = _free_port()
        (work / "config.json").write_text(json.dumps({"remote_access": False, "port": port, "roots": [str(r) for r in roots],
                                                      "email": {"index_path": str(emails), "roots": [str(mail)]}}))
        env = dict(synthetic_host_env(os.environ, work), FOLDER_EXPLORER_DATA_DIR=str(work / "data"), FOLDER_EXPLORER_CONFIG=str(work / "config.json"),
                   FOLDER_EXPLORER_OUTLOOK="off", FOLDER_EXPLORER_DESKTOP="off", PYTHONUTF8="1", PYTHONUNBUFFERED="1")
        scan = run_utf8([sys.executable, "-m", "app.cli", "scan"], cwd=ROOT, env=env, timeout=_SCAN_TIMEOUT_S)
        if scan.returncode != 0:
            raise WebappBootError("fixture scan failed: " + scan.stdout + scan.stderr)
        with open(work / "webapp.log", "wb") as log:
            proc = subprocess.Popen([sys.executable, "-m", "app.webapp", "--port", str(port)], cwd=str(ROOT), env=env,
                                    stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
            url = "http://127.0.0.1:%d" % port
            try:
                deadline = time.monotonic() + startup_timeout
                while not _healthy(url):
                    if proc.poll() is not None or time.monotonic() > deadline:
                        log.flush()
                        raise WebappBootError("disposable app did not start: "
                                              + (work / "webapp.log").read_text(errors="replace")[-_LOG_TAIL_CHARS:])
                    time.sleep(0.2)
                yield url
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
    finally:
        shutil.rmtree(work, ignore_errors=True)
