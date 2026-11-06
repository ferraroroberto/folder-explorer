"""The Search tab in a browser: email search with facets and the preview, folder search from a link."""

from __future__ import annotations

import json
import re

from playwright.sync_api import Page, Route, expect

from tests.e2e._layout_lint import assert_layout_clean


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


def test_email_search_refines_previews_and_shows_the_folder(page: Page, app_url: str, browser_name: str) -> None:
    errors = _errors(page)
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
    _lint(page)
    preview.locator('[data-act="folder"]').first.click()
    expect(page.locator("#paneExplore")).to_be_visible()
    expect(page.locator("#scopePill")).to_contain_text("Northwind")
    assert errors == []


def test_folder_search_opens_from_a_link_and_matches_every_word(page: Page, app_url: str, browser_name: str,
                                                                roots_count: int) -> None:
    errors = _errors(page)
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
