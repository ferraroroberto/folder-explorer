"""A drive root typed in any common shape resolves to one clean path, so a folder or file below it opens.

The drive letters are invented and need not exist: only the built path string is
checked, and the open route's disk check and launch are replaced.
"""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.webapp.routers import search as search_routes
from app.webapp.server import app
from src import desktop, folder_search
from src.index import Index, open_index
from src.roots import resolve_roots
from src.scanner import scan_to_index
from tests.conftest import write_config
from tests.fixture_tree import build_tree

LOCAL = ("127.0.0.1", 50000)
DRIVE_SHAPES = ["Z:\\", "Z:", "Z:/", "Z:\\\\"]


@pytest.mark.parametrize("typed", DRIVE_SHAPES)
def test_a_drive_root_resolves_to_one_clean_path_and_name(typed: str) -> None:
    (root,) = resolve_roots([typed])
    assert (root.key, root.name, root.path, root.kind) == ("z", "Z:", "Z:\\", "local")


@pytest.mark.parametrize("typed", DRIVE_SHAPES)
def test_folder_and_file_paths_below_a_drive_root_have_single_separators(tmp_path: Path, typed: str) -> None:
    build_tree(tmp_path / "tree")
    index_path = tmp_path / "idx.sqlite3"
    scan_to_index(str(tmp_path / "tree"), index_path, root_name="Z:")
    index = Index(open_index(index_path))
    (root,) = resolve_roots([typed])
    garden = next(r["id"] for r in index.conn.execute("SELECT id FROM folders WHERE name = 'garden'"))
    folder = folder_search.item_path(root, index, "folder", int(garden))
    assert folder == "Z:\\Photos\\2025\\garden"
    file_id = int(index.conn.execute("SELECT rowid FROM files WHERE name = 'clip-01.mov'").fetchone()[0])
    assert folder_search.item_path(root, index, "file", file_id) == "Z:\\Photos\\2025\\garden\\clip-01.mov"
    hit = folder_search.search([(root, index)], "2025 garden", prune=False, skip=0)["results"][0]
    assert (hit["path"], hit["root_name"]) == ("Z:\\Photos\\2025\\garden", "Z:")


@pytest.mark.parametrize("typed", DRIVE_SHAPES)
def test_open_folder_checks_and_launches_the_clean_path(isolated: Path, monkeypatch: pytest.MonkeyPatch, typed: str) -> None:
    drive = build_tree(isolated / "Invented drive")
    write_config(isolated, roots=[str(drive)])
    checked: list[str] = []
    launched: list[str] = []
    monkeypatch.setattr(search_routes.os.path, "isdir", lambda p: checked.append(str(p)) or True)
    monkeypatch.setattr(search_routes, "open_in_explorer", lambda path, *_: launched.append(path))
    monkeypatch.setattr(desktop, "_shell", lambda path, verb: launched.append(path))
    with TestClient(app, client=LOCAL) as c:
        assert c.post("/api/scan", json={}).status_code == 202
        for _ in range(100):
            if not c.get("/api/scan").json()["scanning"]:
                break
            time.sleep(0.05)
        hit = c.get("/api/search/folders", params={"q": "2025 garden"}).json()["results"][0]
        (real,) = search_routes._roots()
        (typed_root,) = resolve_roots([typed])
        monkeypatch.setattr(search_routes, "_roots", lambda: [dataclasses.replace(typed_root, key=real.key)])
        checked.clear()
        assert c.post("/api/folders/open", json={"root": hit["root"], "id": hit["id"]}).json() == {"status": "opened"}
    assert [p for p in checked if p.startswith("Z:")] == launched == ["Z:\\Photos\\2025\\garden"]
