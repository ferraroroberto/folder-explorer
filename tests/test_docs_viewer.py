"""The Markdown viewer: one reader, lookup by id, the root fence, and the size and cloud-only refusals.

Reads go through ``src.docs.reader``; the refusal tests replace its listing
and open seams, so no test touches a real OneDrive placeholder and every
refusal is proven to happen before a byte is read.
"""

from __future__ import annotations

import ast
import os
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.webapp.server import app
from src import winfs
from src.docs import reader
from src.docs.locate import resolve_href
from tests.conftest import write_config
from tests.fixture_tree import BIG_MD_BYTES, DOCS_FOLDER, TINY_PNG, add_docs, build_tree

LOCAL = ("127.0.0.1", 50000)
REMOTE = ("100.64.0.7", 50000)
ROOT = Path(__file__).resolve().parents[1]
READER = ROOT / "src" / "docs" / "reader.py"

# Calls that read a file's bytes; outside the reader, the viewer's modules make none.
_READ_CALLS = frozenset({"open", "read_bytes", "read_text", "open_osfhandle", "fdopen", "FileResponse", "startfile"})
_VIEWER_MODULES = (ROOT / "src" / "docs", ROOT / "app" / "webapp" / "routers" / "docs.py")


def _calls(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            fn = node.func
            names.add(fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else "")
    return names


def _imports_reader(path: Path) -> bool:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module == "src.docs" and any(a.name == "reader" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "src.docs.reader":
            return True
    return False


def test_only_the_docs_reader_reads_document_bytes() -> None:
    viewer = [p for base in _VIEWER_MODULES for p in ([base] if base.is_file() else base.rglob("*.py"))]
    readers = [p.relative_to(ROOT).as_posix() for p in viewer if p != READER and _calls(p) & _READ_CALLS]
    assert readers == []
    assert {"open_osfhandle", "fdopen"} & _calls(READER)
    # Only the docs route and the docs package reach the reader; nothing else in the app reads a document.
    allowed = {ROOT / "app" / "webapp" / "routers" / "docs.py"}
    importers = [p.relative_to(ROOT).as_posix() for folder in ("src", "app", "scripts") for p in (ROOT / folder).rglob("*.py")
                 if _imports_reader(p) and p not in allowed and (ROOT / "src" / "docs") not in p.parents]
    assert importers == []


@pytest.mark.parametrize("href", [
    "../../elsewhere.md", "..\\..\\elsewhere.md", "sub/../../../elsewhere.md", "%2e%2e/%2e%2e/elsewhere.md",
    "/elsewhere.md", "C:/elsewhere.md", "c:elsewhere.md", "\\\\server\\share\\x.md", "file:///C:/x.md",
    "https://example.invalid/x.md", "", "#top",
])
def test_a_link_never_leaves_the_root(tmp_path: Path, href: str) -> None:
    root = tmp_path / "Invented root"
    doc = root / "Notes" / "guide.md"
    assert resolve_href(str(root), str(doc), href) is None


def test_a_relative_link_resolves_inside_the_root(tmp_path: Path) -> None:
    root = tmp_path / "Invented root"
    doc = str(root / "Notes" / "guide.md")
    assert resolve_href(str(root), doc, "sub/watering.md#schedule") == str(root / "Notes" / "sub" / "watering.md")
    assert resolve_href(str(root), doc, "../README%20copy.md") == str(root / "README copy.md")
    assert resolve_href(str(root), doc, "./diagram.png?raw=1") == str(root / "Notes" / "diagram.png")


@pytest.fixture()
def docs(isolated: Path) -> dict[str, Any]:
    tree = add_docs(build_tree(isolated / "Invented root"))
    write_config(isolated, roots=[str(tree)])
    with TestClient(app, client=LOCAL) as c:
        assert c.post("/api/scan", json={}).status_code == 202
        for _ in range(200):
            if not c.get("/api/scan").json()["scanning"]:
                break
            time.sleep(0.05)
        root = c.get("/api/roots").json()["roots"][0]
        top = c.get("/api/folder", params={"root": root["key"]}).json()
        notes = next(it["id"] for it in top["items"] if it["name"] == DOCS_FOLDER)
        body = c.get("/api/folder", params={"root": root["key"], "folder": notes}).json()
        sub = next(it["id"] for it in body["items"] if it["name"] == "sub")
        deeper = c.get("/api/folder", params={"root": root["key"], "folder": sub}).json()
    ids = {it["name"]: it["id"] for it in body["items"] + deeper["items"] if it["kind"] == "file"}
    ids["notes.txt"] = next(it["id"] for it in top["items"] if it["name"] == "notes.txt")
    return {"root": root["key"], "path": root["path"], "scan": body["scan_id"], "ids": ids}


def _get(c: TestClient, d: dict[str, Any], name: str, route: str = "/api/doc", **over: Any) -> Any:
    return c.get(route, params={"root": d["root"], "scan": d["scan"], "id": d["ids"][name], **over})


def test_a_document_renders_by_its_id(docs: dict[str, Any]) -> None:
    with TestClient(app, client=LOCAL) as c:
        r = _get(c, docs, "guide.md")
    assert r.status_code == 200, r.text
    doc = r.json()
    html = doc["html"]
    assert doc["name"] == "guide.md" and doc["id"] == docs["ids"]["guide.md"] and doc["scan_id"] == docs["scan"]
    assert [(h["level"], h["text"]) for h in doc["outline"]] == [(1, "Garden guide"), (2, "Beds"), (2, "Out of reach")]
    assert '<h2 id="' + doc["outline"][1]["id"] + '">Beds</h2>' in html
    assert "<table>" in html and "footnote" in html
    assert '<span class="task-mark is-done" role="img" aria-label="Done">' in html and 'aria-label="To do"' in html and "<input" not in html
    assert '<div class="code-listing">' in html and 'class="k"' in html  # Pygments token classes
    assert "<script>" not in html and "&lt;script&gt;" in html
    watering = "/doc?root=%s&amp;scan=%d&amp;id=%d#schedule" % (docs["root"], docs["scan"], docs["ids"]["watering.md"])
    assert 'href="' + watering + '"' in html and 'data-doc-id="%d"' % docs["ids"]["watering.md"] in html
    assert 'href="https://example.invalid/page" target="_blank" rel="noopener noreferrer"' in html
    assert html.count('class="doc-link-off"') == 2  # the escape and the unscanned page render inert, with no address
    assert 'data-doc-img="%d"' % docs["ids"]["diagram.png"] in html and " src=" not in html
    assert "../" not in html and "elsewhere" not in html and "outside.png" not in html


def test_the_image_beside_a_document_is_served_by_its_id(docs: dict[str, Any]) -> None:
    with TestClient(app, client=LOCAL) as c:
        img = _get(c, docs, "diagram.png", "/api/doc/image")
        not_image = _get(c, docs, "guide.md", "/api/doc/image")
        not_doc = _get(c, docs, "notes.txt")
    assert img.status_code == 200 and img.content == TINY_PNG and img.headers["content-type"] == "image/png"
    assert img.headers["x-content-type-options"] == "nosniff"
    assert not_image.status_code == 415 and not_image.json()["reason"] == "not_document"
    assert not_doc.status_code == 415 and not_doc.json()["reason"] == "not_document"


def test_an_svg_is_served_as_an_image_never_a_page() -> None:
    headers = reader.response_headers(".svg")
    assert headers["Content-Type"] == "image/svg+xml"
    assert "sandbox" in headers["Content-Security-Policy"] and headers["X-Content-Type-Options"] == "nosniff"


def test_an_oversize_document_is_refused_before_any_read(docs: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def never(*_a: Any, **_k: Any) -> bytes:
        raise AssertionError("an oversize file was opened")

    monkeypatch.setattr(reader, "_open_read", never)
    with TestClient(app, client=LOCAL) as c:
        r = _get(c, docs, "big.md")
    assert os.path.getsize(Path(docs["path"]) / DOCS_FOLDER / "big.md") == BIG_MD_BYTES
    assert r.status_code == 413 and r.json()["reason"] == "too_large"


def test_a_document_that_grew_past_the_cap_after_the_listing_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "grown.md"
    path.write_bytes(b"# small")
    monkeypatch.setattr(reader, "_open_read", lambda _p, limit, recall: b"#" * limit)
    with pytest.raises(reader.Refused) as refused:
        reader.read_document(str(path), index_online=False)
    assert refused.value.reason == "too_large"


def _cloud_only(entry: winfs.Entry) -> winfs.Entry:
    return winfs.Entry(entry.name, entry.is_dir, entry.size, 0, entry.attrs | winfs.FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS,
                       entry.mtime, entry.reparse_tag)


def test_a_cloud_only_document_is_refused_and_never_opened(docs: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    listed = reader._listed
    opened: list[bool] = []
    monkeypatch.setattr(reader, "_listed", lambda path: _cloud_only(listed(path)))  # type: ignore[arg-type]

    def record(_path: str, limit: int, recall: bool) -> bytes:
        if not recall:
            raise AssertionError("a cloud-only file was opened without the download tap")
        opened.append(recall)
        return b"# Downloaded"

    monkeypatch.setattr(reader, "_open_read", record)
    with TestClient(app, client=LOCAL) as c:
        r = _get(c, docs, "guide.md")
        img = _get(c, docs, "diagram.png", "/api/doc/image")
        assert opened == []
        downloaded = c.post("/api/doc/download", json={"root": docs["root"], "scan_id": docs["scan"], "id": docs["ids"]["guide.md"]})
    assert r.status_code == 423 and r.json()["reason"] == "cloud_only" and r.json()["detail"] == "Only in OneDrive"
    assert img.status_code == 423 and img.json()["reason"] == "cloud_only"
    assert downloaded.status_code == 200 and "Downloaded" in downloaded.json()["html"] and opened == [True]


def test_a_cloud_only_index_row_is_checked_against_a_fresh_listing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "synced.md"
    path.write_bytes(b"# here")
    # The index saw a placeholder; the folder lists it as local now (downloaded since the scan), so it reads.
    assert reader.read_document(str(path), index_online=True) == b"# here"
    listed = reader._listed
    monkeypatch.setattr(reader, "_listed", lambda p: _cloud_only(listed(p)))  # type: ignore[arg-type]
    with pytest.raises(reader.Refused) as refused:
        reader.read_document(str(path), index_online=False)
    assert refused.value.reason == "cloud_only"


def test_lookups_refuse_a_stale_scan_an_unknown_item_and_a_missing_file(docs: dict[str, Any]) -> None:
    with TestClient(app, client=LOCAL) as c:
        stale = _get(c, docs, "guide.md", scan=docs["scan"] + 1)
        unknown = _get(c, docs, "guide.md", id=999_999)
        other_root = _get(c, docs, "guide.md", root="elsewhere")
        os.remove(Path(docs["path"]) / DOCS_FOLDER / "sub" / "watering.md")
        gone = _get(c, docs, "watering.md")
    assert stale.status_code == 409 and stale.json()["reason"] == "scan_changed"
    assert unknown.status_code == 404 and other_root.status_code == 404
    assert gone.status_code == 410 and gone.json()["reason"] == "gone"


def test_any_signed_in_device_may_view_and_the_page_shell_is_open(docs: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOLDER_EXPLORER_PASSWORD", "invented-password")
    monkeypatch.setenv("FOLDER_EXPLORER_TOKEN", "invented-token")
    with TestClient(app, client=REMOTE) as c:
        assert _get(c, docs, "guide.md").status_code == 401
        signed_in = c.get("/api/doc", params={"root": docs["root"], "scan": docs["scan"], "id": docs["ids"]["guide.md"]},
                          headers={"Authorization": "Bearer invented-token"})
        shell = c.get("/doc", params={"root": docs["root"], "scan": docs["scan"], "id": docs["ids"]["guide.md"]})
    assert signed_in.status_code == 200
    assert shell.status_code == 200 and 'id="docBody"' in shell.text and "Garden guide" not in shell.text
