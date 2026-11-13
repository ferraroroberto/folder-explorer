"""The tray's Quick search window: its model over the real search API on an invented tree, its states, the setting and the tray.

The window's client is pointed at a FastAPI TestClient, so every answer here is the
one the Search tab gets for the same query. Nothing is launched: the open routes'
launch seams are replaced.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterator

import pytest
import requests
from fastapi.testclient import TestClient

from app.webapp.routers import search as search_routes
from app.webapp.server import app
from app.webapp.state import EMAILS
from src import config
from src.search_window.client import UNREACHABLE, ApiError, SearchClient, Unreachable
from src.search_window.model import (
    EMAILS as EMAIL_TAB,
)
from src.search_window.model import (
    EMPTY,
    FOLDERS,
    HINTS,
    MARK_END,
    MARK_START,
    QuickSearchModel,
    Status,
    fetch,
    highlight,
    marked,
    plain,
    words,
)
from tests.conftest import write_config
from tests.email_fixture import build_email_index
from tests.fixture_tree import build_tree

LOCAL = ("127.0.0.1", 50000)
# Invented per-person folders under a client folder, so pruning has a folder of people to fold.
PEOPLE = ("Mail/Clients/Northwind/ana@example.com/2025", "Mail/Clients/Northwind/leo@example.org")


def _scan(c: TestClient) -> None:
    assert c.post("/api/scan", json={}).status_code == 202
    for _ in range(200):
        if not c.get("/api/scan").json()["scanning"]:
            return
        time.sleep(0.05)
    raise AssertionError("the scan did not finish")


@pytest.fixture()
def api(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A scanned invented drive with an email folder and people folders, its email index, and a client on loopback."""
    drive = build_tree(isolated / "Invented drive")
    for rel in PEOPLE:
        (drive / rel).mkdir(parents=True, exist_ok=True)
    mail = drive / "Mail"
    index = build_email_index(isolated / "emails.db", mail)
    write_config(isolated, roots=[str(drive)], email={"index_path": str(index), "roots": [str(mail)]})
    EMAILS.close()
    monkeypatch.setattr("src.email.settings.reader_installed", lambda: True)
    with TestClient(app, client=LOCAL) as c:
        _scan(c)
        yield c
    EMAILS.close()


def _client(c: TestClient) -> SearchClient:
    return SearchClient(lambda: "", send=lambda method, path, **kw: c.request(method, path, **kw))


# ------------------------------------------------------------------ same answers as the Search tab

def test_the_folders_tab_lists_what_the_search_tab_gets(api: TestClient) -> None:
    for query in ("harbour", "photos 2025", "invoices;documents", "BUDGET"):
        page = api.get("/api/search/folders", params={"q": query}).json()
        answer = fetch(_client(api), FOLDERS, query)
        assert [r.target for r in answer.rows] == [{"root": h["root"], "scan_id": h["scan_id"], "id": h["id"]} for h in page["results"]]
        assert answer.total == page["total"]
    harbour = fetch(_client(api), FOLDERS, "harbour")
    assert harbour.status is Status.RESULTS and plain(harbour.rows[0].title) == "harbour-trip"
    assert harbour.rows[0].path.endswith("Photos\\2024\\harbour-trip")


def test_the_emails_tab_lists_what_the_search_tab_gets(api: TestClient) -> None:
    for query in ("budget", "invoice", "from:ana@example.com"):
        page = api.get("/api/search/emails", params={"q": query, "sort": "relevance", "offset": 0}).json()
        answer = fetch(_client(api), EMAIL_TAB, query)
        assert [r.target["id"] for r in answer.rows] == [h["id"] for h in page["hits"]] and answer.total == page["total"]
    budget = fetch(_client(api), EMAIL_TAB, "budget")
    assert any(mark for row in budget.rows for _, mark in row.title), "the index's match marks become highlighted runs"
    assert budget.rows[0].path.endswith(".msg")


def test_words_split_on_spaces_and_semicolons_and_every_word_must_match(api: TestClient) -> None:
    assert words(" alpha;beta  gamma; ") == ["alpha", "beta", "gamma"]
    both = fetch(_client(api), FOLDERS, "photos;garden")
    assert [plain(r.title) for r in both.rows] == ["garden"]
    assert fetch(_client(api), FOLDERS, "photos;nothing-like-this").status is Status.EMPTY


def test_a_folder_of_people_folds_into_one_row_unless_pruning_is_off(api: TestClient) -> None:
    pruned = fetch(_client(api), FOLDERS, "northwind")
    assert [plain(r.title) for r in pruned.rows] == ["Northwind"] and "2 people" in pruned.rows[0].meta
    config.save({"folder_search": {"skip_levels": 0, "prune_people": False, "onedrive_prevails": True}})
    full = fetch(_client(api), FOLDERS, "northwind")
    assert {"Northwind", "ana@example.com", "leo@example.org"} <= {plain(r.title) for r in full.rows}


def test_the_shown_path_drops_the_leading_levels_and_copy_keeps_the_whole_path(api: TestClient) -> None:
    whole = fetch(_client(api), FOLDERS, "harbour").rows[0]
    config.save({"folder_search": {"skip_levels": 2, "prune_people": True, "onedrive_prevails": True}})
    trimmed = fetch(_client(api), FOLDERS, "harbour").rows[0]
    assert plain(trimmed.detail).count("\\") == plain(whole.detail).count("\\") - 2
    assert plain(trimmed.detail).endswith("harbour-trip") and trimmed.path == whole.path


def test_counts_say_how_many_and_when_only_the_first_are_shown(api: TestClient) -> None:
    model = QuickSearchModel(_client(api), query="harbour")
    model.run()
    assert model.count_line() == "1 folder"
    model.tab, model.query = EMAIL_TAB, "budget"
    model.run()
    assert model.count_line() == "3 emails"
    model.apply(model.answer.__class__(EMAIL_TAB, "budget", Status.RESULTS, model.rows[:1], 3))
    assert model.count_line() == "3 emails, first 1 shown"


# ------------------------------------------------------------------ states

def test_an_empty_query_shows_the_hint_and_asks_nothing() -> None:
    asked: list[str] = []
    client = SearchClient(lambda: "", send=lambda *a, **k: asked.append(a[1]))
    for tab in (FOLDERS, EMAIL_TAB):
        answer = fetch(client, tab, "  ;  ")
        assert answer.status is Status.HINT and answer.message == HINTS[tab]
    assert asked == []


def test_an_unreachable_app_is_its_own_state_never_no_matches() -> None:
    def down(*_a: Any, **_k: Any) -> Any:
        raise requests.ConnectionError("refused")

    for tab in (FOLDERS, EMAIL_TAB):
        answer = fetch(SearchClient(lambda: "", send=down), tab, "budget")
        assert answer.status is Status.UNREACHABLE and answer.message == UNREACHABLE
        assert answer.message != EMPTY[tab] and answer.rows == ()
    model = QuickSearchModel(SearchClient(lambda: "", send=down), query="budget")
    model.run()
    assert model.count_line() == "" and model.status is Status.UNREACHABLE


def test_a_closed_port_raises_unreachable_over_real_http() -> None:
    with pytest.raises(Unreachable):
        SearchClient(lambda: "http://127.0.0.1:9").folders("x")


def test_an_email_part_that_isnt_set_up_says_why(isolated: Path) -> None:
    EMAILS.close()
    with TestClient(app, client=LOCAL) as c:
        answer = fetch(_client(c), EMAIL_TAB, "budget")
    assert answer.status is Status.ERROR and "isn't set up" in answer.message


def test_a_late_answer_to_an_older_query_is_dropped(api: TestClient) -> None:
    model = QuickSearchModel(_client(api), query="harbour")
    old = fetch(model.client, FOLDERS, "garden")
    assert model.apply(old) is False and model.answer is None
    model.run()
    assert model.is_current() and model.apply(old) is False and plain(model.rows[0].title) == "harbour-trip"


# ------------------------------------------------------------------ marks

def test_highlight_marks_every_word_case_insensitively_and_skips_single_letters() -> None:
    assert highlight("Budget\\Alpha budget", ["BUDGET", "a"]) == (("Budget", True), ("\\Alpha ", False), ("budget", True))
    assert marked("Q4 " + MARK_START + "budget" + MARK_END + " draft") == (("Q4 ", False), ("budget", True), (" draft", False))


# ------------------------------------------------------------------ opening

def test_open_goes_through_the_apps_routes_by_id(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    folders: list[str] = []
    mails: list[str] = []
    monkeypatch.setattr(search_routes, "ensure_there", lambda *a, **k: None)
    monkeypatch.setattr(search_routes, "open_in_explorer", lambda path, *a: folders.append(path))
    monkeypatch.setattr(search_routes, "open_in_outlook", mails.append)
    model = QuickSearchModel(_client(api), query="harbour", open_folder=False)
    model.run()
    assert model.open() == (True, "Opened in File Explorer") and folders[0].endswith("harbour-trip")
    model.tab, model.query = EMAIL_TAB, "invoice"
    model.run()
    assert model.open(0) == (True, "Opening in Outlook") and mails[0].endswith(".msg")


def test_an_email_opens_its_folder_by_default_and_never_the_mail(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[tuple[str, str]] = []
    mails: list[str] = []
    monkeypatch.setattr(search_routes, "open_item", lambda path, verb, *a: shown.append((path, verb)))
    monkeypatch.setattr(search_routes, "open_in_outlook", mails.append)
    model = QuickSearchModel(_client(api), tab=EMAIL_TAB, query="invoice")
    assert model.open_folder is True
    model.run()
    assert model.open(0) == (True, "Opened the folder")
    assert len(shown) == 1 and shown[0][0].endswith(".msg") and shown[0][1] == "reveal" and mails == []


def test_the_folders_tab_ignores_the_open_folder_toggle(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    folders: list[str] = []
    monkeypatch.setattr(search_routes, "ensure_there", lambda *a, **k: None)
    monkeypatch.setattr(search_routes, "open_in_explorer", lambda path, *a: folders.append(path))
    for on in (True, False):
        model = QuickSearchModel(_client(api), query="harbour", open_folder=on)
        model.run()
        assert model.open() == (True, "Opened in File Explorer")
    assert len(folders) == 2


def test_the_toggle_tells_its_owner_so_the_choice_can_be_kept() -> None:
    told: list[bool] = []
    model = QuickSearchModel(SearchClient(lambda: ""), on_open_folder=told.append)
    model.set_open_folder(False)
    model.set_open_folder(True)
    assert told == [False, True] and model.open_folder is True


def test_a_missing_mail_file_still_says_it_is_no_longer_there(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[str] = []
    monkeypatch.setattr(search_routes, "open_item", lambda path, verb, *a: shown.append(path))
    model = QuickSearchModel(_client(api), tab=EMAIL_TAB, query="invoice")
    model.run()
    Path(model.current().path).unlink()
    ok, text = model.open(0)
    assert ok is False and "no longer there" in text and shown == []


def test_a_refused_open_says_so_and_keeps_the_window(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search_routes, "ensure_there", lambda *a, **k: (_ for _ in ()).throw(search_routes.GoneError("x")))
    model = QuickSearchModel(_client(api), query="harbour")
    model.run()
    ok, text = model.open()
    assert ok is False and "no longer there" in text


def test_arrows_stay_inside_the_list(api: TestClient) -> None:
    model = QuickSearchModel(_client(api))
    model.move(1)
    assert model.current() is None
    model.query = "photos"
    model.run()
    model.move(-5)
    assert model.selected == 0
    model.move(99)
    assert model.selected == len(model.rows) - 1


def test_the_window_reads_only_through_the_api() -> None:
    """No module of the window touches an index, SQLite or the email search: the API is its one source."""
    package = Path(__file__).resolve().parents[1] / "src" / "search_window"
    for source in package.glob("*.py"):
        imports = [ln.strip() for ln in source.read_text(encoding="utf-8").splitlines() if ln.strip().startswith(("import ", "from "))]
        for banned in ("sqlite3", "src.index", "src.store", "src.email", "src.folder_search", "src.desktop"):
            assert not [ln for ln in imports if banned in ln], "%s imports %s" % (source.name, banned)


def test_an_error_answer_carries_the_apps_own_sentence(api: TestClient) -> None:
    with pytest.raises(ApiError) as caught:
        _client(api)._call("GET", "/api/search/emails", params={"q": "x", "sort": "bogus"})
    assert caught.value.status == 400 and "relevance or newest" in str(caught.value)


# ------------------------------------------------------------------ the setting and the tray

def test_the_tray_left_click_setting_defaults_to_quick_search_and_saves(api: TestClient) -> None:
    assert config.DEFAULTS["tray"] == {"left_click": "quick_search"} and config.load()["tray"]["left_click"] == "quick_search"
    for bad in ({"left_click": "browser"}, {"left_click": True}, {"left_click": "open_app", "other": 1}, "open_app"):
        assert api.post("/api/config", json={"tray": bad}).status_code == 400
    saved = api.post("/api/config", json={"tray": {"left_click": "open_app"}})
    assert saved.status_code == 200 and saved.json()["config"]["tray"] == {"left_click": "open_app"}
    assert config.load()["tray"] == {"left_click": "open_app"}


def test_the_quick_search_setting_defaults_on_saves_and_refuses_anything_else(api: TestClient) -> None:
    assert config.DEFAULTS["quick_search"] == {"open_folder": True} and config.load()["quick_search"] == {"open_folder": True}
    for bad in ({"open_folder": "yes"}, {"open_folder": 1}, {"open_folder": False, "other": 1}, {}, "off"):
        assert api.post("/api/config", json={"quick_search": bad}).status_code == 400
    saved = api.post("/api/config", json={"quick_search": {"open_folder": False}})
    assert saved.status_code == 200 and saved.json()["config"]["quick_search"] == {"open_folder": False}
    assert config.load()["quick_search"] == {"open_folder": False}


def test_the_window_remembers_the_toggle_in_config(isolated: Path) -> None:
    from src.search_window import _remember_open_folder

    _remember_open_folder(False)
    assert config.load()["quick_search"] == {"open_folder": False}
    _remember_open_folder(True)
    assert config.load()["quick_search"] == {"open_folder": True}


def test_the_tray_default_item_follows_the_setting_at_click_time(isolated: Path) -> None:
    from app.tray import tray

    assert tray.left_click_searches() is True
    config.save({"tray": {"left_click": "open_app"}})
    assert tray.left_click_searches() is False
    assert tray.local_base().startswith("http") and tray.local_base().startswith(("http://127.0.0.1:", "https://127.0.0.1:"))
