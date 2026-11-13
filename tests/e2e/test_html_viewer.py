"""The HTML viewer: a page in its sandboxed frame in Explore's sheet and the /doc window, edited, and dropped.

The pages are invented fixture files under a temp root ("Media disk" in
conftest): ``page.html`` with a stylesheet, a script and an image beside it,
and a probe script that records in its own body whether it reached the app's
page, cookies or API; ``sketch.htm`` is the one the editor changes. Playwright
reads inside the frame (its origin is opaque, so the app's page can't), and
every request the frame sends to the API is recorded with the answer it got.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

import pytest
from playwright.sync_api import FrameLocator, Page, Request, expect

from tests.e2e._layout_lint import assert_layout_clean
from tests.fixture_tree import PAGES_FOLDER

# What drawDoc gives the frame (doc-view.js FRAME_SANDBOX): scripts and popups, never the app's origin.
SANDBOX = "allow-scripts allow-popups allow-popups-to-escape-sandbox"
DROP = """({ name, size, modified, text }) => {
  const file = new File([text === null ? new Uint8Array(size) : text], name, { lastModified: modified });
  Object.defineProperty(file, 'path', { value: 'C:\\\\Invented\\\\Elsewhere\\\\' + name });
  const dt = new DataTransfer();
  dt.items.add(file);
  for (const type of ['dragenter', 'dragover', 'drop']) document.body.dispatchEvent(new DragEvent(type, { dataTransfer: dt, bubbles: true, cancelable: true }));
}"""


def _errors(page: Page) -> list[str]:
    """The page's script errors; WebKit reports the frame's own refused CORS call as one, which is the point, not a fault."""
    errors: list[str] = []
    page.on("pageerror", lambda e: None if "access control checks" in str(e) else errors.append(str(e)))
    return errors


def _framed_calls(page: Page) -> list[dict[str, Any]]:
    """Every API request a frame inside the page sends: URL, method, its Origin header and the status the app answered.

    A no-cors request finishes with an opaque answer whose status the browser still records; a CORS one fails on
    the browser's own check, so it has no status here and the test replays it with the same Origin.
    """
    calls: list[dict[str, Any]] = []

    def seen(r: Request) -> None:
        if "/api/" in r.url and r.frame != page.main_frame:
            answer = r.response()
            calls.append({"url": r.url, "method": r.method, "origin": r.all_headers().get("origin"),
                          "status": answer.status if answer else None})

    page.on("requestfinished", seen)
    page.on("requestfailed", seen)
    return calls


def _file(page: Page, app_url: str, name: str) -> dict[str, Any]:
    """An invented file's place, size and modified time, read from the app's own listing."""
    root = next(r for r in page.request.get(app_url + "/api/roots").json()["roots"] if r["name"].startswith("Media disk"))
    top = page.request.get(app_url + "/api/folder?" + urlencode({"root": root["key"]})).json()
    pages = next(it["id"] for it in top["items"] if it["name"] == PAGES_FOLDER)
    body = page.request.get(app_url + "/api/folder?" + urlencode({"root": root["key"], "folder": pages})).json()
    item = next(it for it in body["items"] if it["name"] == name)
    return {"root": root["key"], "scan": body["scan_id"], "id": item["id"], "size": item["size"], "modified": item["mtime"] * 1000}


def _walled_off(page: Page, frame: FrameLocator, calls: list[dict[str, Any]]) -> None:
    """The page rendered as a browser would, its scripts ran, and none of them reached the app."""
    expect(frame.locator("h1")).to_have_text("Invented page")
    expect(frame.locator(".styled")).to_have_css("color", "rgb(0, 128, 0)")
    expect(frame.locator("html")).to_have_attribute("data-page-js", "ran")
    expect(frame.locator('img[alt="Bed plan"]')).to_have_js_property("naturalWidth", 1)
    body = frame.locator("body")
    expect(body).to_have_attribute("data-parent", "blocked")
    expect(body).to_have_attribute("data-cookie", "blocked")
    expect(body).to_have_attribute("data-storage", "blocked")
    expect(body).to_have_attribute("data-quiet", "sent")
    expect(body).to_have_attribute("data-api", "blocked")
    # The no-cors call and the CORS one both went out, and the app on this PC refused each.
    by_route = {c["url"].split("/api/")[1].split("?")[0]: c for c in calls}
    assert set(by_route) >= {"doc/render", "doc/recent"}, calls
    assert by_route["doc/recent"]["status"] == 403, calls
    cors = by_route["doc/render"]
    assert cors["origin"] == "null", calls
    answer = page.request.fetch(cors["url"], method=cors["method"], headers={"Origin": "null"})
    assert answer.status == 403 and answer.json()["reason"] == "framed_page"


def test_an_html_row_opens_in_the_sheet_walled_off_and_in_its_own_window(page: Page, app_url: str, browser_name: str) -> None:
    errors = _errors(page)
    calls = _framed_calls(page)
    # A cookie and a stored value on the app's own origin: the page's probe reports whether it can read either.
    page.context.add_cookies([{"name": "probe", "value": "1", "url": app_url}])
    page.goto(app_url + "/")
    page.evaluate("() => localStorage.setItem('folder-explorer.probe', '1')")
    assert "probe=" in page.evaluate("() => document.cookie")
    page.locator('.tab[data-tab="explore"]').click()
    expect(page.locator("#rootSelect option", has_text="Media disk")).to_have_count(1)
    media = page.eval_on_selector("#rootSelect", "s => [...s.options].find(o => o.text.startsWith('Media disk')).value")
    page.locator("#rootSelect").select_option(media)
    rows = page.locator("#contentsDialog" if browser_name == "webkit" else "#contentsDock")
    # The tree lists only the largest folders; the small pages folder is reached from the root's contents.
    if browser_name == "webkit":
        page.locator(".tree-main").first.click()
    rows.locator(".list-row", has_text=PAGES_FOLDER).click()
    row = rows.locator(".list-row", has_text="page.html")
    expect(row.locator(".doc-badge")).to_have_text("HTML")
    if browser_name == "webkit":
        row.click()
    else:
        row.dblclick()

    sheet = page.locator("#docDialog")
    expect(sheet.locator("#docDialogTitle")).to_have_text("page.html")
    iframe = sheet.locator("#docSheetBody iframe.doc-frame")
    expect(iframe).to_have_attribute("sandbox", SANDBOX)
    _walled_off(page, sheet.frame_locator("#docSheetBody iframe.doc-frame"), calls)
    for theme in ("light", "dark"):
        page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
        assert_layout_clean(page)

    window = sheet.locator("#docWindow").get_attribute("href") or ""
    assert "id=" in window
    sheet.locator("[data-close]").click()
    calls.clear()
    page.goto(app_url + window)
    expect(page.locator("#docTitle")).to_have_text("page.html")
    expect(page.locator("#docBody iframe.doc-frame")).to_have_attribute("sandbox", SANDBOX)
    _walled_off(page, page.frame_locator("#docBody iframe.doc-frame"), calls)
    for theme in ("light", "dark"):
        page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
        assert_layout_clean(page)
    assert errors == []


@pytest.mark.desktop_only
def test_an_html_file_edits_with_a_live_frame_preview_and_saves(page: Page, app_url: str) -> None:
    errors = _errors(page)
    sketch = _file(page, app_url, "sketch.htm")
    loc = {k: sketch[k] for k in ("root", "scan", "id")}
    page.goto(app_url + "/doc?" + urlencode(loc))
    expect(page.frame_locator("#docBody iframe.doc-frame").locator("h1")).to_have_text("Sketch")
    page.locator('.doc-mode [data-mode="edit"]').click()
    source = page.locator(".doc-src-input")
    expect(source).to_have_value("<!doctype html>\n<h1>Sketch</h1>\n<p>First draft.</p>\n")
    expect(page.locator(".doc-format")).to_be_hidden()
    preview = page.frame_locator(".doc-edit-preview iframe.doc-frame")
    expect(preview.locator("p")).to_have_text("First draft.")

    source.fill("<!doctype html>\n<h1>Sketch</h1>\n<p>Second draft.</p>\n")
    expect(preview.locator("p")).to_have_text("Second draft.")
    page.locator("#docSave").click()
    expect(page.locator(".doc-edit-status")).to_have_text("Saved")
    saved = page.request.get(app_url + "/api/doc/source?" + urlencode(loc)).json()
    assert saved["text"] == "<!doctype html>\n<h1>Sketch</h1>\n<p>Second draft.</p>\n" and saved["kind"] == "html"
    for theme in ("light", "dark"):
        page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
        assert_layout_clean(page)
    assert errors == []


@pytest.mark.desktop_only
def test_a_dropped_html_file_opens_its_own_window_editable_or_read_only(page: Page, app_url: str) -> None:
    errors = _errors(page)
    sent: list[tuple[str, Any]] = []
    page.on("request", lambda r: sent.append((r.url.split("/api/doc/dropped/")[1], r.post_data_json)) if "/api/doc/dropped/" in r.url else None)
    item = _file(page, app_url, "page.html")
    page.goto(app_url + "/")

    with page.expect_popup() as opened:
        page.evaluate(DROP, {"name": "page.html", "size": item["size"], "modified": item["modified"], "text": None})
    win = opened.value
    win.wait_for_url("**/doc?*id=%d*" % item["id"])
    expect(win.frame_locator("#docBody iframe.doc-frame").locator("h1")).to_have_text("Invented page")
    expect(win.locator("#docBody .doc-drop-note")).to_have_count(0)
    expect(win.locator('.doc-mode [data-mode="edit"]')).to_be_visible()
    win.close()

    text = "<!doctype html><h1>Loose page</h1><img alt=\"Plan\" src=\"diagram.png\">"
    with page.expect_popup() as opened:
        page.evaluate(DROP, {"name": "loose.html", "size": len(text.encode()), "modified": 1_700_000_000_000, "text": text})
    win = opened.value
    expect(win.locator("#docTitle")).to_have_text("loose.html")
    expect(win.locator("#docBody .doc-drop-note")).to_contain_text("read-only")
    expect(win.frame_locator("#docBody iframe.doc-frame").locator("h1")).to_have_text("Loose page")
    expect(win.locator(".doc-modebar")).to_be_hidden()

    allowed = {"match": {"name", "size", "modified", "sha256"}, "render": {"name", "text"}}
    assert [route for route, _ in sent] == ["match", "match", "render"]
    for route, body in sent:
        assert set(body) == allowed[route], (route, sorted(body))
        assert "Invented" not in json.dumps({k: v for k, v in body.items() if k != "text"}), (route, body)
    assert errors == []
