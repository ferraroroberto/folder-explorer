"""The HTML viewer: an ``.html``/``.htm`` file served as its frame by the Markdown routes, edited and dropped the same way.

Every byte still comes through ``src.docs.reader``; the stylesheet, script and
image beside a page are found by the same root-fenced lookup as a Markdown
image and never downloaded. The remote switch and the guard that keeps a
framed page's script off the API are held here too.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.webapp.server import app
from src import winfs
from src.docs import reader
from tests.fixture_tree import PAGE_CSS, PAGE_JS, PAGES_FOLDER, SKETCH_HTML, TINY_PNG

LOCAL = ("127.0.0.1", 50000)


def _where(d: dict[str, Any], name: str) -> dict[str, Any]:
    return {"root": d["root"], "scan_id": d["scan"], "id": d["ids"][name]}


def _get(c: TestClient, d: dict[str, Any], name: str, route: str = "/api/doc") -> Any:
    return c.get(route, params={"root": d["root"], "scan": d["scan"], "id": d["ids"][name]})


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_an_html_page_answers_its_frame_with_the_files_beside_it_inlined(docs: dict[str, Any]) -> None:
    with TestClient(app, client=LOCAL) as c:
        r = _get(c, docs, "page.html")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "html" and body["outline"] == [] and body["name"] == "page.html"
    html = body["html"]
    assert "data:text/css;base64," + _b64(PAGE_CSS.encode()) in html
    assert "data:text/javascript;base64," + _b64(PAGE_JS.encode()) in html
    assert "data:image/png;base64," + _b64(TINY_PNG) in html
    assert "/doc?root=" in html and "id=%d" % docs["ids"]["guide.md"] in html
    assert '<img alt="Escaped">' in html
    assert "Content-Security-Policy" not in html


def test_a_markdown_document_still_answers_as_markdown(docs: dict[str, Any]) -> None:
    with TestClient(app, client=LOCAL) as c:
        body = _get(c, docs, "guide.md").json()
    assert body["kind"] == "markdown" and body["outline"]


def test_the_settings_switch_blocks_remote_content(docs: dict[str, Any], isolated: Path) -> None:
    with TestClient(app, client=LOCAL) as c:
        assert c.post("/api/config", json={"docs": {"block_remote_html": True}}).status_code == 200
        html = _get(c, docs, "page.html").json()["html"]
    assert html.startswith('<meta http-equiv="Content-Security-Policy"')
    assert json.loads((isolated / "config.json").read_text(encoding="utf-8"))["docs"] == {"block_remote_html": True}


def test_a_cloud_only_stylesheet_is_never_downloaded(docs: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    listed = reader._listed

    def cloud_css(path: str) -> Any:
        entry = listed(path)
        if entry is not None and path.endswith(".css"):
            return winfs.Entry(entry.name, entry.is_dir, entry.size, 0, entry.attrs | winfs.FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS,
                               entry.mtime, entry.reparse_tag)
        return entry

    opened: list[str] = []
    real_open = reader._open_read

    def record(path: str, limit: int, recall: bool) -> bytes:
        opened.append(path)
        return real_open(path, limit, recall)

    monkeypatch.setattr(reader, "_listed", cloud_css)
    monkeypatch.setattr(reader, "_open_read", record)
    with TestClient(app, client=LOCAL) as c:
        html = _get(c, docs, "page.html").json()["html"]
    assert "data:text/css" not in html
    assert not any(p.endswith(".css") for p in opened)


def test_an_asset_read_refuses_what_is_not_a_stylesheet_or_script(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    with pytest.raises(reader.Refused) as refused:
        reader.read_asset(str(tmp_path / "notes.txt"), index_online=False)
    assert refused.value.reason == "not_document"


def test_an_html_file_edits_with_the_same_version_check(docs: dict[str, Any]) -> None:
    where = _where(docs, "sketch.htm")
    with TestClient(app, client=LOCAL) as c:
        src = c.get("/api/doc/source", params={"root": docs["root"], "scan": docs["scan"], "id": docs["ids"]["sketch.htm"]}).json()
        assert src["text"] == SKETCH_HTML.decode()
        preview = c.post("/api/doc/render", json={**where, "text": "<p>Second draft.</p>"}).json()
        assert preview["kind"] == "html" and "<p>Second draft.</p>" in preview["html"]
        saved = c.post("/api/doc/save", json={**where, "text": "<p>Second draft.</p>\n", "version": src["version"]})
        assert saved.status_code == 200, saved.text
        stale = c.post("/api/doc/save", json={**where, "text": "<p>Third.</p>\n", "version": src["version"]})
    assert stale.status_code == 409 and stale.json()["reason"] == "conflict"
    assert (Path(docs["path"]) / PAGES_FOLDER / "sketch.htm").read_text(encoding="utf-8") == "<p>Second draft.</p>\n"


def test_a_dropped_html_file_renders_read_only_with_nothing_inlined(docs: dict[str, Any]) -> None:
    with TestClient(app, client=LOCAL) as c:
        r = c.post("/api/doc/dropped/render", json={"name": "loose.html", "text": '<img alt="x" src="diagram.png"><p>Loose</p>'})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "html" and body["dropped"] is True
    assert "<p>Loose</p>" in body["html"] and "data:image" not in body["html"]


def test_a_script_in_a_framed_page_cannot_call_the_api(docs: dict[str, Any]) -> None:
    """A sandboxed frame has an opaque origin, so its requests carry ``Origin: null``; the app answers none of them."""
    with TestClient(app, client=LOCAL) as c:
        framed = c.post("/api/doc/render", headers={"Origin": "null"}, content=json.dumps({**_where(docs, "sketch.htm"), "text": "x"}))
        read = c.get("/api/config", headers={"Origin": "null"})
        own = c.post("/api/doc/render", headers={"Origin": "http://127.0.0.1:8453"}, json={**_where(docs, "sketch.htm"), "text": "x"})
    assert framed.status_code == 403 and framed.json()["reason"] == "framed_page"
    assert read.status_code == 403
    assert own.status_code == 200


def test_a_no_cors_request_from_a_framed_page_is_refused_but_a_navigation_is_not() -> None:
    """A no-cors fetch or an ``<img>`` sends no Origin; the browser's ``Sec-Fetch-Site: cross-site`` gives it away."""
    with TestClient(app, client=LOCAL) as c:
        quiet = c.get("/api/config", headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"})
        navigated = c.get("/api/version", headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"})
        same = c.get("/api/config", headers={"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors"})
        shell = c.get("/doc", headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"})
    assert quiet.status_code == 403 and quiet.json()["reason"] == "framed_page"
    assert navigated.status_code == 200 and same.status_code == 200 and shell.status_code == 200
