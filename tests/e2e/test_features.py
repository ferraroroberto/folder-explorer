"""Settings, Available features: each block's switch hides its parts and stops its calls, and turning it back on restores them.

The instance runs with Outlook and the desktop off, so a block's parts are checked by what the page does (rows, modes, cards,
requests), never by a real mailbox or window.
"""

from __future__ import annotations

from typing import Callable, Iterator

import pytest
from playwright.sync_api import Locator, Page, expect

from tests.e2e._filters import choose_root
from tests.e2e._layout_lint import assert_layout_clean
from tests.e2e.conftest import LIVE_PORT
from tests.fixture_tree import DOCS_FOLDER

BLOCKS = {"email": "Email", "archive": "Archive", "documents": "Documents", "screenshot": "Screenshot"}
# The sheet scrolls, so its controls above or below the visible part sit outside the viewport; their own overflow is still linted.
BELOW_FOLD = {dialog + tag: "outside the visible part of the scrolling sheet"
              for dialog in ("#settingsDialog ",) for tag in ("button", "input", "select", "[role=switch]", "label")}


@pytest.fixture(autouse=True)
def all_on(page: Page, app_url: str) -> Iterator[None]:
    """Every block on before and after each test, so no test leaves a block off for the next."""
    if app_url.endswith(":%d" % LIVE_PORT):
        pytest.skip("the switches write the settings: never on the live app")
    everything = {"features": {key: True for key in BLOCKS}}
    page.request.post(app_url + "/api/config", data=everything)
    yield
    page.request.post(app_url + "/api/config", data=everything)


def _off(page: Page) -> list[str]:
    return (page.evaluate("document.documentElement.dataset.featuresOff") or "").split()


def _wait_for(page: Page, enough: Callable[[], bool], what: str) -> None:
    """Wait up to 8 s for ``enough()`` to hold; fail naming ``what``."""
    for _ in range(80):
        if enough():
            return
        page.wait_for_timeout(100)
    pytest.fail("never happened: " + what)


def _settings(page: Page, app_url: str) -> None:
    page.goto(app_url + "/")
    page.locator(".open-settings").first.click()
    expect(page.locator("#settingsDialog")).to_be_visible()


def _flip(page: Page, key: str, now: bool) -> None:
    """Tap a block's switch and wait for the page to take the saved state."""
    switch = page.locator("#featSlot%s [role=switch]" % BLOCKS[key])
    switch.click()
    expect(switch).to_have_attribute("aria-checked", "true" if now else "false")
    page.wait_for_function("([k, off]) => (document.documentElement.dataset.featuresOff || '').split(' ').includes(k) === off", arg=[key, not now])


def test_the_group_lists_every_block_on_by_default_and_lays_out_clean(page: Page, app_url: str) -> None:
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    _settings(page, app_url)
    expect(page.locator("#groupFeatures")).to_have_text("Available features")
    for key, name in BLOCKS.items():
        expect(page.locator("#featSlot%s [role=switch]" % name)).to_have_attribute("aria-checked", "true")
        expect(page.locator("#featHint%s" % name)).not_to_be_empty()
    # The instance has no Outlook: that block says why, rather than failing silently.
    expect(page.locator("#featWhyArchive")).to_contain_text("Not available here")
    assert _off(page) == []
    for theme in ("light", "dark"):
        page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
        page.wait_for_timeout(100)
        assert_layout_clean(page, allow=BELOW_FOLD)
    assert errors == []


def test_a_block_that_is_off_stays_off_after_a_reload(page: Page, app_url: str) -> None:
    _settings(page, app_url)
    _flip(page, "screenshot", False)
    expect(page.locator('#settingsDialog [data-open="screenshot"]')).to_be_hidden()
    expect(page.locator("#featHintScreenshot")).to_contain_text("applies when the tray next starts")
    assert page.request.post(app_url + "/api/snap/open-folder").status == 404
    page.reload()
    page.locator(".open-settings").first.click()
    expect(page.locator("#featSlotScreenshot [role=switch]")).to_have_attribute("aria-checked", "false")
    assert _off(page) == ["screenshot"]
    _flip(page, "screenshot", True)
    expect(page.locator('#settingsDialog [data-open="screenshot"]')).to_be_visible()


def _docs_rows(page: Page, browser_name: str) -> Locator:
    """Explore's contents of the invented notes folder: a dock on the desktop, a sheet on the phone."""
    page.locator('.tab[data-tab="explore"]').click()
    # The first root's tree finishes drawing before the root changes, so its answer can never land over the second root's.
    expect(page.locator("#tree .tree-row").first).to_be_visible()
    choose_root(page, "Media disk")
    page.locator(".tree-main", has_text=DOCS_FOLDER).click()
    rows = page.locator("#contentsDialog" if browser_name == "webkit" else "#contentsDock")
    expect(rows.locator(".list-row", has_text="guide.md")).to_be_visible()
    return rows


def test_documents_off_hides_the_badges_the_search_mode_and_the_viewer(page: Page, app_url: str, browser_name: str) -> None:
    _settings(page, app_url)
    expect(page.locator('#settingsDialog [data-open="markdown-search"]')).to_be_visible()
    _flip(page, "documents", False)
    for row in ("markdown-search", "remote-html"):
        expect(page.locator('#settingsDialog [data-open="%s"]' % row)).to_be_hidden()
    assert page.request.get(app_url + "/api/doc/recent").status == 404
    page.locator("#settingsDialog [data-close]").click()
    page.locator('.tab[data-tab="search"]').click()
    expect(page.locator('#modeSeg [data-value="docs"]')).to_be_hidden()
    rows = _docs_rows(page, browser_name)
    expect(rows.locator(".doc-badge")).to_have_count(0)
    if browser_name == "webkit":
        rows.locator("[data-close]").click()
    page.locator("#paneExplore .open-settings").click()
    _flip(page, "documents", True)
    page.locator("#settingsDialog [data-close]").click()
    rows = _docs_rows(page, browser_name)
    expect(rows.locator(".list-row", has_text="guide.md").locator(".doc-badge")).to_have_text("MD")
    assert page.request.get(app_url + "/api/doc/recent").status == 200


def test_email_off_hides_its_card_and_mode_and_stops_asking(page: Page, app_url: str) -> None:
    asked: list[str] = []
    page.on("request", lambda r: asked.append(r.url) if "/api/email/status" in r.url else None)
    page.goto(app_url + "/")
    page.locator('.tab[data-tab="scans"]').click()
    expect(page.locator("#emailCard")).to_be_visible()
    page.locator("#paneScans .open-settings").click()
    expect(page.locator("#settingsDialog")).to_be_visible()
    _flip(page, "email", False)
    expect(page.locator("#groupEmail")).to_be_hidden()
    expect(page.locator("#emailCard")).to_be_hidden()
    asked.clear()
    page.locator("#settingsDialog [data-close]").click()
    page.locator('.tab[data-tab="search"]').click()
    page.locator('.tab[data-tab="scans"]').click()
    page.wait_for_timeout(500)
    assert asked == [], "no email status call while the block is off"
    expect(page.locator("#emailCard")).to_be_hidden()
    assert page.request.get(app_url + "/api/search/status").json()["emails"]["ready"] is False
    page.locator("#paneScans .open-settings").click()
    _flip(page, "email", True)
    page.locator("#settingsDialog [data-close]").click()
    expect(page.locator("#emailCard")).to_be_visible()


def test_archive_off_stops_the_hand_off_poll_and_hides_its_rows(page: Page, app_url: str) -> None:
    polls: list[str] = []
    page.on("request", lambda r: polls.append(r.url) if "/api/archive/pending" in r.url else None)
    _settings(page, app_url)
    expect(page.locator("#groupOutlook")).to_be_visible()
    _wait_for(page, lambda: len(polls) > 0, 'the hand-off poll while the block is on')
    _flip(page, "archive", False)
    expect(page.locator("#groupOutlook")).to_be_hidden()
    expect(page.locator("#showMacro")).to_be_hidden()
    # The poll that was already in flight ends; the next one is never made.
    page.wait_for_timeout(300)
    before = len(polls)
    page.wait_for_timeout(3600)
    assert len(polls) == before, "no hand-off poll while the block is off"
    assert page.request.get(app_url + "/api/archive/macro").status == 404
    _flip(page, "archive", True)
    expect(page.locator("#groupOutlook")).to_be_visible()
    _wait_for(page, lambda: len(polls) > before, 'the hand-off poll again once the block is back on')
