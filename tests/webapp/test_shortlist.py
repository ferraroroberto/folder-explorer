"""The cleanup shortlist: marked by id, kept by names across scans, never touching a file."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.webapp.server import app
from src import store
from tests.conftest import write_config
from tests.fixture_tree import build_tree

LOCAL = ("127.0.0.1", 50000)


def _scan(c: TestClient) -> dict:
    assert c.post("/api/scan", json={}).status_code == 202
    for _ in range(200):
        if not c.get("/api/scan").json()["scanning"]:
            break
        time.sleep(0.05)
    return c.get("/api/roots").json()["roots"][0]


@pytest.fixture()
def tree(isolated: Path) -> Path:
    root = build_tree(isolated / "Invented root")
    write_config(isolated, roots=[str(root)])
    return root


def _top(c: TestClient, root: dict) -> dict:
    return c.get("/api/top", params={"root": root["key"], "n": 1}).json()["files"][0]


def test_a_marked_file_is_listed_with_its_place_in_the_current_scan(tree: Path) -> None:
    with TestClient(app, client=LOCAL) as c:
        root = _scan(c)
        top = _top(c, root)
        body = c.post("/api/shortlist", json={"root": root["key"], "scan_id": root["current"]["id"], "id": top["id"]}).json()
        [item] = body["items"]
        assert item["name"] == "river-01.mp4" and item["folder"] == ["Videos", "drone-footage"]
        assert item["file"]["id"] == top["id"] and item["file"]["size"] == 2_400_000
        assert body["size"] == 2_400_000
        folder = c.get("/api/tree", params={"root": root["key"], "folder": item["folder_id"]}).json()
        assert folder["folder"]["name"] == "drone-footage"
        # Marking it again keeps one entry.
        again = c.post("/api/shortlist", json={"root": root["key"], "scan_id": root["current"]["id"], "id": top["id"]}).json()
        assert len(again["items"]) == 1


def test_the_mark_outlives_a_rescan_and_says_when_the_file_is_gone(tree: Path) -> None:
    with TestClient(app, client=LOCAL) as c:
        root = _scan(c)
        top = _top(c, root)
        c.post("/api/shortlist", json={"root": root["key"], "scan_id": root["current"]["id"], "id": top["id"]})
        (tree / "Videos" / "drone-footage" / "river-01.mp4").unlink()
        rescanned = _scan(c)
        assert rescanned["current"]["id"] != root["current"]["id"]
        [item] = c.get("/api/shortlist").json()["items"]
        assert item["file"] is None and item["folder_id"] is not None
        assert item["scan_id"] == rescanned["current"]["id"] and item["size"] == 2_400_000
        assert c.get("/api/shortlist").json()["size"] == 0


def test_a_stale_scan_or_unknown_file_is_refused(tree: Path) -> None:
    with TestClient(app, client=LOCAL) as c:
        root = _scan(c)
        top = _top(c, root)
        stale = c.post("/api/shortlist", json={"root": root["key"], "scan_id": root["current"]["id"] - 1, "id": top["id"]})
        assert stale.status_code == 409
        assert c.post("/api/shortlist", json={"root": root["key"], "scan_id": root["current"]["id"], "id": 999_999}).status_code == 404
        assert c.post("/api/shortlist", json={"root": "unknown", "scan_id": 1, "id": top["id"]}).status_code == 404
        assert c.post("/api/shortlist", json={"root": root["key"], "scan_id": root["current"]["id"], "id": "1"}).status_code == 404
        assert c.get("/api/shortlist").json()["items"] == []


def test_remove_takes_one_entry_off(tree: Path) -> None:
    with TestClient(app, client=LOCAL) as c:
        root = _scan(c)
        top = _top(c, root)
        [item] = c.post("/api/shortlist", json={"root": root["key"], "scan_id": root["current"]["id"], "id": top["id"]}).json()["items"]
        assert c.delete("/api/shortlist/%d" % item["id"]).json()["items"] == []
        assert c.delete("/api/shortlist/%d" % item["id"]).status_code == 404


def test_the_list_is_capped(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "SHORTLIST_MAX", 2)
    store.shortlist_add("r", ["a.bin"], 1, 1)
    store.shortlist_add("r", ["b.bin"], 1, 1)
    store.shortlist_add("r", ["a.bin"], 1, 1)  # already there: no error
    with pytest.raises(ValueError):
        store.shortlist_add("r", ["c.bin"], 1, 1)
    assert [e["rel"] for e in store.shortlist()] == [["b.bin"], ["a.bin"]]
