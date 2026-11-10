"""The Quick search window's Tk view over a fake API: typing, the tabs, the error state, keys, copy, hide and show."""

from __future__ import annotations

import time
import tkinter as tk
from typing import Any, Iterator

import pytest
import requests

from src.archive_window import theme
from src.search_window import QuickSearch
from src.search_window.client import UNREACHABLE, SearchClient
from src.search_window.model import EMAILS, MARK_END, MARK_START, QuickSearchModel, Status
from src.search_window.view import QuickSearchView


class FakeResponse:
    def __init__(self, body: dict[str, Any], status: int = 200) -> None:
        self.body, self.status_code = body, status

    def json(self) -> dict[str, Any]:
        return self.body


FOLDER_HITS = [{"root": "inv", "id": 7, "name": "harbour-trip", "display": "Photos\\2024\\harbour-trip",
                "path": "X:\\Invented\\Photos\\2024\\harbour-trip", "files": 3, "size": 1_990_000, "people": 0, "kind": "local"}]
EMAIL_HITS = [{"id": 4, "subject": "Q4 " + MARK_START + "budget" + MARK_END, "from_name": "Ana Lopez", "from_addr": "ana@example.com",
               "date": "2025-10-14T09:12:00", "folder": "X:\\Invented\\Mail\\Clients\\Northwind",
               "path": "X:\\Invented\\Mail\\Clients\\Northwind\\001 - Q4 budget.msg", "snippet": "the revised budget", "attachments": 2}]


class FakeApi:
    """Answers like the app's routes, records every call, and can be switched off to act unreachable."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.down = False

    def __call__(self, method: str, path: str, **kw: Any) -> FakeResponse:
        self.calls.append((method, path, kw))
        if self.down:
            raise requests.ConnectionError("refused")
        if path == "/api/search/folders":
            hits = FOLDER_HITS if "harbour" in kw["params"]["q"] else []
            return FakeResponse({"results": hits, "total": len(hits)})
        if path == "/api/search/emails":
            return FakeResponse({"hits": EMAIL_HITS, "total": 1, "notes": []})
        return FakeResponse({"status": "opened"})


@pytest.fixture(scope="module")
def tk_root() -> Iterator[tk.Tk]:
    try:
        shared = tk.Tk()
    except tk.TclError as exc:
        pytest.skip("Tk can't open a window here: %s" % exc)
    shared.withdraw()
    yield shared
    shared.destroy()


@pytest.fixture()
def window(tk_root: tk.Tk) -> Iterator[tuple[QuickSearchView, FakeApi]]:
    fake = FakeApi()
    view = QuickSearchView(QuickSearchModel(SearchClient(lambda: "", send=fake)), theme.palette(False), tk_root)
    view.build()
    tk_root.update()
    yield view, fake
    view.frame.destroy()


def _settle(view: QuickSearchView, until: Any = None) -> None:
    """Run Tk until the debounce and the worker's answer are painted."""
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        view.root.update()
        if not view.search_job and view.inflight == 0 and (until is None or until()):
            return
        time.sleep(0.01)
    raise AssertionError("the window did not settle")


def _text(view: QuickSearchView) -> str:
    return view.list.get("1.0", "end-1c")


def test_typing_searches_after_a_pause_and_paints_the_rows(window: tuple[QuickSearchView, FakeApi]) -> None:
    view, fake = window
    assert "Every word must match" in _text(view)
    view.query.set("harbour")
    _settle(view)
    assert "harbour-trip" in _text(view) and "Photos\\2024\\harbour-trip" in _text(view)
    assert view.count.cget("text") == "1 folder" and str(view.open_btn.cget("state")) == "normal"
    assert view.list.tag_ranges("mark"), "the query word is highlighted"
    assert [c[1] for c in fake.calls] == ["/api/search/folders"]


def test_the_emails_tab_searches_the_same_query(window: tuple[QuickSearchView, FakeApi]) -> None:
    view, fake = window
    view.query.set("budget")
    _settle(view)
    assert "No folder matches every word." in _text(view)
    view.switch(EMAILS)
    _settle(view)
    assert view.model.tab == EMAILS and "Q4 budget" in _text(view) and "Ana Lopez" in _text(view)
    assert fake.calls[-1][1] == "/api/search/emails"


def test_an_unreachable_app_shows_its_own_message_not_no_matches(window: tuple[QuickSearchView, FakeApi]) -> None:
    view, fake = window
    fake.down = True
    view.query.set("harbour")
    _settle(view)
    assert view.model.status is Status.UNREACHABLE and UNREACHABLE in _text(view)
    assert "No folder matches" not in _text(view) and view.count.cget("text") == ""
    assert view.list.tag_ranges("problem")


def test_enter_searches_a_query_not_yet_answered_then_opens_and_hides(window: tuple[QuickSearchView, FakeApi]) -> None:
    view, fake = window
    view.root.deiconify()
    view.query.set("harbour")
    view.enter()
    _settle(view)
    assert view.model.is_current()
    view.enter()
    assert fake.calls[-1][:2] == ("POST", "/api/folders/open") and fake.calls[-1][2]["json"] == {"root": "inv", "id": 7}
    assert view.root.state() == "withdrawn"


def test_copy_path_puts_the_whole_path_on_the_clipboard(window: tuple[QuickSearchView, FakeApi]) -> None:
    view, _ = window
    view.query.set("harbour")
    _settle(view)
    view.copy_path()
    assert view.root.clipboard_get() == FOLDER_HITS[0]["path"]


def test_hide_keeps_the_tab_and_query_for_the_next_show(window: tuple[QuickSearchView, FakeApi]) -> None:
    view, _ = window
    view.query.set("harbour")
    _settle(view)
    view.hide()
    assert view.root.state() == "withdrawn"
    view.show()
    view.root.update()
    assert view.root.state() == "normal" and view.query.get() == "harbour" and "harbour-trip" in _text(view)
    assert view.root.attributes("-topmost") in (1, True)
    view.hide()


def test_a_second_show_reuses_the_one_window_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[str] = []
    quick = QuickSearch(lambda: "")
    monkeypatch.setattr(quick, "_run", lambda: (started.append("run"), time.sleep(0.3)))
    quick.show()
    quick.show()
    assert started == ["run"] and quick._requests.qsize() == 2
