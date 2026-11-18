"""A Markdown file dropped from a folder no scan covers opens with Edit and saves through the browser's own file handle.

Playwright can't drag from the desktop, so the drop is the page's own File and
DataTransfer (as in test_doc_drop.py) and the drop's item is told to answer
``getAsFileSystemHandle`` with a real handle: one from the origin's private
file system, which is the same FileSystemFileHandle a dropped file gets, clones
between windows the same way, and needs no permission prompt to write. The
Save prompt of a real desktop drop is checked by hand on a real Chromium.
The server only ever renders text the page sends: no save, no source, no path.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e._layout_lint import assert_layout_clean
from tests.e2e.test_doc_drop import _drop, _errors, _only_the_page_knows, _requests

pytestmark = pytest.mark.desktop_only

SEED_HANDLE = """async ({ name, text }) => {
  const dir = await navigator.storage.getDirectory();
  const handle = await dir.getFileHandle(name, { create: true });
  const out = await handle.createWritable();
  await out.write(text);
  await out.close();
  window.seededHandle = handle;
  DataTransferItem.prototype.getAsFileSystemHandle = async () => window.seededHandle;
}"""
ON_DISK = """async (name) => { const dir = await navigator.storage.getDirectory(); return await (await (await dir.getFileHandle(name)).getFile()).text(); }"""
CHANGE_ON_DISK = """async ({ name, text }) => {
  const dir = await navigator.storage.getDirectory();
  const out = await (await dir.getFileHandle(name)).createWritable();
  await out.write(text);
  await out.close();
}"""
# What the window may ask the server about a document with no scan: to render text it sends.
LOCAL_ROUTES = {"/api/doc/dropped/render"}


def _doc_requests(page: Page) -> list[str]:
    """The path of every document request the page makes, but the recent list."""
    seen: list[str] = []
    page.on("request", lambda r: seen.append("/" + r.url.split("/", 3)[3].split("?")[0]) if "/api/doc/" in r.url and "/api/doc/recent" not in r.url else None)
    return seen


def test_a_dropped_file_with_a_handle_opens_with_edit_and_saves_through_it(page: Page, app_url: str) -> None:
    errors = _errors(page)
    sent = _requests(page)
    page.goto(app_url + "/")
    text = "# Elsewhere\n\nA note from a folder no scan covers.\n"
    page.evaluate(SEED_HANDLE, {"name": "elsewhere.md", "text": text})
    with page.expect_popup() as opened:
        _drop(page, "elsewhere.md", len(text.encode()), text=text)
    win = opened.value
    asked = _doc_requests(win)

    expect(win.locator("#docTitle")).to_have_text("elsewhere.md")
    expect(win.locator("#docBody h1")).to_have_text("Elsewhere")
    expect(win.locator("#docBody .doc-drop-note")).to_contain_text("Edit saves it back to the file")
    expect(win.locator(".doc-modebar")).to_be_visible()
    for theme in ("light", "dark"):
        win.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
        assert_layout_clean(win)
    win.locator('.doc-mode [data-mode="edit"]').click()
    expect(win.locator(".doc-src-input")).to_have_value(text)
    win.locator(".doc-src-input").fill("# Elsewhere\n\nSaved through the handle.\n")
    expect(win.locator(".doc-edit-preview h1")).to_have_text("Elsewhere")
    win.locator("#docSave").click()
    expect(win.locator(".doc-edit-status")).to_have_text("Saved")
    assert page.evaluate(ON_DISK, "elsewhere.md") == "# Elsewhere\n\nSaved through the handle.\n"

    # Back in View the window shows the file as it is on disk now.
    win.keyboard.press("Escape")
    expect(win.locator("#docBody .doc-prose p:not(.doc-drop-note)")).to_have_text("Saved through the handle.")
    # A second edit is checked against the saved version, so it does not conflict with the first save.
    win.locator('.doc-mode [data-mode="edit"]').click()
    win.locator(".doc-src-input").fill("# Elsewhere\n\nSaved twice.\n")
    win.locator("#docSave").click()
    expect(win.locator(".doc-edit-status")).to_have_text("Saved")
    assert page.evaluate(ON_DISK, "elsewhere.md") == "# Elsewhere\n\nSaved twice.\n"

    assert set(asked) <= LOCAL_ROUTES, asked
    assert [route for route, _ in sent] == ["match", "render"]
    _only_the_page_knows(sent)
    assert errors == []


def test_a_dropped_file_changed_on_disk_stops_at_the_conflict_screen(page: Page, app_url: str) -> None:
    errors = _errors(page)
    page.goto(app_url + "/")
    sheet = page.locator("#docDialog")
    page.evaluate("() => { window.open = () => null; }")  # the pop-up is blocked, so the app's own sheet takes the file
    text = "# Elsewhere\n\nFirst copy.\n"
    page.evaluate(SEED_HANDLE, {"name": "contested.md", "text": text})
    _drop(page, "contested.md", len(text.encode()), text=text)

    expect(sheet.locator("#docDialogTitle")).to_have_text("contested.md")
    expect(sheet.locator(".doc-mode")).to_be_visible()
    expect(sheet.locator("#docWindow")).to_be_hidden()
    sheet.locator('.doc-mode [data-mode="edit"]').click()
    sheet.locator(".doc-src-input").fill("# Elsewhere\n\nMy copy.\n")

    page.evaluate(CHANGE_ON_DISK, {"name": "contested.md", "text": "# Elsewhere\n\nSomeone else's copy.\n"})
    sheet.locator("#docSave").click()
    expect(sheet.locator(".doc-conflict")).to_be_visible()
    assert page.evaluate(ON_DISK, "contested.md") == "# Elsewhere\n\nSomeone else's copy.\n"
    for theme in ("light", "dark"):
        page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
        assert_layout_clean(page)

    # Overwrite asks first, says nothing is kept for a file the page saves itself, and only then replaces the copy.
    sheet.locator('[data-conflict="overwrite"]').click()
    dialog = page.locator("dialog[open]").last
    expect(dialog).to_contain_text("not kept")
    dialog.get_by_role("button", name="Overwrite", exact=True).click()
    expect(sheet.locator(".doc-edit-status")).to_have_text("Saved")
    assert page.evaluate(ON_DISK, "contested.md") == "# Elsewhere\n\nMy copy.\n"
    assert errors == []


def test_a_dropped_file_with_no_handle_says_why_it_is_read_only(page: Page, app_url: str) -> None:
    errors = _errors(page)
    page.goto(app_url + "/")
    page.evaluate("() => { window.open = () => null; DataTransferItem.prototype.getAsFileSystemHandle = async () => null; }")
    text = "# Scratch note\n\nJust text.\n"
    _drop(page, "scratch.md", len(text.encode()), text=text)
    expect(page.locator("#docDialog .doc-drop-note")).to_contain_text("read-only")
    expect(page.locator("#docDialog .doc-mode")).to_be_hidden()
    expect(page.locator("#toast")).to_contain_text("no file to save back to")

    # A browser without the API at all is told to use one that has it.
    page.locator("#docDialog [data-close]").click()
    page.evaluate("() => { delete DataTransferItem.prototype.getAsFileSystemHandle; }")
    _drop(page, "scratch.md", len(text.encode()), text=text)
    expect(page.locator("#toast")).to_contain_text("Chrome or Edge")
    assert errors == []
