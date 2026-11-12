"""A Markdown file dropped on the window: matched to its indexed copy by name, size and modified time, or rendered read-only.

The page sends a dropped file's name, size and modified time (and, when no
indexed copy matches, its text), never a path. Neither route reads the disk:
the tests replace the reader's and the listing's seams with ones that fail.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.webapp.server import app
from src import winfs
from src.docs import reader
from tests.conftest import write_config
from tests.fixture_tree import BIG_MD_BYTES, DOCS_FOLDER, add_docs, build_tree

LOCAL = ("127.0.0.1", 50000)
MATCH = "/api/doc/dropped/match"
RENDER = "/api/doc/dropped/render"
STAMP = 1_700_000_000


def _scan(c: TestClient) -> None:
    assert c.post("/api/scan", json={}).status_code == 202
    for _ in range(200):
        if not c.get("/api/scan").json()["scanning"]:
            return
        time.sleep(0.05)
    raise AssertionError("the scan did not finish")


def _drop(file: Path, **over: Any) -> dict[str, Any]:
    """What the page sends for a dropped file: its name, size and modified time in milliseconds, as a browser reports them."""
    st = file.stat()
    return {"name": file.name, "size": st.st_size, "modified": int(st.st_mtime * 1000), **over}


@pytest.fixture()
def tree(isolated: Path) -> Path:
    root = add_docs(build_tree(isolated / "Invented root"))
    for path in root.rglob("*.md"):
        os.utime(path, (STAMP, STAMP))
    write_config(isolated, roots=[str(root)])
    return root


@pytest.fixture()
def client(tree: Path) -> Any:
    with TestClient(app, client=LOCAL) as c:
        _scan(c)
        yield c


@pytest.fixture()
def no_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any read, listing or stat of a document fails the test: both routes answer from the index and the page's own text."""
    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("the dropped-file routes read the disk")

    monkeypatch.setattr(winfs, "list_dir", boom)
    for name in ("read_document", "read_document_listed", "read_image", "find_document", "_read", "_listed"):
        monkeypatch.setattr(reader, name, boom)


def _id(c: TestClient, tree: Path, name: str) -> dict[str, Any]:
    root = c.get("/api/roots").json()["roots"][0]
    top = c.get("/api/folder", params={"root": root["key"]}).json()
    notes = next(it["id"] for it in top["items"] if it["name"] == DOCS_FOLDER)
    body = c.get("/api/folder", params={"root": root["key"], "folder": notes}).json()
    return {"root": root["key"], "scan_id": body["scan_id"], "id": next(it["id"] for it in body["items"] if it["name"] == name)}


def test_a_dropped_file_that_is_indexed_opens_that_copy(client: TestClient, tree: Path, no_disk: None) -> None:
    res = client.post(MATCH, json=_drop(tree / DOCS_FOLDER / "guide.md"))
    assert res.status_code == 200
    assert res.json() == {"match": _id(client, tree, "guide.md")}


def test_a_name_matches_whatever_its_case(client: TestClient, tree: Path) -> None:
    res = client.post(MATCH, json=_drop(tree / DOCS_FOLDER / "guide.md", name="GUIDE.MD"))
    assert res.json()["match"] is not None


@pytest.mark.parametrize("change", [
    {"name": "other.md"},
    {"size": 1},
    {"modified": (STAMP + 5) * 1000},
    {"modified": (STAMP - 5) * 1000},
])
def test_a_file_that_differs_in_name_size_or_time_is_unknown(client: TestClient, tree: Path, change: dict[str, Any]) -> None:
    assert client.post(MATCH, json=_drop(tree / DOCS_FOLDER / "guide.md", **change)).json() == {"match": None}


def test_a_modified_time_a_browser_rounded_still_matches(client: TestClient, tree: Path) -> None:
    for ms in (STAMP * 1000 + 999, STAMP * 1000 - 400):
        assert client.post(MATCH, json=_drop(tree / DOCS_FOLDER / "guide.md", modified=ms)).json()["match"] is not None


def test_two_indexed_copies_are_not_a_match(client: TestClient, tree: Path) -> None:
    twin = tree / DOCS_FOLDER / "sub" / "guide.md"
    shutil.copy2(tree / DOCS_FOLDER / "guide.md", twin)
    _scan(client)
    assert client.post(MATCH, json=_drop(twin)).json() == {"match": None}


def test_a_path_the_page_sends_is_never_used(client: TestClient, tree: Path, no_disk: None) -> None:
    body = _drop(tree / DOCS_FOLDER / "guide.md", path=str(tree / "elsewhere" / "other.md"), webkitRelativePath="x/other.md")
    assert client.post(MATCH, json=body).json() == {"match": _id(client, tree, "guide.md")}
    other = _drop(tree / DOCS_FOLDER / "guide.md", name="nothing.md", path=str(tree / DOCS_FOLDER / "guide.md"))
    assert client.post(MATCH, json=other).json() == {"match": None}


@pytest.mark.parametrize("name", ["..\\guide.md", "sub/guide.md", "C:guide.md", "..", ""])
def test_a_name_that_is_a_path_is_refused(client: TestClient, tree: Path, name: str) -> None:
    for route, extra in ((MATCH, {"size": 10, "modified": STAMP * 1000}), (RENDER, {"text": "# x"})):
        res = client.post(route, json={"name": name, **extra})
        assert res.status_code == 422, (route, name)


def test_only_markdown_names_are_taken(client: TestClient, tree: Path) -> None:
    for route, extra in ((MATCH, {"size": 10, "modified": STAMP * 1000}), (RENDER, {"text": "x"})):
        res = client.post(route, json={"name": "notes.txt", **extra})
        assert res.status_code == 415 and res.json()["reason"] == "not_document"


@pytest.mark.parametrize("body", [
    {"name": "a.md", "size": "10", "modified": 1}, {"name": "a.md", "size": 10, "modified": "1"},
    {"name": "a.md", "size": True, "modified": 1}, {"name": "a.md", "size": -1, "modified": 1},
    {"name": 5, "size": 1, "modified": 1}, {"size": 1, "modified": 1},
])
def test_a_malformed_match_is_refused(client: TestClient, body: dict[str, Any]) -> None:
    assert client.post(MATCH, json=body).status_code == 422


def test_a_file_past_the_size_cap_is_refused_before_matching(client: TestClient, tree: Path) -> None:
    res = client.post(MATCH, json=_drop(tree / DOCS_FOLDER / "big.md"))
    assert res.status_code == 413 and res.json()["reason"] == "too_large"
    assert client.post(RENDER, json={"name": "big.md", "text": "#" * BIG_MD_BYTES}).status_code == 413


def test_an_unknown_file_renders_read_only_without_reading_the_disk(client: TestClient, no_disk: None) -> None:
    text = "# Unsaved note\n\n## Part\n\nSee [the guide](guide.md), ![a plan](plan.png) and [web](https://example.invalid/x).\n\n<b>raw</b>\n"
    res = client.post(RENDER, json={"name": "scratch.md", "text": text})
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "scratch.md" and body["dropped"] is True
    assert [h["text"] for h in body["outline"]] == ["Unsaved note", "Part"]
    assert "<h1" in body["html"]
    # A relative link and image have no folder to resolve against, so they render inert; raw HTML stays text.
    assert 'class="doc-link-off"' in body["html"] and "/doc?" not in body["html"]
    assert "data-doc-img" not in body["html"] and "doc-img-off" in body["html"]
    assert "&lt;b&gt;" in body["html"]


def test_a_dropped_text_must_be_text(client: TestClient) -> None:
    assert client.post(RENDER, json={"name": "a.md", "text": 5}).status_code == 422
    assert client.post(RENDER, json={"name": "a.md"}).status_code == 422
