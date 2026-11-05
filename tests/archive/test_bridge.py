"""The Outlook bridge's COM helpers over fake COM objects: which mail is at hand, and a mail fetched by its ids."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.outlook import bridge
from src.outlook.mapi import OL_CLASS_MAIL_ITEM, OutlookUnavailableError
from tests.archive.fakes import win32com_client

MAIL = SimpleNamespace(Class=OL_CLASS_MAIL_ITEM, Subject="Opened")
SELECTED = SimpleNamespace(Class=OL_CLASS_MAIL_ITEM, Subject="Selected")
MEETING = SimpleNamespace(Class=53, Subject="A meeting request")


@pytest.fixture(autouse=True)
def _plain_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(win32com_client(monkeypatch), "Dispatch", lambda obj: obj)


def _app(window: object, selection: list[object]) -> SimpleNamespace:
    sel = SimpleNamespace(Count=len(selection), Item=lambda i: selection[i - 1])
    return SimpleNamespace(ActiveWindow=lambda: window, ActiveExplorer=lambda: SimpleNamespace(Selection=sel))


def test_an_opened_mail_wins_over_the_selection() -> None:
    inspector = SimpleNamespace(Class=35, CurrentItem=MAIL)
    assert bridge.mail_at_hand(_app(inspector, [SELECTED])) is MAIL


def test_the_selection_is_used_from_the_main_window() -> None:
    explorer_window = SimpleNamespace(Class=34)
    assert bridge.mail_at_hand(_app(explorer_window, [SELECTED, MAIL])) is SELECTED


def test_nothing_selected_or_not_a_mail_is_no_mail() -> None:
    assert bridge.mail_at_hand(_app(SimpleNamespace(Class=34), [])) is None
    assert bridge.mail_at_hand(_app(SimpleNamespace(Class=34), [MEETING])) is None


def test_a_mail_fetched_by_its_ids_or_none_when_it_moved() -> None:
    def get(entry: str, store: str) -> object:
        if entry != "entry-1":
            raise RuntimeError("The item could not be found.")
        return MAIL

    app = SimpleNamespace(GetNamespace=lambda _name: SimpleNamespace(GetItemFromID=get))
    assert bridge.mail_by_ids(app, "entry-1", "store-1") is MAIL
    assert bridge.mail_by_ids(app, "entry-moved", "store-1") is None


def test_attach_never_starts_outlook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge.process, "get_active_application", lambda: None)
    monkeypatch.setattr(bridge.process, "ensure_running", lambda *a: pytest.fail("the bridge must not start Outlook"))
    with pytest.raises(OutlookUnavailableError):
        bridge.attach()
