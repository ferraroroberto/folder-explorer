"""The Search tab in a browser: email search with facets, the preview and its actions, folder search from a link, Show all."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator

import pytest
from playwright.sync_api import Page, Route, expect

from tests.e2e._layout_lint import assert_layout_clean
from tests.e2e.conftest import LIVE_PORT
from tests.e2e.test_settings_folders import BELOW_FOLD


def _errors(page: Page) -> list[str]:
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    return errors


def _lint(page: Page) -> None:
    # The wide layout scrolls the side column on its own; the lint reads boxes in page
    # coordinates, so a scrolled column would "overlap" the search box above it.
    page.evaluate("document.querySelectorAll('.search-side, .results-card, .preview-card').forEach(e => { e.scrollTop = 0; })")
    assert_layout_clean(page)


def _preview(page: Page, browser_name: str):  # type: ignore[no-untyped-def]
    return page.locator("#previewSheetBody" if browser_name == "webkit" else "#previewDock")


def _open_faked(page: Page, route_glob: str) -> list[dict]:
    """Answer an open route with ``opened`` and record each body, so nothing is launched on this PC."""
    asked: list[dict] = []

    def fake_open(route: Route) -> None:
        asked.append(route.request.post_data_json)
        route.fulfill(status=200, content_type="application/json", body='{"status": "opened"}')

    page.route(route_glob, fake_open)
    return asked


def _typing_or_a_held_modifier_runs_nothing(page: Page, box_keys: tuple[str, ...], restore: str) -> None:
    """Typing in the box, or a held modifier, runs no card key; the box is restored and the first result focused."""
    for key in box_keys:
        page.locator("#q").press(key)
    page.locator("#q").fill(restore)
    page.locator("#results .result-row").first.focus()
    page.keyboard.press("Control+e")
    page.keyboard.press("Control+c")
    page.wait_for_timeout(300)
    expect(page.locator(".toast")).to_have_count(0)


def _copy_key_puts_the_shown_path_on_the_clipboard(page: Page, preview) -> None:  # type: ignore[no-untyped-def]
    page.keyboard.press("c")
    expect(page.locator(".toast", has_text="Path copied")).to_be_visible()
    shown = preview.locator(".pv-path").inner_text().strip()
    assert page.evaluate("() => navigator.clipboard.readText()").replace("/", "\\") == shown


def test_email_search_refines_previews_and_acts_on_the_mail(page: Page, app_url: str, browser_name: str) -> None:
    errors = _errors(page)
    opened_folder = _open_faked(page, "**/api/open")
    opened_mail: list[str] = []

    def fake_open_mail(route: Route) -> None:
        opened_mail.append(route.request.url)
        route.fulfill(status=200, content_type="application/json", body="{}")

    def outlook_ready(route: Route) -> None:
        # The e2e instance has no Outlook; the keys only need the detail to say the mail can be opened.
        fetched = route.fetch()
        route.fulfill(response=fetched, json={**fetched.json(), "can_open": True})

    page.route(re.compile(r"/api/emails/\d+/open$"), fake_open_mail)
    page.route(re.compile(r"/api/emails/\d+\?"), outlook_ready)
    page.goto(app_url + "/")
    expect(page.locator("#paneSearch")).to_be_visible()
    page.locator("#q").fill("budget")
    rows = page.locator("#results .result-row")
    expect(rows).to_have_count(3)
    expect(rows.first.locator("mark").first).to_have_text("budget", ignore_case=True)
    if browser_name == "webkit":
        page.locator("#facetsCard .side-toggle").click()
    page.locator('.facet[data-op="is"]').click()
    expect(page.locator(".qchip")).to_have_count(1)
    expect(page.locator("#q")).to_have_value("budget is:flagged")
    expect(rows).to_have_count(1)
    rows.first.click()
    preview = _preview(page, browser_name)
    expect(preview.locator(".pv-subject")).to_contain_text("Q4 budget draft")
    expect(preview.locator(".pv-snippet mark").first).to_have_text("budget", ignore_case=True)
    expect(preview.locator('[data-act="folder"]').first).to_be_visible()
    expect(preview.locator('[data-act="outlook"]')).to_be_visible()
    open_folder = preview.locator('[data-act="openfolder"]')
    expect(open_folder).to_have_text("Open folder")
    _lint(page)
    if browser_name == "chromium":
        page.context.grant_permissions(["clipboard-read", "clipboard-write"])
        for label in ("open folder", "show folder", "copy path"):
            expect(page.locator("#keysLine")).to_contain_text(label)
        _typing_or_a_held_modifier_runs_nothing(page, ("e", "o"), "budget is:flagged")
        assert opened_folder == [] and opened_mail == []
        page.keyboard.press("o")
        expect(page.locator(".toast", has_text="Opening in Outlook")).to_be_visible()
        assert len(opened_mail) == 1
        page.keyboard.press("e")
        expect(page.locator(".toast", has_text="Opened in File Explorer")).to_be_visible()
        assert len(opened_folder) == 1
        _copy_key_puts_the_shown_path_on_the_clipboard(page, preview)

    open_folder.click()
    expect(page.locator(".toast", has_text="Opened in File Explorer").last).to_be_visible()
    # The server is asked to open by the mail's folder record, never by a path from the page.
    assert len(opened_folder) == (2 if browser_name == "chromium" else 1)
    assert opened_folder[-1]["kind"] == "folder" and opened_folder[-1]["verb"] == "explore"
    assert opened_folder[-1]["id"] > 0 and "path" not in opened_folder[-1]

    if browser_name == "chromium":
        page.keyboard.press("f")
    else:
        preview.locator('[data-act="folder"]').first.click()
    expect(page.locator("#paneExplore")).to_be_visible()
    expect(page.locator("#scopePill")).to_contain_text("Northwind")
    assert errors == []


def test_folder_search_opens_from_a_link_matches_every_word_and_runs_the_card_keys(page: Page, app_url: str, browser_name: str,
                                                                                    roots_count: int) -> None:
    errors = _errors(page)
    asked = _open_faked(page, "**/api/folders/open")
    page.goto(app_url + "/#search?mode=folders&q=harbour%202024")
    expect(page.locator("#q")).to_have_value("harbour 2024")
    expect(page.locator('#modeSeg [data-value="folders"]')).to_have_attribute("aria-pressed", "true")
    rows = page.locator("#results .result-row")
    # Each invented root holds the same tree, so each gives one match.
    expect(rows).to_have_count(roots_count)
    expect(rows.first).to_contain_text("harbour-trip")
    rows.first.click()
    preview = _preview(page, browser_name)
    expect(preview.locator(".pv-path")).to_contain_text("harbour-trip")
    _lint(page)
    if browser_name == "webkit":
        page.locator("#previewDialog [data-close]").click()
    page.locator("#q").fill("harbour nothing-like-this")
    expect(page.locator("#results .empty-state-message")).to_have_text("No folder matches every word.")
    page.locator("#q").fill("harbour 2024")

    if browser_name == "chromium":
        page.context.grant_permissions(["clipboard-read", "clipboard-write"])
        expect(rows).to_have_count(roots_count)
        rows.first.click()
        expect(preview.locator(".pv-path")).to_contain_text("harbour-trip")
        expect(page.locator("#keysLine")).to_contain_text("open in File Explorer")
        expect(page.locator("#keysLine")).to_contain_text("copy path")
        _typing_or_a_held_modifier_runs_nothing(page, ("c", "e"), "harbour 2024")
        assert asked == []
        page.keyboard.press("e")
        expect(page.locator(".toast", has_text="Opened in File Explorer")).to_be_visible()
        assert len(asked) == 1 and asked[0]["id"] > 0 and "path" not in asked[0]
        _copy_key_puts_the_shown_path_on_the_clipboard(page, preview)
        page.keyboard.press("f")
        expect(page.locator("#paneExplore")).to_be_visible()
    assert errors == []


_PIXEL = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000100000000376ef9240000000a49444154789c6360000000020001e221bc330000000049454e44ae426082")
# An invented newsletter with its own colours and a remote logo, then what a sanitizer bypass would leave behind.
_NEWSLETTER = ('<table width="100%" bgcolor="#ffffff"><tr><td bgcolor="#e8f0fe" style="color:#1a3a6b;padding:12px">'
               '<img src="https://images.example/logo.png" alt="Logo" width="40" height="40"> Harbour weekly</td></tr>'
               '<tr><td style="color:#222222">Budget notes for the quarter.</td></tr></table>')
_BYPASS = '<script>window.__ran = 1</script><img src="x" onerror="window.__ran = 1">'


def test_show_all_follows_the_theme_and_loads_remote_images_only_when_asked(page: Page, app_url: str, browser_name: str) -> None:
    from src.email.sanitize import sanitize_html

    errors = _errors(page)
    asked: list[str] = []

    def fake_remote(route: Route) -> None:
        asked.append(route.request.url)
        route.fulfill(status=200, content_type="image/png", body=_PIXEL)

    def fake_body(route: Route) -> None:
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"text": "Budget notes", "html": sanitize_html(_NEWSLETTER) + _BYPASS}))

    page.context.route("https://images.example/**", fake_remote)
    page.route("**/api/emails/*/body", fake_body)
    page.goto(app_url + "/")
    page.locator("#q").fill("budget")
    page.locator("#results .result-row").first.click()
    preview = _preview(page, browser_name)
    preview.locator('[data-act="showall"]').click()
    label = preview.locator(".pv-label", has_text="Full message")
    expect(label).to_have_text("Full message · HTML, remote images blocked")
    frame = preview.frame_locator(".pv-frame")
    expect(frame.locator("body")).to_contain_text("Budget notes for the quarter.")
    # Off: the address is not even in the frame's markup, and nothing left the frame.
    assert frame.locator("img").first.get_attribute("src") is None
    page.wait_for_timeout(300)
    assert asked == []
    # Nothing runs in the frame, whatever the markup holds.
    assert page.evaluate("document.querySelector('.pv-frame') !== null")
    assert frame.locator("html").evaluate("() => window.__ran === undefined") is True

    look = preview.locator('[data-act="look"]')
    pv_frame = preview.locator(".pv-frame")
    follows = page.evaluate("document.documentElement.dataset.theme")
    expect(pv_frame).to_have_class(re.compile(follows))
    look.click()
    other = "dark" if follows == "light" else "light"
    expect(pv_frame).to_have_class(re.compile(other))
    assert ("invert" in frame.locator("html").evaluate("e => getComputedStyle(e).filter")) is (other == "dark")
    look.click()
    expect(pv_frame).to_have_class(re.compile(follows))

    switch = preview.locator('[data-act="images"]')
    expect(switch).to_have_attribute("aria-checked", "false")
    switch.click()
    expect(switch).to_have_attribute("aria-checked", "true")
    expect(label).to_have_text("Full message · HTML, remote images shown")
    for _ in range(30):
        if asked:
            break
        page.wait_for_timeout(100)
    assert asked
    assert all(u.startswith("https://images.example/") for u in asked)
    assert frame.locator("html").evaluate("() => window.__ran === undefined") is True
    # The bypass probe is what the browser reports: its script blocked, its image refused by the policy.
    assert any("Blocked script execution" in e for e in errors)
    assert [e for e in errors if "Blocked script execution" not in e and "Content Security Policy" not in e] == []


def _stored_recent(page: Page, mode: str) -> list[str]:
    return json.loads(page.evaluate("(k) => localStorage.getItem(k) || '[]'", "folder-explorer.search.recent." + mode))


def test_recent_searches_fold_remove_one_and_clear(page: Page, app_url: str) -> None:
    page.goto(app_url + "/#search")
    page.evaluate("localStorage.setItem('folder-explorer.search.recent.emails', JSON.stringify(['harbour 2024', 'alpha', 'beta']))")
    page.reload()
    head = page.locator("#recentList .recent-head")
    items = page.locator("#recentList .recent-item")
    # Folded by default once the list has entries, and the choice survives a reload.
    expect(head).to_have_attribute("aria-expanded", "false")
    expect(page.locator("#recentItems")).to_be_hidden()
    head.click()
    expect(head).to_have_attribute("aria-expanded", "true")
    expect(items).to_have_count(3)
    page.reload()
    errors = _errors(page)  # after the last reload: it cancels the Archive sheet's long poll, which WebKit logs
    expect(head).to_have_attribute("aria-expanded", "true")
    expect(items).to_have_count(3)
    _lint(page)
    # Remove one: only that query goes, and nothing runs.
    page.locator('#recentList .recent-remove[aria-label="Remove alpha"]').click()
    expect(items).to_have_count(2)
    assert _stored_recent(page, "emails") == ["harbour 2024", "beta"]
    expect(page.locator("#q")).to_have_value("")
    # The entry's text still runs it.
    items.first.click()
    expect(page.locator("#q")).to_have_value("harbour 2024")
    page.locator("#q").fill("")
    # Clear empties the list and the block hides.
    page.locator("#recentList .recent-clear").click()
    expect(page.locator("#recentList")).to_be_hidden()
    assert _stored_recent(page, "emails") == []
    assert errors == []


@pytest.fixture()
def markdown_search_restored(page: Page, app_url: str) -> Iterator[None]:
    """Leave the session app's Markdown search switch on however the test ends."""
    if app_url.endswith(":%d" % LIVE_PORT):
        pytest.skip("the switch writes the settings: never on the live app")
    yield
    page.request.post(app_url + "/api/config", data={"search": {"markdown": True}})


@pytest.mark.usefixtures("markdown_search_restored")
def test_the_markdown_search_switch_hides_the_mode_and_falls_back_to_folders(page: Page, app_url: str) -> None:
    errors = _errors(page)
    segments = page.locator("#modeSeg .segmented-item:visible")
    markdown = page.locator('#modeSeg .segmented-item[data-value="docs"]')
    switch = page.locator("#settingsDialog #markdownSearchSlot [role=switch]")

    def settings(want: str) -> None:
        page.locator("#paneSearch .home-settings").click()
        expect(page.locator("#settingsDialog")).to_be_visible()
        expect(switch).to_have_attribute("aria-checked", want)

    page.goto(app_url + "/#search?mode=docs&q=")
    expect(markdown).to_be_visible()
    expect(markdown).to_have_attribute("aria-pressed", "true")
    expect(segments).to_have_text(["Folders", "Documents", "Emails"])

    settings("true")
    for theme in ("light", "dark"):
        page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
        page.wait_for_timeout(100)
        assert_layout_clean(page, allow=BELOW_FOLD)
    switch.click()
    expect(switch).to_have_attribute("aria-checked", "false")
    expect(page.locator("#markdownSearchNote")).to_contain_text("Documents mode is hidden")
    page.locator("#settingsDialog [data-close]").click()

    expect(segments).to_have_text(["Folders", "Emails"])
    expect(page.locator('#modeSeg .segmented-item[aria-pressed=true]')).to_have_text("Folders")
    expect(page.locator("#resultsTitle")).to_contain_text("Folders")
    assert "mode=folders" in page.url
    page.locator("#q").fill("harbour")
    expect(page.locator("#results .result-row").first).to_contain_text("harbour")
    page.locator('#modeSeg .segmented-item[data-value="emails"]').click()
    expect(page.locator("#resultsTitle")).to_contain_text("Emails")

    # It survives a reload, and a link to the Markdown mode lands on Folders.
    page.goto(app_url + "/#search?mode=docs&q=")
    page.reload()
    expect(segments).to_have_text(["Folders", "Emails"])
    expect(page.locator('#modeSeg .segmented-item[aria-pressed=true]')).to_have_text("Folders")

    settings("false")
    switch.click()
    expect(switch).to_have_attribute("aria-checked", "true")
    page.locator("#settingsDialog [data-close]").click()
    expect(segments).to_have_text(["Folders", "Documents", "Emails"])
    page.locator("#modeSeg .segmented-item", has_text="Documents").click()
    expect(page.locator("#resultsTitle")).to_contain_text("Documents")
    assert errors == []
