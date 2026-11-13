"""The Markdown list in Search: every indexed Markdown file across the roots, newest first, narrowed by name.

It reads the index only. The tests prove the list opens no file, leaves out what is
not Markdown and what is hidden, and keeps each hit addressable by root, scan id and id.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.webapp.server import app
from src import winfs
from src.docs import listing, reader
from src.index import Index, open_index
from src.scanner import scan_to_index
from src.winfs import ONLINE_ONLY_ATTRS
from tests.conftest import write_config
from tests.fixture_tree import DOCS_FOLDER, add_docs, build_tree, set_hidden

LOCAL = ("127.0.0.1", 50000)
REMOTE = ("100.64.0.7", 50000)
DAY = 86_400
NOW = 1_700_000_000
# Invented modification times, one per file, so the order never depends on the order the files were written.
STAMPS = {"guide.md": NOW - 1 * DAY, "page.html": NOW - 2 * DAY, "watering.md": NOW - 5 * DAY, "journal.md": NOW - 9 * DAY,
          "big.md": NOW - 30 * DAY, "sketch.htm": NOW - 40 * DAY}
ORDER = ["Roadmap.MD", "guide.md", "page.html", "archive.markdown", "watering.md", "journal.md", "big.md", "sketch.htm"]


def _scan(c: TestClient) -> None:
    assert c.post("/api/scan", json={}).status_code == 202
    for _ in range(200):
        if not c.get("/api/scan").json()["scanning"]:
            return
        time.sleep(0.05)
    raise AssertionError("the scan did not finish")


@pytest.fixture()
def two_roots(isolated: Path) -> dict[str, Path]:
    """Two scanned invented roots: the invented notes (Markdown and HTML), and a small root with an upper-case, a .markdown and a hidden file."""
    first = add_docs(build_tree(isolated / "Invented root"))
    second = isolated / "Second root"
    (second / "Plans").mkdir(parents=True)
    (second / "Plans" / "Roadmap.MD").write_text("# Roadmap\n", encoding="utf-8")
    (second / "Plans" / "archive.markdown").write_text("# Archive\n", encoding="utf-8")
    (second / "Plans" / "plans.txt").write_text("not markdown\n", encoding="utf-8")
    (second / "Plans" / "readme.md.bak").write_text("not markdown either\n", encoding="utf-8")
    (second / "secret.md").write_text("# Hidden\n", encoding="utf-8")
    set_hidden(second / "secret.md")
    for path in (p for p in first.rglob("*") if p.name in STAMPS):
        os.utime(path, (STAMPS[path.name], STAMPS[path.name]))
    os.utime(second / "Plans" / "Roadmap.MD", (NOW, NOW))
    os.utime(second / "Plans" / "archive.markdown", (NOW - 3 * DAY, NOW - 3 * DAY))
    write_config(isolated, roots=[str(first), str(second)])
    return {"first": first, "second": second}


def _names(body: dict[str, Any]) -> list[str]:
    return [h["name"] for h in body["hits"]]


def test_every_root_s_documents_are_listed_newest_first(two_roots: dict[str, Path]) -> None:
    with TestClient(app, client=LOCAL) as c:
        _scan(c)
        body = c.get("/api/search/docs").json()
    assert _names(body) == ORDER
    assert body["total"] == 8


def test_only_documents_are_listed_and_hidden_files_stay_out(two_roots: dict[str, Path]) -> None:
    with TestClient(app, client=LOCAL) as c:
        _scan(c)
        names = _names(c.get("/api/search/docs").json())
        assert "plans.txt" not in names and "readme.md.bak" not in names and "notes.txt" not in names
        assert "secret.md" not in names
        assert c.post("/api/config", json={"explore": {"show_hidden": True}}).status_code == 200
        assert "secret.md" in _names(c.get("/api/search/docs").json())


def test_a_name_query_narrows_the_list_and_every_word_must_match(two_roots: dict[str, Path]) -> None:
    with TestClient(app, client=LOCAL) as c:
        _scan(c)
        assert _names(c.get("/api/search/docs", params={"q": "GUIDE"}).json()) == ["guide.md"]
        assert _names(c.get("/api/search/docs", params={"q": "road md"}).json()) == ["Roadmap.MD"]
        assert _names(c.get("/api/search/docs", params={"q": "guide journal"}).json()) == []
        assert c.get("/api/search/docs", params={"q": "zzz"}).json()["total"] == 0


def test_an_accent_does_not_hide_a_name(isolated: Path) -> None:
    root = isolated / "Invented root"
    root.mkdir()
    (root / "Planificación.md").write_text("# Plan\n", encoding="utf-8")
    write_config(isolated, roots=[str(root)])
    with TestClient(app, client=LOCAL) as c:
        _scan(c)
        assert _names(c.get("/api/search/docs", params={"q": "planificacion"}).json()) == ["Planificación.md"]


def test_a_hit_names_its_file_by_root_scan_and_id_and_opens_by_them(two_roots: dict[str, Path]) -> None:
    with TestClient(app, client=LOCAL) as c:
        _scan(c)
        hit = next(h for h in c.get("/api/search/docs", params={"q": "watering"}).json()["hits"])
        assert set(hit) >= {"root", "scan_id", "id", "name", "ext", "size", "mtime", "online", "folder", "root_name"}
        assert hit["folder"] == [DOCS_FOLDER, "sub"] and hit["ext"] == "md" and hit["online"] is False
        assert Path(hit["path"]) == two_roots["first"] / DOCS_FOLDER / "sub" / "watering.md"
        opened = c.get("/api/doc", params={"root": hit["root"], "scan": hit["scan_id"], "id": hit["id"]})
        assert opened.status_code == 200 and opened.json()["name"] == "watering.md"


def test_building_the_list_opens_and_lists_nothing_on_disk(two_roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    with TestClient(app, client=LOCAL) as c:
        _scan(c)

        def forbid(*_a: Any, **_k: Any) -> None:
            raise AssertionError("the list read the disk")

        for seam in ("read_document", "read_document_listed", "read_image", "find_document"):
            monkeypatch.setattr(reader, seam, forbid)
        monkeypatch.setattr(winfs, "list_dir", forbid)
        assert c.get("/api/search/docs").json()["total"] == 8


def test_a_cloud_only_file_carries_its_online_mark_from_the_index(tmp_path: Path) -> None:
    root = add_docs(build_tree(tmp_path / "Invented root"))
    path = tmp_path / "index.sqlite3"
    scan_to_index(str(root), path, root_name="Invented root")
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE files SET attrs = attrs | ? WHERE name = 'journal.md'", (ONLINE_ONLY_ATTRS,))
    index = Index(open_index(path))
    online = {f["name"]: f["online"] for f in index.files_by_ext(listing.EXTS)}
    assert online == {"guide.md": False, "watering.md": False, "journal.md": True, "big.md": False, "page.html": False,
                      "sketch.htm": False}


def test_the_list_pages_and_the_total_is_the_whole(two_roots: dict[str, Path]) -> None:
    with TestClient(app, client=LOCAL) as c:
        _scan(c)
        first = c.get("/api/search/docs", params={"limit": 4}).json()
        rest = c.get("/api/search/docs", params={"limit": 4, "offset": 4}).json()
    assert first["total"] == rest["total"] == 8
    assert len(first["hits"]) == 4 and len(rest["hits"]) == 4
    assert _names(first) + _names(rest) == ORDER


def test_the_list_says_when_nothing_is_scanned(isolated: Path) -> None:
    write_config(isolated, roots=[str(isolated / "Never scanned")])
    with TestClient(app, client=LOCAL) as c:
        assert c.get("/api/search/status").json()["docs"]["ready"] is False
        assert c.get("/api/search/docs").json() == {"hits": [], "total": 0, "offset": 0}


def test_status_turns_the_list_on_with_a_scan_and_a_signed_in_remote_device_may_ask(
        two_roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    with TestClient(app, client=LOCAL) as c:
        assert c.get("/api/search/status").json()["docs"]["ready"] is False
        _scan(c)
    monkeypatch.setenv("FOLDER_EXPLORER_PASSWORD", "invented-password")
    monkeypatch.setenv("FOLDER_EXPLORER_TOKEN", "invented-token")
    auth = {"Authorization": "Bearer invented-token"}
    with TestClient(app, client=REMOTE) as c:
        assert c.get("/api/search/docs").status_code in (401, 403)
        assert c.get("/api/search/status", headers=auth).json()["docs"]["ready"] is True
        assert c.get("/api/search/docs", headers=auth).json()["total"] == 8


def test_the_extensions_come_from_the_readers_own_list() -> None:
    assert set(listing.EXTS) == {e.lstrip(".") for e in reader.DOC_EXTS}


def test_a_page_asks_for_a_sane_window(two_roots: dict[str, Path]) -> None:
    with TestClient(app, client=LOCAL) as c:
        _scan(c)
        assert c.get("/api/search/docs", params={"limit": 0}).status_code == 422
        assert c.get("/api/search/docs", params={"offset": -1}).status_code == 422
        assert c.get("/api/search/docs", params={"limit": 10_000}).status_code == 422
