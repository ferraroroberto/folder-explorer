"""Folder search lists a folder once when two roots reach it, over scanned invented trees."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from src import roots as roots_module
from src.folder_search import search
from src.index import Index, open_index
from src.roots import Root, parts_within
from src.scanner import scan_to_index

Entry = tuple[Root, Index]


def build(base: Path) -> Path:
    """An invented library: one "Budget" folder holding one file."""
    (base / "Alpha" / "Budget").mkdir(parents=True)
    (base / "Alpha" / "Budget" / "model.xlsx").write_bytes(b"x" * 100)
    return base


def entry(tmp_path: Path, key: str, path: Path, kind: str = "local") -> Entry:
    index_path = tmp_path / (key + ".sqlite3")
    scan_to_index(str(path), index_path, root_name=key)
    return Root(key, key, str(path), kind), Index(open_index(index_path))


def junction(link: Path, target: Path) -> Path:
    if sys.platform != "win32":
        pytest.skip("junctions are Windows only")
    import _winapi

    _winapi.CreateJunction(str(target), str(link))
    return link


def budgets(entries: list[Entry], **options: Any) -> list[str]:
    """The root of every "Budget" row, after checking the total counts exactly what is listed."""
    found = search(entries, "alpha budget", prune=False, skip=0, **options)
    assert found["total"] == len(found["results"])
    return sorted(r["root"] for r in found["results"] if r["name"] == "Budget")


def test_a_root_reached_through_a_junction_is_listed_once_and_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    drive = tmp_path / "drive"
    build(drive / "Teams" / "Library")
    above = junction(tmp_path / "link", drive / "Teams")
    try:
        outer = entry(tmp_path, "drive", drive)
        with caplog.at_level(logging.INFO, logger="src.folder_search"):
            assert budgets([outer, entry(tmp_path, "linked", above / "Library")]) == ["drive"]
        assert [r.getMessage() for r in caplog.records] == [
            "ℹ️ folder search: roots reaching the same folders, listed once: linked left out in favour of drive"]
        assert str(tmp_path) not in caplog.text
        # The synced root's row wins over configured order.
        assert budgets([outer, entry(tmp_path, "cloud", above / "Library", "synced")], onedrive_prevails=True) == ["cloud"]
    finally:
        os.rmdir(above)


def test_the_first_configured_root_wins_and_a_root_that_is_a_junction_hides_nothing(tmp_path: Path) -> None:
    drive = tmp_path / "drive"
    library = build(drive / "Library")
    inner, outer = entry(tmp_path, "inner", library), entry(tmp_path, "outer", drive)
    assert budgets([outer, inner]) == ["outer"] and budgets([inner, outer]) == ["inner"]
    link = junction(tmp_path / "link", library)
    try:
        # The scan opens a junction itself, not its target, so this root's index is empty.
        assert budgets([entry(tmp_path, "empty", link), outer]) == ["outer"]
    finally:
        os.rmdir(link)


def test_two_synced_roots_onto_one_library_are_listed_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two spellings no path resolution relates, faked: both report the same volume serial and file ID."""
    first, second = build(tmp_path / "Library"), build(tmp_path / "Shortcut to library")
    entries = [entry(tmp_path, "od", first, "synced"), entry(tmp_path, "od-2", second, "synced")]
    assert budgets(entries) == ["od", "od-2"]
    real = roots_module.folder_identity
    monkeypatch.setattr(roots_module, "folder_identity", lambda path: real(str(path).replace(second.name, first.name)))
    assert parts_within(str(second), str(first)) == []
    assert budgets(entries) == ["od"]


def test_really_distinct_folders_with_the_same_name_and_path_both_stay(tmp_path: Path) -> None:
    one = build(tmp_path / "one")
    shutil.copytree(one, tmp_path / "two")
    assert budgets([entry(tmp_path, "one", one), entry(tmp_path, "two", tmp_path / "two")]) == ["one", "two"]


def test_ids_are_never_compared_across_two_drive_letters(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cloned disk repeats the volume serial and the file IDs, and is still another folder."""
    if sys.platform != "win32":
        pytest.skip("drive letters are Windows only")
    monkeypatch.setattr(roots_module, "folder_identity", lambda path: (1, 5))
    assert parts_within("Y:\\Photos", "Z:\\Photos") is None


def test_within_a_parent_folder_still_lists_the_library_under_it(tmp_path: Path) -> None:
    drive = tmp_path / "drive"
    library = build(drive / "Teams" / "Library")
    entries = [entry(tmp_path, "drive", drive), entry(tmp_path, "od", library, "synced")]
    # The library root does not hold its parent, so only the drive's copy can answer for it.
    assert budgets(entries, onedrive_prevails=True, within=[str(drive / "Teams")]) == ["drive"]
    assert budgets(entries, onedrive_prevails=True, within=[str(library)]) == ["od"]
