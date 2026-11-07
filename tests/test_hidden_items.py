"""Hidden and system items: the attribute read, the index's ``hidden`` column, Explore's filter and its switch."""

from __future__ import annotations

import shutil
import sqlite3
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.webapp.server import app
from src import winfs
from src.index import Index, open_index
from src.scanner import (
    HIDDEN_INSIDE,
    HIDDEN_ITSELF,
    VISIBLE,
    hidden_state,
    scan_to_index,
)
from tests.conftest import write_config
from tests.fixture_tree import (
    HIDDEN_BYTES,
    HIDDEN_FILE,
    HIDDEN_FOLDER,
    HIDDEN_FOLDER_FILE,
    TOTAL_BYTES,
    add_hidden,
    build_tree,
)
from tests.test_winfs import _record

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="the hidden attribute is a Windows file attribute")
LOCAL = ("127.0.0.1", 50000)


def test_hidden_and_system_attributes_count_as_hidden() -> None:
    page = (_record("a", winfs.FILE_ATTRIBUTE_HIDDEN, 1, 1) + _record("b", winfs.FILE_ATTRIBUTE_SYSTEM, 1, 1)
            + _record("c", 0x20, 1, 1, last=True))
    hidden, system, plain = winfs.parse_records(page)
    assert winfs.is_hidden(hidden.attrs) and winfs.is_hidden(system.attrs)
    assert not winfs.is_hidden(plain.attrs)


def test_an_item_inside_a_hidden_folder_is_hidden_with_it() -> None:
    assert hidden_state(winfs.FILE_ATTRIBUTE_HIDDEN, VISIBLE) == HIDDEN_ITSELF
    assert hidden_state(0x20, HIDDEN_ITSELF) == HIDDEN_INSIDE
    assert hidden_state(0x20, HIDDEN_INSIDE) == HIDDEN_INSIDE
    assert hidden_state(0x20, VISIBLE) == VISIBLE


@windows_only
def test_the_directory_listing_reads_the_hidden_attribute(tmp_path: Path) -> None:
    add_hidden(tmp_path)
    by_name = {e.name: e for e in winfs.list_dir(str(tmp_path))}
    assert winfs.is_hidden(by_name[HIDDEN_FOLDER].attrs) and by_name[HIDDEN_FOLDER].is_dir
    assert winfs.is_hidden(by_name[HIDDEN_FILE].attrs)
    assert not winfs.is_hidden(next(winfs.list_dir(str(tmp_path / HIDDEN_FOLDER))).attrs)


@pytest.fixture()
def hidden_index(tmp_path: Path) -> Index:
    if sys.platform != "win32":
        pytest.skip("the hidden attribute is a Windows file attribute")
    root = add_hidden(build_tree(tmp_path / "Invented root"))
    path = tmp_path / "index.sqlite3"
    scan_to_index(str(root), path, root_name="Invented root")
    return Index(open_index(path))


def test_the_index_marks_hidden_items_and_still_counts_them(hidden_index: Index) -> None:
    rows = dict(hidden_index.conn.execute("SELECT name, hidden FROM folders").fetchall())
    files = dict(hidden_index.conn.execute("SELECT name, hidden FROM files").fetchall())
    assert rows[HIDDEN_FOLDER] == HIDDEN_ITSELF and rows["Photos"] == VISIBLE
    assert files[HIDDEN_FILE] == HIDDEN_ITSELF and files[HIDDEN_FOLDER_FILE] == HIDDEN_INSIDE
    assert files["notes.txt"] == VISIBLE
    assert hidden_index.totals(0, None)["size"] == TOTAL_BYTES + HIDDEN_BYTES


def test_explore_queries_leave_hidden_items_out_unless_asked(hidden_index: Index) -> None:
    ix = hidden_index
    assert HIDDEN_FOLDER not in [k["name"] for k in ix.children(0, "size", None, False)["children"]]
    assert HIDDEN_FOLDER in [k["name"] for k in ix.children(0, "size", None, True)["children"]]
    hiding = ix.contents(0, "size", None, False)
    names = [it["name"] for it in hiding["items"]]
    assert HIDDEN_FOLDER not in names and HIDDEN_FILE not in names and "notes.txt" in names
    assert hiding["hidden_items"] == 2
    showing = ix.contents(0, "size", None, True)
    assert showing["total_items"] == hiding["total_items"] + 2
    marks = {it["name"]: it["hidden"] for it in showing["items"]}
    assert marks[HIDDEN_FOLDER] == HIDDEN_ITSELF and marks[HIDDEN_FILE] == HIDDEN_ITSELF
    nested = [f["name"] for f in ix.largest(0, 100, "size", None, False)]
    assert HIDDEN_FOLDER_FILE not in nested and HIDDEN_FILE not in nested and "notes.txt" in nested
    assert HIDDEN_FOLDER_FILE in [f["name"] for f in ix.largest(0, 100, "size", None, True)]
    assert ix.contents(0, "size", "bin", False)["hidden_items"] == 1
    assert ix.children(0, "size", "bin", False)["children"] == []


def test_an_index_from_before_the_hidden_column_shows_everything_and_says_so(hidden_index: Index, tmp_path: Path) -> None:
    old = tmp_path / "old.sqlite3"
    hidden_index.conn.close()
    shutil.copy(tmp_path / "index.sqlite3", old)
    with sqlite3.connect(str(old)) as conn:
        conn.execute("ALTER TABLE folders DROP COLUMN hidden")
        conn.execute("ALTER TABLE files DROP COLUMN hidden")
    ix = Index(open_index(old))
    assert not ix.knows_hidden()
    body = ix.contents(0, "size", None, False)
    assert HIDDEN_FOLDER in [it["name"] for it in body["items"]] and body["hidden_items"] is None


@pytest.fixture()
def hidden_root(isolated: Path) -> str:
    if sys.platform != "win32":
        pytest.skip("the hidden attribute is a Windows file attribute")
    write_config(isolated, roots=[str(add_hidden(build_tree(isolated / "Invented root")))])
    with TestClient(app, client=LOCAL) as c:
        assert c.post("/api/scan", json={}).status_code == 202
        for _ in range(100):
            if not c.get("/api/scan").json()["scanning"]:
                break
            time.sleep(0.05)
        return c.get("/api/roots").json()["roots"][0]["key"]


def test_the_switch_is_saved_and_explore_follows_it(hidden_root: str) -> None:
    with TestClient(app, client=LOCAL) as c:
        body = c.get("/api/folder", params={"root": hidden_root}).json()
        assert body["hidden"] == {"shown": False, "known": True} and body["hidden_items"] == 2
        assert HIDDEN_FOLDER not in [it["name"] for it in body["items"]]
        tree = c.get("/api/tree", params={"root": hidden_root}).json()
        assert HIDDEN_FOLDER not in [k["name"] for k in tree["children"]]
        assert c.post("/api/config", json={"explore": {"show_hidden": "yes"}}).status_code == 400
        assert c.post("/api/config", json={"explore": {"show_hidden": True, "other": 1}}).status_code == 400
        saved = c.post("/api/config", json={"explore": {"show_hidden": True}})
        assert saved.status_code == 200 and saved.json()["config"]["explore"] == {"show_hidden": True}
        body = c.get("/api/folder", params={"root": hidden_root}).json()
        assert body["hidden"]["shown"] is True
        assert {it["name"]: it["hidden"] for it in body["items"]}[HIDDEN_FOLDER] == HIDDEN_ITSELF
