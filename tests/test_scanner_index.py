"""The scanner writes a correct index, and every query reads it back right."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from src import winfs
from src.index import Index, IndexPool, open_index
from src.scanner import extension, scan_to_index
from tests.fixture_tree import FILES, TOTAL_BYTES, build_tree


@pytest.fixture()
def indexed(tmp_path: Path) -> Index:
    root = build_tree(tmp_path / "Invented root")
    scan_to_index(str(root), tmp_path / "idx.sqlite3", root_name="Invented root")
    return Index(open_index(tmp_path / "idx.sqlite3"))


def _child(ix: Index, parent: int, name: str) -> dict:
    return next(c for c in ix.children(parent, "size", None)["children"] if c["name"] == name)


def test_extension_rules() -> None:
    assert extension("a.JPG") == "jpg"
    assert extension("archive.tar.gz") == "gz"
    assert extension("README") == ""
    assert extension(".gitignore") == ""


def test_totals_match_the_files_written(indexed: Index) -> None:
    t = indexed.totals(0, None)
    assert t["size"] == TOTAL_BYTES
    assert t["files"] == len(FILES)
    assert t["alloc"] >= 0
    assert indexed.folder(0)["name"] == "Invented root"


def test_children_are_heaviest_first_with_shares(indexed: Index) -> None:
    kids = indexed.children(0, "size", None)["children"]
    sizes = [k["size"] for k in kids]
    assert sizes == sorted(sizes, reverse=True)
    assert kids[0]["name"] in ("Photos", "Videos")
    empty = next(k for k in kids if k["name"] == "Empty folder")
    assert empty["size"] == 0 and not empty["has_children"]


def test_subtree_interval_covers_nested_files(indexed: Index) -> None:
    photos = _child(indexed, 0, "Photos")
    expected = sum(size for rel, size in FILES if rel.startswith("Photos/"))
    assert indexed.totals(photos["id"], None)["size"] == expected


def test_type_filter_narrows_tree_and_totals(indexed: Index) -> None:
    jpg = sum(size for rel, size in FILES if rel.endswith(".jpg"))
    assert indexed.totals(0, "jpg")["size"] == jpg
    kids = indexed.children(0, "size", "jpg")["children"]
    assert [k["name"] for k in kids] == ["Photos"]
    assert indexed.totals(0, "")["files"] == 1  # README, no extension


def test_largest_files_carry_their_path(indexed: Index) -> None:
    top = indexed.largest(0, 3, "size", None)
    assert [f["name"] for f in top] == ["river-01.mp4", "river-02.mp4", "clip-01.mov"]
    assert top[0]["path"] == ["Videos", "drone-footage"]


def test_contents_mixes_folders_and_files_biggest_first(indexed: Index) -> None:
    body = indexed.contents(0, "size", None)
    kinds = [(i["kind"], i["name"]) for i in body["items"]]
    assert ("file", "notes.txt") in kinds and ("folder", "Photos") in kinds
    sizes = [i["size"] for i in body["items"]]
    assert sizes == sorted(sizes, reverse=True)


def test_percentile_groups_by_top_level_folder(indexed: Index) -> None:
    r = indexed.percentile(0, 10, "size", None)
    assert r["count"] == 2  # ceil(18 * 10%) = 2
    assert r["bytes"] == 2_400_000 + 2_100_000
    assert r["where"] == [{"name": "Videos", "files": 2, "bytes": 4_500_000}]


def test_types_breakdown_sums_to_the_whole(indexed: Index) -> None:
    body = indexed.types(0, "size")
    assert body["whole"] == TOTAL_BYTES
    assert sum(t["files"] for t in body["types"]) == len(FILES)
    assert body["types"][0]["ext"] == "mp4"


def test_disk_basis_orders_by_allocation(indexed: Index) -> None:
    top = indexed.largest(0, 18, "disk", None)
    allocs = [f["disk"] for f in top]
    assert allocs == sorted(allocs, reverse=True)


def test_unknown_folder_raises_key_error(indexed: Index) -> None:
    with pytest.raises(KeyError):
        indexed.totals(10_000, None)


def _fake_lister(tree: dict) -> "winfs.Lister":
    def lister(path: str) -> Iterator[winfs.Entry]:
        node = tree
        for part in Path(path).parts[2:]:
            node = node[part]
        if node == "denied":
            raise PermissionError("access denied")
        for name, value in node.items():
            if isinstance(value, tuple):
                size, alloc, attrs = value
                yield winfs.Entry(name, False, size, alloc, attrs, 0, 0)
            elif value == "junction":
                yield winfs.Entry(name, True, 0, 0, winfs.FILE_ATTRIBUTE_DIRECTORY | winfs.FILE_ATTRIBUTE_REPARSE_POINT, 0, 0xA0000003)
            else:
                yield winfs.Entry(name, True, 0, 0, winfs.FILE_ATTRIBUTE_DIRECTORY, 0, 0)
    return lister


def test_online_only_links_and_errors_are_counted_not_followed(tmp_path: Path) -> None:
    online = 0x20 | winfs.FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
    tree = {
        "cloud": {"report.docx": (580_946, 0, online), "pinned.pdf": (1_000, 4_096, winfs.FILE_ATTRIBUTE_PINNED)},
        "elsewhere": "junction",
        "locked": "denied",
        "local.txt": (10, 16, 0x20),
    }
    root = str(Path(tmp_path.anchor) / "fake")
    counters = scan_to_index(root, tmp_path / "f.sqlite3", lister=_fake_lister(tree))
    assert (counters.files, counters.skipped_links, counters.errors) == (3, 1, 1)
    assert (counters.online_files, counters.online_size) == (1, 580_946)
    ix = Index(open_index(tmp_path / "f.sqlite3"))
    t = ix.totals(0, None)
    assert (t["size"], t["alloc"]) == (580_946 + 1_000 + 10, 4_096 + 16)
    assert ix.largest(0, 1, "size", None)[0]["online"] is True


def test_unlistable_root_raises_and_leaves_no_index(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        scan_to_index(str(tmp_path / "missing"), tmp_path / "m.sqlite3")
    assert not (tmp_path / "m.sqlite3").exists()
    assert not (tmp_path / "m.sqlite3.part").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are a Windows feature")
def test_real_junction_is_skipped(tmp_path: Path) -> None:
    import _winapi

    root = build_tree(tmp_path / "r")
    _winapi.CreateJunction(str(root / "Photos"), str(root / "Photos again"))
    counters = scan_to_index(str(root), tmp_path / "j.sqlite3")
    assert counters.files == len(FILES)
    assert counters.skipped_links == 1


def test_pool_reuses_and_retires_connections(tmp_path: Path) -> None:
    root = build_tree(tmp_path / "r")
    path = tmp_path / "p.sqlite3"
    scan_to_index(str(root), path)
    pool = IndexPool()
    first, _ = pool.get(path)
    again, _ = pool.get(path)
    assert first is again
    pool.retire(set())
    path.unlink()  # closed, so Windows lets it go
    assert not path.exists()
