"""The Markdown viewer: Explore's document sheet and the /doc window, in both themes, on the phone and the desktop.

The documents are invented fixture files under a temp root ("Media disk" in
conftest); the cloud-only answer is faked at the route, so nothing here
touches a real OneDrive placeholder.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

from playwright.sync_api import Page, Route, expect

from tests.e2e._layout_lint import assert_layout_clean
from tests.fixture_tree import DOCS_FOLDER

HEADINGS = ["Garden guide", "Beds", "Out of reach"]
IMAGE_LOADED = "sel => { const i = document.querySelector(sel); return !!i && i.complete && i.naturalWidth > 0; }"


def _errors(page: Page) -> list[str]:
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return errors


def _guide(page: Page, app_url: str) -> dict[str, Any]:
    """The invented guide's place: ``{root, scan, id}``, read from the app's own listing."""
    root = next(r for r in page.request.get(app_url + "/api/roots").json()["roots"] if r["name"].startswith("Media disk"))
    top = page.request.get(app_url + "/api/folder?" + urlencode({"root": root["key"]})).json()
    notes = next(it["id"] for it in top["items"] if it["name"] == DOCS_FOLDER)
    body = page.request.get(app_url + "/api/folder?" + urlencode({"root": root["key"], "folder": notes})).json()
    return {"root": root["key"], "scan": body["scan_id"], "id": next(it["id"] for it in body["items"] if it["name"] == "guide.md")}


# The sheet's text scrolls in its own box inside the dialog's fixed layer; a link below that box's fold is not off the screen.
SHEET_SCROLLER = {"#docSheetBody .doc-prose a": "below the fold of the sheet's own scroller"}


def _themes(page: Page, check: Any, allow: Any = None) -> None:
    for theme in ("light", "dark"):
        page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
        page.wait_for_timeout(100)
        check()
        assert_layout_clean(page, allow=allow or {})


def test_a_markdown_row_opens_in_the_document_sheet(page: Page, app_url: str, browser_name: str) -> None:
    errors = _errors(page)
    page.goto(app_url + "/")
    page.locator('.tab[data-tab="explore"]').click()
    media = page.eval_on_selector("#rootSelect", "s => [...s.options].find(o => o.text.startsWith('Media disk')).value")
    page.locator("#rootSelect").select_option(media)
    page.locator(".tree-main", has_text=DOCS_FOLDER).click()
    rows = page.locator("#contentsDialog" if browser_name == "webkit" else "#contentsDock")
    guide = rows.locator(".list-row", has_text="guide.md")
    expect(guide.locator(".doc-badge")).to_have_text("MD")
    expect(rows.locator(".list-row", has_text="diagram.png").locator(".doc-badge")).to_have_count(0)
    if browser_name == "webkit":
        guide.click()  # one tap on a touch screen
    else:
        guide.dblclick()

    sheet = page.locator("#docDialog")
    body = sheet.locator("#docSheetBody")
    expect(sheet.locator("#docDialogTitle")).to_have_text("guide.md")
    expect(sheet.locator("#docWhere")).to_contain_text(DOCS_FOLDER)
    expect(body.locator(".doc-prose :is(h1, h2)")).to_have_text(HEADINGS)
    expect(body.locator("table td").first).to_have_text("A1")
    expect(body.locator(".code-listing .k").first).to_have_text("def")
    expect(body.locator(".doc-link-off")).to_have_count(2)
    page.wait_for_function(IMAGE_LOADED, arg="#docSheetBody img[data-doc-img]")
    _themes(page, lambda: expect(body.locator("h1")).to_be_visible(), SHEET_SCROLLER)

    sheet.locator("#docOutlineBtn").click()
    expect(sheet.locator("#docOutline a")).to_have_text(HEADINGS)
    sheet.locator("#docOutline a", has_text="Out of reach").click()
    expect(sheet.locator("#docOutline")).to_be_hidden()

    body.locator("a", has_text="second page").click()
    expect(sheet.locator("#docDialogTitle")).to_have_text("watering.md")
    expect(body.locator("h1")).to_have_text("Watering")
    sheet.locator("#docBack").click()
    expect(sheet.locator("#docDialogTitle")).to_have_text("guide.md")
    expect(sheet.locator("#docBack")).to_be_hidden()
    assert (sheet.locator("#docWindow").get_attribute("href") or "").startswith("/doc?root=")
    sheet.locator("[data-close]").click()
    expect(sheet).to_be_hidden()
    assert errors == []


def test_the_doc_window_renders_the_same_document_with_the_fleet_nav(page: Page, app_url: str, browser_name: str) -> None:
    errors = _errors(page)
    loc = _guide(page, app_url)
    page.goto(app_url + "/doc?" + urlencode(loc))
    body = page.locator("#docBody")
    expect(page.locator("#docTitle")).to_have_text("guide.md")
    expect(page.locator("#docStatus")).to_contain_text(DOCS_FOLDER)
    expect(body.locator(".doc-prose :is(h1, h2)")).to_have_text(HEADINGS)
    expect(page.locator("nav.tabs .tab")).to_have_count(5)
    expect(page.locator("nav.tabs .tab[aria-selected=true]")).to_have_count(0)
    page.wait_for_function(IMAGE_LOADED, arg="#docBody img[data-doc-img]")
    outline = page.locator("#docOutline")
    if browser_name == "webkit":
        expect(outline).to_be_hidden()
        page.locator("#docOutlineBtn").click()
    else:
        expect(page.locator("#docOutlineBtn")).to_be_hidden()
    expect(outline.locator(".doc-outline-list:not(.doc-recent-list) a")).to_have_text(HEADINGS)
    _themes(page, lambda: expect(body.locator("h1")).to_be_visible())

    body.locator("a", has_text="second page").click()
    expect(page.locator("#docTitle")).to_have_text("watering.md")
    assert "#schedule" in page.url
    page.locator('.tab[data-tab="explore"]').click()
    expect(page.locator("#paneExplore")).to_be_visible()
    assert errors == []


def test_the_doc_window_explains_a_renumbered_file_and_downloads_a_cloud_only_one(page: Page, app_url: str) -> None:
    loc = _guide(page, app_url)
    page.goto(app_url + "/doc?" + urlencode({**loc, "scan": loc["scan"] + 1}))
    expect(page.locator(".doc-state")).to_contain_text("renumbered")
    page.locator('[data-doc-act="explore"]').click()
    expect(page.locator("#paneExplore")).to_be_visible()

    real = page.request.get(app_url + "/api/doc?" + urlencode(loc)).json()
    asked: list[Any] = []
    page.route("**/api/doc?*", lambda route: route.fulfill(
        status=423, content_type="application/json", body=json.dumps({"detail": "Only in OneDrive", "reason": "cloud_only"})))

    def download(route: Route) -> None:
        asked.append(route.request.post_data_json)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(real))

    page.route("**/api/doc/download", download)
    page.goto(app_url + "/doc?" + urlencode(loc))
    expect(page.locator(".doc-state")).to_contain_text("Only in OneDrive")
    assert_layout_clean(page)
    page.locator('[data-doc-act="download"]').click()
    expect(page.locator("#docBody h1")).to_have_text("Garden guide")
    assert asked == [{"root": loc["root"], "scan_id": loc["scan"], "id": loc["id"]}]


def test_the_doc_window_lists_recent_documents_and_opens_one(page: Page, app_url: str, browser_name: str) -> None:
    errors = _errors(page)
    page.goto(app_url + "/doc?" + urlencode(_guide(page, app_url)))
    expect(page.locator("#docTitle")).to_have_text("guide.md")
    page.locator("#docBody a", has_text="second page").click()
    expect(page.locator("#docTitle")).to_have_text("watering.md")
    if browser_name == "webkit":
        page.locator("#docOutlineBtn").click()
    recent = page.locator("#docOutline .doc-recent-list a")
    expect(recent.first).to_have_text("guide.md")
    expect(recent.filter(has_text="watering.md")).to_have_count(0)
    _themes(page, lambda: expect(recent.first).to_be_visible())
    recent.first.click()
    expect(page.locator("#docTitle")).to_have_text("guide.md")
    assert "/doc?" in page.url
    assert errors == []


def test_search_lists_every_markdown_file_and_a_row_opens_the_viewer(page: Page, app_url: str, browser_name: str) -> None:
    errors = _errors(page)
    flagged: list[str] = []

    def mark_cloud_only(route: Route) -> None:
        body = route.fetch().json()
        for hit in body["hits"]:
            if hit["name"] == "journal.md":
                hit["online"] = True
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
        flagged.append(route.request.url)

    page.route("**/api/search/docs?*", mark_cloud_only)
    page.goto(app_url + "/#search?mode=docs&q=")
    rows = page.locator("#results .result-row")
    expect(page.locator("#modeSeg .segmented-item[aria-pressed=true]")).to_have_text("Markdown")
    expect(page.locator("#resultsTitle")).to_contain_text("Markdown files")
    expect(page.locator("#resultsMeta")).to_have_text("4 files")
    names = [r.locator(".rr-who").inner_text().strip() for r in rows.all()]
    assert sorted(names) == ["big.md", "guide.md", "journal.md", "watering.md"]
    expect(rows.filter(has_text="watering.md").locator(".rr-snip")).to_contain_text(DOCS_FOLDER + " › sub")
    expect(rows.filter(has_text="journal.md").locator(".chip")).to_have_text("Online-only")
    expect(rows.filter(has_text="guide.md").locator(".chip")).to_have_count(0)
    expect(page.locator("#facetsCard")).to_be_hidden()
    expect(page.locator("#filtersCard")).to_be_hidden()
    assert page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth")

    def clean() -> None:
        page.evaluate("document.querySelectorAll('.search-side, .results-card, .preview-card').forEach(e => { e.scrollTop = 0; })")

    _themes(page, clean)

    page.locator("#q").fill("gui")
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text("guide.md")
    expect(page.locator("#resultsMeta")).to_have_text("1 file")
    assert "q=gui" in page.url
    page.locator("#q").fill("gui jour")
    expect(page.locator("#results .empty-state-message")).to_contain_text("No Markdown file name matches")
    page.locator("#clearQuery").click()
    expect(rows).to_have_count(4)

    rows.filter(has_text="guide.md").click()
    sheet = page.locator("#docDialog")
    expect(sheet.locator("#docDialogTitle")).to_have_text("guide.md")
    expect(sheet.locator("#docSheetBody .doc-prose h1")).to_have_text("Garden guide")
    sheet.locator("[data-close]").click()
    expect(sheet).to_be_hidden()
    if browser_name != "webkit":
        dock = page.locator("#previewDock")
        expect(dock.locator(".pv-subject")).to_have_text("guide.md")
        expect(dock.locator("[data-act=view]")).to_be_visible()
        expect(dock.locator("[data-act=default]")).to_have_count(1)
        dock.locator("[data-act=edit]").click()
        expect(sheet.locator("#docSave")).to_be_visible()
        sheet.locator("[data-close]").click()

    # A cloud-only file opens to the viewer's own refusal, and the list never read it.
    page.route("**/api/doc?*", lambda route: route.fulfill(
        status=423, content_type="application/json", body=json.dumps({"detail": "Only in OneDrive", "reason": "cloud_only"})))
    rows.filter(has_text="journal.md").click()
    expect(sheet.locator(".doc-state")).to_contain_text("Only in OneDrive")
    expect(sheet.locator('[data-doc-act="download"]')).to_be_visible()
    assert_layout_clean(page, allow=SHEET_SCROLLER)
    assert flagged and errors == []
