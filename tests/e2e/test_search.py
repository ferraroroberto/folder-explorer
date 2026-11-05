"""The Search tab in a browser: email search with facets and the preview, folder search from a link."""

from __future__ import annotations

from playwright.sync_api import Page, expect

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
