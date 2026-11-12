"""The Markdown editor: edit, save and reopen in Explore's sheet, and the conflict screen in the /doc window.

Every edit goes to the invented ``journal.md`` under the temp root ("Media
disk" in conftest), which no other test reads. "Changed on disk" is another
client's save through the same API, so nothing here writes a file directly.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

from playwright.sync_api import Locator, Page, expect

from tests.e2e._layout_lint import assert_layout_clean
from tests.fixture_tree import DOCS_FOLDER

# The sheet's and the editor's panes scroll in their own boxes inside a fixed layer; text below their fold is not off the screen.
SCROLLERS = {"#docSheetBody .doc-prose a": "below the fold of the sheet's own scroller",
             ".doc-edit-preview .doc-prose a": "below the fold of the preview's own scroller"}


def _errors(page: Page) -> list[str]:
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return errors


def _journal(page: Page, app_url: str) -> dict[str, Any]:
    """The journal's place, ``{root, scan, id}``, read from the app's own listing."""
    root = next(r for r in page.request.get(app_url + "/api/roots").json()["roots"] if r["name"].startswith("Media disk"))
    top = page.request.get(app_url + "/api/folder?" + urlencode({"root": root["key"]})).json()
    notes = next(it["id"] for it in top["items"] if it["name"] == DOCS_FOLDER)
    body = page.request.get(app_url + "/api/folder?" + urlencode({"root": root["key"], "folder": notes})).json()
    return {"root": root["key"], "scan": body["scan_id"], "id": next(it["id"] for it in body["items"] if it["name"] == "journal.md")}


def _source(page: Page, app_url: str, loc: dict[str, Any]) -> dict[str, Any]:
    return page.request.get(app_url + "/api/doc/source?" + urlencode(loc)).json()


def _open(row: Locator, browser_name: str) -> None:
    """Open a Markdown row as the reader would: one tap on a touch screen, a double-click with a mouse."""
    if browser_name == "webkit":
        row.click()
    else:
        row.dblclick()


def _themes(page: Page) -> None:
    for theme in ("light", "dark"):
        page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
        page.wait_for_timeout(100)
        assert_layout_clean(page, allow=SCROLLERS)


def test_edit_save_and_reopen_in_the_sheet(page: Page, app_url: str, browser_name: str) -> None:
    errors = _errors(page)
    loc = _journal(page, app_url)
    page.goto(app_url + "/")
    page.locator('.tab[data-tab="explore"]').click()
    media = page.eval_on_selector("#rootSelect", "s => [...s.options].find(o => o.text.startsWith('Media disk')).value")
    page.locator("#rootSelect").select_option(media)
    page.locator(".tree-main", has_text=DOCS_FOLDER).click()
    rows = page.locator("#contentsDialog" if browser_name == "webkit" else "#contentsDock")
    journal = rows.locator(".list-row", has_text="journal.md")
    _open(journal, browser_name)

    sheet = page.locator("#docDialog")
    expect(sheet.locator("#docSheetBody h1")).to_have_text("Journal")
    sheet.locator('.doc-mode [data-mode="edit"]').click()
    source = sheet.locator(".doc-src-input")
    expect(source).to_have_value(_source(page, app_url, loc)["text"])
    expect(sheet.locator("#docSave")).to_be_disabled()
    if browser_name == "webkit":
        expect(sheet.locator(".doc-format")).to_be_hidden()
        sheet.locator('.doc-edit-panes [data-pane="preview"]').click()
        expect(sheet.locator(".doc-edit-preview h1")).to_have_text("Journal")
        sheet.locator('.doc-edit-panes [data-pane="write"]').click()
    else:
        expect(sheet.locator(".doc-edit-preview h1")).to_have_text("Journal")
        expect(sheet.locator(".doc-format button")).to_have_count(8)
    _themes(page)

    # Unsaved changes: closing asks, and a kept draft comes back on the next Edit.
    source.fill("# Journal\n\nA kept draft.\n")
    expect(sheet.locator("#docSave")).to_be_enabled()
    sheet.locator("[data-close]").click()
    leave = page.locator('dialog[data-track-view="doc-leave"]')
    expect(leave).to_be_visible()
    leave.locator('[data-confirm="keep"]').click()
    expect(sheet).to_be_hidden()
    _open(journal, browser_name)
    sheet.locator('.doc-mode [data-mode="edit"]').click()
    expect(source).to_have_value("# Journal\n\nA kept draft.\n")
    expect(sheet.locator(".doc-edit-status")).to_have_text("Restored your unsaved draft")

    source.fill("# Journal\n\nSaved from the sheet.\n")
    sheet.locator("#docSave").click()
    expect(sheet.locator(".doc-edit-status")).to_have_text("Saved")
    expect(sheet.locator("#docSave")).to_be_disabled()
    assert _source(page, app_url, loc)["text"] == "# Journal\n\nSaved from the sheet.\n"

    page.keyboard.press("Escape")  # back to View, which reads the copy on disk
    expect(sheet.locator("#docSheetBody p")).to_have_text("Saved from the sheet.")
    sheet.locator("[data-close]").click()
    expect(sheet).to_be_hidden()
    _open(journal, browser_name)
    expect(sheet.locator("#docSheetBody p")).to_have_text("Saved from the sheet.")
    assert errors == []


def test_a_file_changed_underneath_shows_the_conflict_screen(page: Page, app_url: str, browser_name: str) -> None:
    errors = _errors(page)
    loc = _journal(page, app_url)
    page.goto(app_url + "/doc?" + urlencode(loc))
    expect(page.locator("#docBody h1")).to_have_text("Journal")
    page.locator('.doc-mode [data-mode="edit"]').click()
    source = page.locator(".doc-src-input")
    opened = _source(page, app_url, loc)
    expect(source).to_have_value(opened["text"])

    # Another device saves first.
    theirs = "# Journal\n\nTheir line.\n"
    other = page.request.post(app_url + "/api/doc/save", data={"root": loc["root"], "scan_id": loc["scan"], "id": loc["id"],
                                                               "text": theirs, "version": opened["version"]})
    assert other.ok, other.text()

    source.fill("# Journal\n\nMy line.\n")
    if browser_name == "webkit":
        page.locator("#docSave").click()
    else:
        page.keyboard.press("Control+s")
    conflict = page.locator(".doc-conflict")
    expect(conflict).to_contain_text("Changed on disk since you opened it")
    assert _source(page, app_url, loc)["text"] == theirs
    conflict.locator('[data-conflict="compare"]').click()
    expect(conflict.locator(".doc-diff-del")).to_contain_text(["Their line."])
    expect(conflict.locator(".doc-diff-add")).to_contain_text(["My line."])
    _themes(page)

    conflict.locator('[data-conflict="overwrite"]').click()
    confirm = page.locator('dialog[data-track-view="doc-overwrite"]')
    expect(confirm).to_contain_text("kept as a backup")
    confirm.locator('[data-confirm="overwrite"]').click()
    expect(conflict).to_have_count(0)
    expect(page.locator(".doc-edit-status")).to_have_text("Saved")
    assert _source(page, app_url, loc)["text"] == "# Journal\n\nMy line.\n"
    assert errors == []


def test_an_answer_with_no_reason_is_not_called_a_disk_read_failure(page: Page, app_url: str) -> None:
    """A plain 404 (the page newer than the running app, as during a restart) and no answer at all each say what they are."""
    loc = _journal(page, app_url)
    not_found = json.dumps({"detail": "Not Found"})
    page.route("**/api/doc/source?*", lambda route: route.fulfill(status=404, content_type="application/json", body=not_found))
    page.goto(app_url + "/doc?" + urlencode(loc))
    expect(page.locator("#docBody h1")).to_have_text("Journal")
    page.locator('.doc-mode [data-mode="edit"]').click()
    state = page.locator(".doc-state")
    expect(state).to_contain_text("different versions")
    expect(state).not_to_contain_text("Windows could not read it")
    expect(state.locator('[data-doc-act="reload"]')).to_be_visible()

    page.unroute("**/api/doc/source?*")
    page.route("**/api/doc?*", lambda route: route.abort())
    page.goto(app_url + "/doc?" + urlencode(loc))
    expect(page.locator(".doc-state")).to_contain_text("did not answer")
    expect(page.locator(".doc-state")).not_to_contain_text("Windows could not read it")
