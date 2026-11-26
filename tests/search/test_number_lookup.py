"""A number like ``017-052`` is looked up in the folder words, never by a pass over every stored path, with the same hits."""

from __future__ import annotations

from pathlib import Path

import pytest

from src import folder_search
from src.folder_search import search
from src.index import Index, open_index
from src.roots import Root
from src.scanner import scan_to_index

# Invented folders: the number as a name, inside longer runs of digits, spaced, reversed, and in a date.
FOLDERS = (
    "Projects/017-052 Kestrel/Notes",
    "Projects/9017-0520 Alpha",
    "Projects/017 052 Spaced",
    "Projects/052-017 Reversed",
    "Projects/017_052 Underscored",
    "Archive/Plans 2025-01-15",
    "Archive/Plans 2025-15-01",
    "Archive/C# samples",
)
ROOT_NAME = "Invented 31-07 drive"


def scanned(tmp_path: Path) -> tuple[Root, Index]:
    base = tmp_path / ROOT_NAME
    for rel in FOLDERS:
        (base / rel).mkdir(parents=True)
    index_path = tmp_path / "root.sqlite3"
    scan_to_index(str(base), index_path, root_name=ROOT_NAME)
    return Root("inv", ROOT_NAME, str(base), "local"), Index(open_index(index_path))


def found(root: Root, ix: Index, query: str) -> list[tuple[str, str]]:
    return [(r["name"], r["tier"]) for r in search([(root, ix)], query, prune=True, skip=0)["results"]]


def no_path_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail on either way folder search reads stored paths in bulk."""
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the number lookup read the stored paths instead of the folder words")

    monkeypatch.setattr(Index, "match_folders", refuse)
    monkeypatch.setattr(Index, "paths_in", refuse)


def test_a_number_lookup_reads_the_folder_words_not_every_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, ix = scanned(tmp_path)
    no_path_reads(monkeypatch)
    # The number's own folder and what is below it, and a longer run of digits holding it, as the path scan matched.
    assert sorted(found(root, ix, "017-052")) == [
        ("017-052 Kestrel", "exact"), ("9017-0520 Alpha", "exact"), ("Notes", "exact")]
    assert found(root, ix, "017_052") == [("017_052 Underscored", "exact")]
    assert found(root, ix, "2025-01-15") == [("Plans 2025-01-15", "exact")]
    assert found(root, ix, "017-052 notes")[0] == ("Notes", "exact")


def test_a_number_no_folder_holds_goes_straight_to_the_closest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, ix = scanned(tmp_path)
    no_path_reads(monkeypatch)
    assert {tier for _, tier in found(root, ix, "017-999")} <= {"closest"}


def test_a_number_in_the_roots_own_path_matches_every_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, ix = scanned(tmp_path)
    no_path_reads(monkeypatch)
    result = search([(root, ix)], "31-07", prune=True, skip=0)
    assert result["total"] == ix.conn.execute("SELECT COUNT(*) FROM folders").fetchone()[0]


@pytest.mark.parametrize("word", ["017-052", "052-017", "17-05", "-052", "2025-01", "01-15", "017.052", "c#", "017-"])
def test_the_hits_are_the_ones_a_scan_of_every_path_finds(tmp_path: Path, word: str) -> None:
    root, ix = scanned(tmp_path)
    scan, _ = ix.match_folders([[word]], 5000)
    hits = search([(root, ix)], word, prune=True, skip=0)["results"]
    assert sorted(h["id"] for h in hits if h["tier"] == "exact") == sorted(fid for fid, _ in scan)


def test_a_part_naming_more_folders_than_a_whole_word_may_is_still_looked_up(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, ix = scanned(tmp_path)
    no_path_reads(monkeypatch)
    # "052" starts a word in five folder names, one of which holds the number: only that one counts against MAX_NAMED.
    monkeypatch.setattr(folder_search, "MAX_NAMED", 1)
    assert found(root, ix, "017_052") == [("017_052 Underscored", "exact")]


def test_a_part_too_common_to_look_up_still_finds_the_number(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, ix = scanned(tmp_path)
    monkeypatch.setattr(folder_search, "JOINED_CANDIDATES", 1)
    assert ("017-052 Kestrel", "exact") in found(root, ix, "017-052")
