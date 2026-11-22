"""Boot a disposable folder-explorer for the browser suite, scanned over an invented tree.

Every run gets its own data folder, config and free port, so the suite never
touches the live tray on 8453 or this PC's real index. Without
``FOLDER_EXPLORER_E2E_AUTOBOOT=1`` or ``--e2e-autoboot`` (the gate sets the first), the vendored live guard
refuses to run while 8453 is occupied unless ``FOLDER_EXPLORER_E2E_LIVE=1``
says to point read-only checks at the live app instead. A boot failure is a
test failure, never a skip. The instance runs with Outlook and every desktop
launch off (``FOLDER_EXPLORER_OUTLOOK`` / ``FOLDER_EXPLORER_DESKTOP``), so no
test can start anything on this PC; tests that open things fake the route.

Two projections run each test: Chromium at desktop size and WebKit on an
iPhone viewport; ``desktop_only`` keeps a test off the phone projection.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Browser, BrowserContext, Page

from scripts.disposable_webapp import WebappBootError, disposable_webapp
from tests.e2e._e2e_live_guard import require_disposable_instance
from tests.e2e._port_pool import pytest_runtest_makereport, pytest_terminal_summary  # noqa: F401 — the hooks pytest looks for here
from tests.e2e._surface import pytest_collection_modifyitems  # noqa: F401 — the hook pytest looks for here: capture mode runs only the tests that call surface()

LIVE_PORT = 8453
#: Rows scrolled out of view inside a pane that scrolls on its own still report page boxes under the footer.
FOOT_UNDER_SCROLLED_ROWS = {"#buildReadout": "rows scrolled out of a pane that scrolls on its own"}
LIVE_ENV = "FOLDER_EXPLORER_E2E_LIVE"
AUTOBOOT_ENV = "FOLDER_EXPLORER_E2E_AUTOBOOT"
_BOOT_TIMEOUT_S = 20.0
# Playwright's own 30 s default turns a stuck locator into a silent wait; 15 s names it sooner.
_DEFAULT_TIMEOUT_MS = 15_000


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--e2e-autoboot", action="store_true", default=False,
                     help="boot the disposable instance even while the live app holds its port (the design review's capture run passes it)")


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


@pytest.fixture(scope="session")
def context(browser: Browser, browser_context_args: dict[str, Any]) -> Iterator[BrowserContext]:
    """One browser context per projection, shared by every case.

    A context of its own per case opens about six fresh sockets to the app, and every closed one
    parks a loopback port pair in TIME_WAIT (``_port_pool.py``); one context keeps those sockets
    and the HTTP cache warm across the run. ``page`` below hands each case a new tab and resets
    what a context keeps between cases.
    """
    shared = browser.new_context(**browser_context_args)
    shared.set_default_timeout(_DEFAULT_TIMEOUT_MS)
    shared.set_default_navigation_timeout(_DEFAULT_TIMEOUT_MS)
    # The server holds the page's hand-off poll open for up to 25 s, and screenshot settling waits for an
    # idle network. A test that hands a mail over routes the poll on its page, which takes precedence.
    shared.route("**/api/archive/pending*", lambda route: route.fulfill(status=200, content_type="application/json",
                                                                        body='{"mail": null}'))
    yield shared
    shared.close()


def _reset_context_state(context: BrowserContext, page: Page, app_url: str) -> None:
    """Drop what a case leaves in the shared context: other tabs, cookies, grants and the app's local storage."""
    for other in context.pages:
        if other is not page:
            other.close()
    page.goto(app_url + "/healthz")  # an app-origin document to clear that origin's storage from
    page.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
    context.clear_cookies()
    context.clear_permissions()


@pytest.fixture
def page(context: BrowserContext, app_url: str) -> Iterator[Page]:
    tab = context.new_page()
    try:
        yield tab
    finally:
        _reset_context_state(context, tab, app_url)
        tab.close()


@pytest.fixture(scope="session")
def roots_count() -> int:
    return 2


@pytest.fixture(scope="session")
def app_url(request: pytest.FixtureRequest, roots_count: int) -> Iterator[str]:
    autoboot = os.environ.get(AUTOBOOT_ENV) == "1" or request.config.getoption("--e2e-autoboot")
    if not autoboot and require_disposable_instance(LIVE_PORT, LIVE_ENV):
        yield "http://127.0.0.1:%d" % LIVE_PORT
        return
    try:
        with disposable_webapp(roots_count=roots_count, startup_timeout=_BOOT_TIMEOUT_S) as url:
            yield url
    except WebappBootError as exc:
        pytest.fail(str(exc))
