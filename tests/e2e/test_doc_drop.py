"""A Markdown file dropped on the window opens in its own /doc window: an indexed one with Edit, any other read-only; the rest is ignored.

Playwright can't drag from the desktop, so each test builds the browser's own
File and DataTransfer in the page and dispatches the drag events. The indexed
case takes its name, size and modified time from the app's listing of the
invented fixture, so it is the very file the scan saw; the other files are
invented. A blocked pop-up is a window.open that answers null, and the drop
then shows the file in the app's own sheet. Every request the drop makes is recorded: none may carry a path.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

import pytest
from playwright.sync_api import Page, expect

from tests.e2e._layout_lint import assert_layout_clean
from tests.fixture_tree import DOCS_FOLDER

pytestmark = pytest.mark.desktop_only

# The File gets a path-like property, as some desktop browser shells add one: it must never reach the server.
DROP ="""({ name, size, modified, text, step }) => {
  const file = new File([text === null ? new Uint8Array(size) : text], name, { lastModified: modified });
  Object.defineProperty(file, 'path', { value: 'C:\\\\Invented\\\\Elsewhere\\\\' + name });
  const dt = new DataTransfer();
  dt.items.add(file);
  for (const type of step) document.body.dispatchEvent(new DragEvent(type, { dataTransfer: dt, bubbles: true, cancelable: true }));
}"""


def _errors(page: Page) -> list[str]:
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return errors


def _requests(page: Page) -> list[tuple[str, Any]]:
    """Every request the page makes to the dropped-file routes: (path, JSON body)."""
    sent: list[tuple[str, Any]] = []
    page.on("request", lambda r: sent.append((r.url.split("/api/doc/dropped/")[1], r.post_data_json)) if "/api/doc/dropped/" in r.url else None)
    return sent


def _guide(page: Page, app_url: str) -> dict[str, Any]:
    """The invented guide as the scan has it: its place, size and modified time."""
    root = next(r for r in page.request.get(app_url + "/api/roots").json()["roots"] if r["name"].startswith("Media disk"))
    top = page.request.get(app_url + "/api/folder?" + urlencode({"root": root["key"]})).json()
    notes = next(it["id"] for it in top["items"] if it["name"] == DOCS_FOLDER)
    body = page.request.get(app_url + "/api/folder?" + urlencode({"root": root["key"], "folder": notes})).json()
    item = next(it for it in body["items"] if it["name"] == "guide.md")
    return {"root": root["key"], "scan": body["scan_id"], "id": item["id"], "size": item["size"], "modified": item["mtime"] * 1000}


def _drop(page: Page, name: str, size: int = 0, modified: int = 1_700_000_000_000, text: Any = None) -> None:
    page.evaluate(DROP, {"name": name, "size": size, "modified": modified, "text": text, "step": ["dragenter", "dragover", "drop"]})


def _only_the_page_knows(sent: list[tuple[str, Any]]) -> None:
    """Each recorded body holds only what the page may send, and no path text anywhere."""
    allowed = {"match": {"name", "size", "modified"}, "render": {"name", "text"}}
    for route, body in sent:
        assert set(body) == allowed[route], (route, sorted(body))
        assert "Invented" not in json.dumps({k: v for k, v in body.items() if k != "text"}), (route, body)


def test_a_dropped_indexed_file_opens_in_its_own_window_with_edit(page: Page, app_url: str) -> None:
    errors = _errors(page)
    sent = _requests(page)
    guide = _guide(page, app_url)
    page.goto(app_url + "/")

    # The drag shows the target, the drop opens the indexed copy in a /doc window, as Explore's "Open in new window" does.
    page.evaluate(DROP, {"name": "guide.md", "size": guide["size"], "modified": guide["modified"], "text": None, "step": ["dragenter"]})
    expect(page.locator(".drop-overlay")).to_be_visible()
    for theme in ("light", "dark"):
        page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
        assert_layout_clean(page)
    page.evaluate(DROP, {"name": "guide.md", "size": 1, "modified": 0, "text": None, "step": ["dragleave"]})
    expect(page.locator(".drop-overlay")).to_be_hidden()

    with page.expect_popup() as opened:
        _drop(page, "guide.md", guide["size"], guide["modified"])
    win = opened.value
    win.wait_for_url("**/doc?*id=%d*" % guide["id"])
    expect(win.locator("#docTitle")).to_have_text("guide.md")
    expect(win.locator("#docBody h1")).to_have_text("Garden guide")
    expect(win.locator("#docBody .doc-drop-note")).to_have_count(0)
    win.locator('.doc-mode [data-mode="edit"]').click()
    expect(win.locator(".doc-src-input")).to_be_visible()
    # The app's own page is left as it was: no sheet, no overlay.
    expect(page.locator("#docDialog")).to_be_hidden()
    expect(page.locator(".drop-overlay")).to_be_hidden()
    assert [route for route, _ in sent] == ["match"]
    _only_the_page_knows(sent)
    assert errors == []


def test_a_dropped_unknown_file_opens_read_only_in_its_own_window(page: Page, app_url: str) -> None:
    errors = _errors(page)
    sent = _requests(page)
    page.goto(app_url + "/")
    text = "# Scratch note\n\n## Part two\n\nSee [the guide](guide.md) and ![plan](plan.png).\n"
    with page.expect_popup() as opened:
        _drop(page, "scratch.md", len(text.encode()), text=text)
    win = opened.value

    expect(win.locator("#docTitle")).to_have_text("scratch.md")
    expect(win.locator("#docBody h1")).to_have_text("Scratch note")
    expect(win.locator("#docBody .doc-drop-note")).to_contain_text("read-only")
    expect(win.locator("#docBody .doc-link-off")).to_have_count(1)
    expect(win.locator(".doc-modebar")).to_be_hidden()
    expect(win.locator("#docStatus")).to_contain_text("Dropped file")
    for theme in ("light", "dark"):
        win.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
        assert_layout_clean(win)
    expect(page.locator("#docDialog")).to_be_hidden()
    assert [route for route, _ in sent] == ["match", "render"]
    _only_the_page_knows(sent)

    # The text lives in the app's page, not in the address: a reload of the window asks for it once more and is told it is gone.
    assert "Scratch" not in win.url
    win.reload()
    expect(win.locator("#docBody .doc-state")).to_contain_text("Drop it again")
    assert errors == []


def _toast_over_the_sheet(page: Page) -> None:
    """The toast says why and is in the top layer with the sheet: a modal sheet covers any toast outside it, whatever its z-index.

    A hit test can't show it (the modal makes everything outside it inert), so the popover state stands for it.
    """
    toast = page.locator("#toast")
    expect(toast).to_contain_text("pop-up")
    expect(toast).to_be_visible()
    assert page.evaluate("() => document.getElementById('toast').matches(':popover-open')")


def test_a_blocked_pop_up_falls_back_to_the_sheet_with_a_note(page: Page, app_url: str) -> None:
    errors = _errors(page)
    sent = _requests(page)
    guide = _guide(page, app_url)
    page.goto(app_url + "/")
    sheet = page.locator("#docDialog")
    page.evaluate("() => { window.open = () => null; }")  # what a browser that blocks the pop-up answers

    _drop(page, "guide.md", guide["size"], guide["modified"])
    expect(sheet.locator("#docDialogTitle")).to_have_text("guide.md")
    expect(sheet.locator("#docSheetBody h1")).to_have_text("Garden guide")
    _toast_over_the_sheet(page)
    for theme in ("light", "dark"):
        page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
        assert_layout_clean(page)
    sheet.locator("[data-close]").click()
    expect(sheet).to_be_hidden()

    text = "# Scratch note\n\nJust text.\n"
    _drop(page, "scratch.md", len(text.encode()), text=text)
    expect(sheet.locator("#docDialogTitle")).to_have_text("scratch.md")
    expect(sheet.locator(".doc-drop-note")).to_contain_text("read-only")
    expect(sheet.locator(".doc-mode")).to_be_hidden()
    _toast_over_the_sheet(page)
    _only_the_page_knows(sent)
    assert errors == []


def test_a_file_that_is_not_markdown_is_ignored_with_a_note(page: Page, app_url: str) -> None:
    errors = _errors(page)
    sent = _requests(page)
    page.goto(app_url + "/")
    _drop(page, "photo.png", 12)
    expect(page.locator("#toast")).to_contain_text("Only Markdown")
    expect(page.locator("#docDialog")).to_be_hidden()
    assert sent == []
    assert errors == []


def test_the_doc_window_takes_a_drop_too(page: Page, app_url: str) -> None:
    errors = _errors(page)
    sent = _requests(page)
    guide = _guide(page, app_url)
    page.goto(app_url + "/doc?" + urlencode({"root": guide["root"], "scan": guide["scan"], "id": guide["id"]}))
    expect(page.locator("#docTitle")).to_have_text("guide.md")

    text = "# Scratch note\n\nJust text.\n"
    _drop(page, "scratch.md", len(text.encode()), text=text)
    expect(page.locator("#docTitle")).to_have_text("scratch.md")
    expect(page.locator("#docBody h1")).to_have_text("Scratch note")
    expect(page.locator("#docBody .doc-drop-note")).to_be_visible()
    expect(page.locator(".doc-modebar")).to_be_hidden()
    expect(page.locator("#docStatus")).to_contain_text("Dropped file")

    # An indexed file goes to that document's own address, where Edit works.
    _drop(page, "guide.md", guide["size"], guide["modified"])
    page.wait_for_url("**/doc?*id=%d*" % guide["id"])
    expect(page.locator("#docTitle")).to_have_text("guide.md")
    expect(page.locator('.doc-mode [data-mode="edit"]')).to_be_visible()
    _only_the_page_knows(sent)
    assert errors == []
