"""Folder search over a scanned invented tree: every word in the path, folders of people, skip-N display."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from src.folder_search import display_path, parse_terms, search
from src.index import Index, open_index
from src.roots import Root
from src.scanner import scan_to_index

# Invented folders: a client folder holding one folder per person, and an ordinary project tree.
FOLDERS = (
    "Clients/Northwind/ana@example.com/2025",
    "Clients/Northwind/leo@example.org",
    "Clients/Northwind/Contracts",
    "Clients/Kestrel/Design/Budget",
    "Projects/Alpha/2025/Budget",
    "Projects/Alpha/Notes",
    "Projects/cl@ve",
)


def scanned(tmp_path: Path) -> tuple[Root, Index]:
    base = tmp_path / "Invented drive"
    for rel in FOLDERS:
        (base / rel).mkdir(parents=True)
    (base / "Projects/Alpha/2025/Budget/model.xlsx").write_bytes(b"x" * 500)
    index_path = tmp_path / "root.sqlite3"
    scan_to_index(str(base), index_path, root_name="Invented drive")
    return Root("inv", "Invented drive", str(base), "local"), Index(open_index(index_path))


def names(result: dict) -> list[str]:
    return [r["name"] for r in result["results"]]


def test_every_word_must_appear_somewhere_in_the_path(tmp_path: Path) -> None:
    root, ix = scanned(tmp_path)
    result = search([(root, ix)], "alpha budget", prune=True, skip=0)
    assert names(result) == ["Budget"]
    hit = result["results"][0]
    assert hit["path"].replace("\\", "/").endswith("Projects/Alpha/2025/Budget") and hit["size"] == 500
    assert names(search([(root, ix)], "BUDGET;design", prune=True, skip=0)) == ["Budget"]
    assert search([(root, ix)], "nothing-like-this", prune=True, skip=0)["total"] == 0
    assert search([(root, ix)], "   ", prune=True, skip=0)["results"] == []


def test_a_folder_of_people_folds_its_matches_into_one_result(tmp_path: Path) -> None:
    root, ix = scanned(tmp_path)
    pruned = search([(root, ix)], "northwind", prune=True, skip=0)
    assert names(pruned) == ["Northwind"] and pruned["results"][0]["people"] == 2
    assert names(search([(root, ix)], "2025 example", prune=True, skip=0)) == ["Northwind"]
    full = search([(root, ix)], "northwind", prune=False, skip=0)
    assert set(names(full)) == {"Northwind", "ana@example.com", "leo@example.org", "Contracts", "2025"}
    # "cl@ve" has no dotted domain, so it is an ordinary folder, not a person.
    assert names(search([(root, ix)], "cl@ve", prune=True, skip=0)) == ["cl@ve"]


def test_name_matches_rank_above_ancestry_matches(tmp_path: Path) -> None:
    root, ix = scanned(tmp_path)
    assert names(search([(root, ix)], "alpha", prune=True, skip=0))[0] == "Alpha"


def test_skip_hides_leading_components_for_display_only(tmp_path: Path) -> None:
    root, ix = scanned(tmp_path)
    hit = search([(root, ix)], "kestrel design budget", prune=True, skip=3)["results"][0]
    assert hit["display"].endswith("Kestrel\\Design\\Budget") and len(hit["path"]) > len(hit["display"])
    assert display_path("C:\\One\\Two", 9) == "Two"
    assert display_path("C:\\One\\Two", 0) == "C:\\One\\Two"
    assert parse_terms("A\\b; c") == ["a/b", "c"]


def test_an_index_from_before_folder_search_asks_for_a_rescan(tmp_path: Path) -> None:
    root, ix = scanned(tmp_path)
    ix.conn.close()
    old = tmp_path / "old.sqlite3"
    shutil.copy(tmp_path / "root.sqlite3", old)
    conn = sqlite3.connect(str(old))
    conn.execute("DROP TABLE folder_paths")
    conn.commit()
    conn.close()
    result = search([(root, Index(open_index(old)))], "alpha", prune=True, skip=0)
    assert result["results"] == [] and result["needs_rescan"] == ["Invented drive"]


def test_find_folder_and_file_by_path(tmp_path: Path) -> None:
    _, ix = scanned(tmp_path)
    fid = ix.find_folder(["projects", "ALPHA", "2025", "Budget"])
    assert fid is not None and ix.folder(fid)["name"] == "Budget"
    assert ix.find_file(fid, "MODEL.xlsx")["size"] == 500
    assert ix.find_folder(["Projects", "Missing"]) is None
