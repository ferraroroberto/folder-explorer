"""The archive window's "Find a folder": the same answers as Quick search, the email folders first, and browsing subfolders.

Over the invented scanned drive Quick search's tests use; the window's source reads this
process's index pool, the Quick search client reads the API, and both must agree.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.webapp.server import app
from app.webapp.state import (
    SHEET_FOLDER_ROWS,
    LocalFolders,
    folder_children,
    folder_hits,
)
from src import config
from src.archive_window.finder import NO_SUBFOLDERS, ROWS_SHOWN, Finder
from src.search_window.model import FOLDERS, Status, fetch
from tests.test_search_window import _client, api  # noqa: F401 - the scanned drive fixture, shared

REMOTE = ("100.64.0.7", 50000)

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


def _hits(api: TestClient, query: str) -> dict:  # noqa: F811
    r = api.get("/api/archive/folders", params={"q": query})
    assert r.status_code == 200
    return r.json()


def _kids(api: TestClient, place: dict) -> object:  # noqa: F811
    return api.get("/api/archive/subfolders", params={"root": place["root"], "scan_id": place["scan_id"], "id": place["id"]})


def test_the_sheets_search_lists_every_folder_the_email_folders_first_and_marks_the_rest(api: TestClient) -> None:  # noqa: F811
    cfg = config.load()
    mail = cfg["email"]["roots"][0]
    page = _hits(api, "2025")
    assert [(h["name"], h["fileable"]) for h in page["folders"]][0] == ("Northwind", True), "a folder of people folds as Quick search folds it"
    assert any(h["name"] == "2025" and not h["fileable"] for h in page["folders"])
    assert [h["fileable"] for h in page["folders"]] == sorted((h["fileable"] for h in page["folders"]), reverse=True)
    assert all(h["fileable"] == h["path"].startswith(mail) for h in page["folders"])
    assert page["total"] == len(folder_hits(cfg, "2025")["results"]) and len(page["folders"]) <= SHEET_FOLDER_ROWS
    assert all(set(h["location"]) == {"root", "scan_id", "id"} for h in page["folders"])


def test_the_sheet_steps_into_a_folder_by_its_place_in_the_scan_and_out_again(api: TestClient) -> None:  # noqa: F811
    north = next(h for h in _hits(api, "northwind")["folders"] if h["name"] == "Northwind")
    r = _kids(api, north["location"])
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == north["path"] and body["total"] == 2
    assert {h["name"] for h in body["folders"]} == {"ana@example.com", "leo@example.org"} and all(h["fileable"] for h in body["folders"])
    assert all(h["path"].startswith(north["path"] + "\\") for h in body["folders"])
    ana = next(h for h in body["folders"] if h["name"] == "ana@example.com")
    deeper = _kids(api, ana["location"]).json()
    assert [h["name"] for h in deeper["folders"]] == ["2025"] and _kids(api, deeper["folders"][0]["location"]).json()["folders"] == []


def test_a_folder_outside_the_email_folders_can_be_stepped_into_but_is_not_fileable(api: TestClient) -> None:  # noqa: F811
    photos = next(h for h in _hits(api, "photos")["folders"] if h["name"] == "Photos")
    assert photos["fileable"] is False
    kids = _kids(api, photos["location"]).json()["folders"]
    assert kids and not any(h["fileable"] for h in kids)


def test_the_subfolders_route_names_a_folder_by_id_never_by_a_path(api: TestClient, tmp_path: Path) -> None:  # noqa: F811
    north = next(h for h in _hits(api, "northwind")["folders"] if h["name"] == "Northwind")["location"]
    # A path in the request names nothing: the answer is the folder the ids name.
    sneaky = api.get("/api/archive/subfolders", params={**north, "path": str(tmp_path), "folder": str(tmp_path)})
    assert sneaky.status_code == 200 and str(tmp_path) not in sneaky.text
    assert api.get("/api/archive/subfolders", params={"root": north["root"], "scan_id": north["scan_id"]}).status_code == 422
    assert _kids(api, {**north, "scan_id": north["scan_id"] + 1}).status_code == 409, "an id from another scan could name another folder"
    assert _kids(api, {**north, "id": 10**9}).status_code == 404
    assert _kids(api, {**north, "root": "no-such-root"}).status_code == 404


def test_the_subfolders_route_answers_the_pc_only(api: TestClient) -> None:  # noqa: F811
    north = next(h for h in _hits(api, "northwind")["folders"] if h["name"] == "Northwind")["location"]
    with TestClient(app, client=REMOTE) as remote:
        r = remote.get("/api/archive/subfolders", params=north, headers={"Authorization": "Bearer nope"})
    assert r.status_code in (401, 403)
