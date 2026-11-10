"""The archive window's "Find a folder": the same answers as Quick search, the email folders first, and browsing subfolders.

Over the invented scanned drive Quick search's tests use; the window's source reads this
process's index pool, the Quick search client reads the API, and both must agree.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.webapp.state import (
    LocalFolders,
    email_folder_hits,
    folder_children,
    folder_hits,
)
from src import config
from src.archive_window.finder import NO_SUBFOLDERS, ROWS_SHOWN, Finder
from src.search_window.model import FOLDERS, Status, fetch
from tests.test_search_window import _client, api  # noqa: F401 - the scanned drive fixture, shared

QUERIES = ("harbour", "photos 2025", "invoices;documents", "BUDGET", "northwind", "2025")


def _finder() -> Finder:
    cfg = config.load()
    return Finder(LocalFolders(cfg), list(cfg["email"]["roots"]))


def test_the_window_finds_what_quick_search_finds_for_the_same_words(api: TestClient) -> None:  # noqa: F811
    for options in ({"skip_levels": 0, "prune_people": True, "onedrive_prevails": True},
                    {"skip_levels": 2, "prune_people": False, "onedrive_prevails": False}):
        config.save({"folder_search": options})
        for query in QUERIES:
            assert LocalFolders(config.load()).folders(query) == api.get("/api/search/folders", params={"q": query}).json()
            finder = _finder()
            finder.search(query)
            quick = fetch(_client(api), FOLDERS, query)
            by_id = {r.target["id"]: r for r in quick.rows}
            assert sorted(f.row.target["id"] for f in finder.found()) == sorted(by_id), query
            assert all(f.row == by_id[f.row.target["id"]] for f in finder.found()), query


def test_folders_outside_the_email_folders_follow_and_cannot_take_the_mail(api: TestClient) -> None:  # noqa: F811
    finder = _finder()
    finder.search("2025")
    found = [(f.name, f.fileable) for f in finder.found()]
    assert found[0] == ("Northwind", True) and ("2025", False) in found and not any(ok for _, ok in found[1:])
    outside = next(f for f in finder.found() if f.name == "2025")
    assert outside.row.path.endswith("Photos\\2025")


def test_stepping_into_a_folder_lists_its_subfolders_and_back_returns_to_the_search(api: TestClient) -> None:  # noqa: F811
    finder = _finder()
    finder.search("northwind")
    top = finder.found()
    assert finder.enter(0) and finder.crumbs() == ["Northwind"]
    people = finder.found()
    assert {f.name for f in people} == {"ana@example.com", "leo@example.org"} and all(f.fileable for f in people)
    assert all(f.row.path.startswith(top[0].row.path + "\\") for f in people)
    ana = next(i for i, f in enumerate(people) if f.name == "ana@example.com")
    assert finder.enter(ana) and finder.crumbs() == ["Northwind", "ana@example.com"]
    assert [f.name for f in finder.found()] == ["2025"]
    assert finder.enter(0) and finder.found() == () and finder.message() == NO_SUBFOLDERS
    assert finder.back() and finder.back() and finder.back() and not finder.back()
    assert finder.found() == top and not finder.browsing


def test_a_new_search_leaves_the_folder_stepped_into(api: TestClient) -> None:  # noqa: F811
    finder = _finder()
    finder.search("northwind")
    finder.enter(0)
    finder.search("harbour")
    assert not finder.browsing and [f.name for f in finder.found()] == ["harbour-trip"]


def test_a_folder_no_scan_holds_has_no_subfolders(api: TestClient, tmp_path: Path) -> None:  # noqa: F811
    assert folder_children(config.load(), str(tmp_path / "Not scanned")) == []


def test_a_failed_search_says_so_in_its_own_words() -> None:
    class Broken:
        def folders(self, query: str) -> dict:
            raise OSError("the index is locked")

        def children(self, path: str) -> list:
            raise OSError("the index is locked")

    finder = Finder(Broken(), [])
    finder.search("anything")
    assert finder.answer is not None and finder.answer.status is Status.ERROR and finder.failed()
    assert "the index is locked" in finder.message() and finder.found() == ()


def test_only_the_first_rows_are_listed_and_the_count_says_so() -> None:
    hits = [{"root": "r", "id": i, "name": "Folder %02d" % i, "path": "C:\\Mail\\Folder %02d" % i, "display": "", "files": 0, "size": 0}
            for i in range(ROWS_SHOWN + 3)]

    class Many:
        def folders(self, query: str) -> dict:
            return {"results": hits, "total": len(hits)}

        def children(self, path: str) -> list:
            return []

    finder = Finder(Many(), ["C:\\Mail"])
    finder.search("folder")
    assert len(finder.shown()) == ROWS_SHOWN and finder.count_line() == "11 folders, first 8 shown"
    finder.move(99)
    assert finder.selected == ROWS_SHOWN - 1


def test_the_sheets_box_offers_the_same_search_kept_to_the_email_folders(api: TestClient) -> None:  # noqa: F811
    cfg = config.load()
    mail = cfg["email"]["roots"][0]
    hits = email_folder_hits(cfg, "2025", 8)
    assert [h["path"] for h in hits] == [r["path"] for r in folder_hits(cfg, "2025")["results"] if r["path"].startswith(mail)][:8]
    assert [h["name"] for h in hits] == ["Northwind"], "a folder of people folds as Quick search folds it"
