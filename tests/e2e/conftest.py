"""Boot a disposable folder-explorer for the browser suite, scanned over an invented tree.

Every run gets its own data folder, config and free port, so the suite never
touches the live tray on 8453 or this PC's real index. Without
``FOLDER_EXPLORER_E2E_AUTOBOOT=1`` (the gate sets it), the vendored live guard
refuses to run while 8453 is occupied unless ``FOLDER_EXPLORER_E2E_LIVE=1``
says to point read-only checks at the live app instead. A boot failure is a
test failure, never a skip. The instance runs with Outlook and every desktop
launch off (``FOLDER_EXPLORER_OUTLOOK`` / ``FOLDER_EXPLORER_DESKTOP``), so no
test can start anything on this PC; tests that open things fake the route.

Two projections run each test: Chromium at desktop size and WebKit on an
iPhone viewport; ``desktop_only`` keeps a test off the phone projection.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import BrowserContext

from tests.e2e._e2e_live_guard import require_disposable_instance
from tests.email_fixture import build_email_index
from tests.fixture_tree import add_hidden, build_tree

ROOT = Path(__file__).resolve().parents[2]
LIVE_PORT = 8453
LIVE_ENV = "FOLDER_EXPLORER_E2E_LIVE"
AUTOBOOT_ENV = "FOLDER_EXPLORER_E2E_AUTOBOOT"
_BOOT_TIMEOUT_S = 20.0
# Playwright's own 30 s default turns a stuck locator into a silent wait; 15 s names it sooner.
_DEFAULT_TIMEOUT_MS = 15_000
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "desktop_only: not run on the WebKit iPhone projection")
    if not config.option.browser:
        config.option.browser.extend(["chromium", "webkit"])


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict[str, Any], browser_name: str, playwright: Any) -> dict[str, Any]:
    if browser_name == "webkit":
        return {**browser_context_args, **playwright.devices["iPhone 14"]}
    return {**browser_context_args, "viewport": {"width": 1440, "height": 900}}


@pytest.fixture(autouse=True)
def _desktop_only(request: pytest.FixtureRequest, browser_name: str) -> None:
    if browser_name == "webkit" and request.node.get_closest_marker("desktop_only"):
        pytest.skip("desktop_only")


@pytest.fixture(autouse=True)
def _bounded_timeouts(context: BrowserContext) -> None:
    context.set_default_timeout(_DEFAULT_TIMEOUT_MS)
    context.set_default_navigation_timeout(_DEFAULT_TIMEOUT_MS)


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


@pytest.fixture(scope="session")
def roots_count() -> int:
    return 2


@pytest.fixture(scope="session")
def app_url(roots_count: int) -> Iterator[str]:
    if os.environ.get(AUTOBOOT_ENV) != "1" and require_disposable_instance(LIVE_PORT, LIVE_ENV):
        yield "http://127.0.0.1:%d" % LIVE_PORT
        return
    work = Path(tempfile.mkdtemp(prefix="folder-explorer-e2e-"))
    roots = [add_hidden(build_tree(work / "Cloud drive")), build_tree(work / "Media disk")][:roots_count]
    mail = roots[0] / "Mail"
    emails = build_email_index(work / "emails.db", mail)
    port = _free_port()
    (work / "config.json").write_text(json.dumps({"remote_access": False, "port": port, "roots": [str(r) for r in roots],
                                                  "email": {"index_path": str(emails), "roots": [str(mail)]}}))
    env = dict(os.environ, FOLDER_EXPLORER_DATA_DIR=str(work / "data"), FOLDER_EXPLORER_CONFIG=str(work / "config.json"),
               FOLDER_EXPLORER_OUTLOOK="off", FOLDER_EXPLORER_DESKTOP="off", PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    scan = subprocess.run([sys.executable, "-m", "app.cli", "scan"], cwd=str(ROOT), env=env, capture_output=True, text=True,
                          timeout=60, creationflags=NO_WINDOW)
    if scan.returncode != 0:
        pytest.fail("fixture scan failed: " + scan.stdout + scan.stderr)
    log = open(work / "webapp.log", "wb")
    proc = subprocess.Popen([sys.executable, "-m", "app.webapp", "--port", str(port)], cwd=str(ROOT), env=env,
                            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    url = "http://127.0.0.1:%d" % port
    try:
        deadline = time.monotonic() + _BOOT_TIMEOUT_S
        while not _healthy(url):
            if proc.poll() is not None or time.monotonic() > deadline:
                log.flush()
                pytest.fail("disposable app did not start: " + (work / "webapp.log").read_text(errors="replace")[-2000:])
            time.sleep(0.2)
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
