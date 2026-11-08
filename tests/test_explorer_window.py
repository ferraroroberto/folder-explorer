"""Which File Explorer window wins, and the shell being unreachable versus nothing being open (invented paths, no COM)."""

from __future__ import annotations

import sys

import pytest

import src.explorer_window as explorer
from src.explorer_window import ExplorerUnavailableError, pick_foremost_folder


def test_no_eligible_windows_returns_none() -> None:
    assert pick_foremost_folder({}, [111, 222]) is None


def test_single_window_wins() -> None:
    assert pick_foremost_folder({222: r"E:\mail\alpha"}, [999, 222]) == r"E:\mail\alpha"


def test_highest_z_order_window_wins_not_the_first_listed() -> None:
    # The shell lists oldest first; the window looked at last is the one highest in the z-order.
    folders = {111: r"E:\mail\old", 222: r"E:\mail\current"}
    assert pick_foremost_folder(folders, [222, 111]) == r"E:\mail\current"


def test_windows_that_are_not_explorer_never_block_the_pick() -> None:
    # The browser and the archive window sit in front but are not in the eligible mapping.
    assert pick_foremost_folder({111: r"E:\mail\alpha"}, [900, 901, 111]) == r"E:\mail\alpha"


def test_the_newest_window_wins_when_the_z_order_has_no_match() -> None:
    folders = {111: r"E:\mail\old", 222: r"E:\mail\newest"}
    assert pick_foremost_folder(folders, []) == r"E:\mail\newest"


def test_only_the_explorer_program_hosts_a_destination() -> None:
    assert explorer._is_explorer(r"C:\Windows\EXPLORER.EXE") and explorer._is_explorer("/win/explorer")
    assert not explorer._is_explorer(r"C:\Program Files\Internet Explorer\iexplore.exe") and not explorer._is_explorer("")


@pytest.fixture()
def com(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """pythoncom replaced, so the start and end of the apartment are seen without Windows."""
    calls: list[str] = []
    fake = type("FakePythonCom", (), {"CoInitialize": staticmethod(lambda: calls.append("init")),
                                     "CoUninitialize": staticmethod(lambda: calls.append("uninit"))})
    monkeypatch.setitem(sys.modules, "pythoncom", fake)
    return calls


def test_nothing_open_is_an_answer_of_none(monkeypatch: pytest.MonkeyPatch, com: list[str]) -> None:
    monkeypatch.setattr(explorer, "_enumerate_explorer_folders", dict)
    assert explorer.get_current_explorer_folder() is None
    assert com == ["init", "uninit"]


def test_an_unreachable_shell_raises_instead_of_reading_as_nothing_open(monkeypatch: pytest.MonkeyPatch, com: list[str]) -> None:
    def boom() -> dict[int, str]:
        raise OSError("COM server unavailable")

    monkeypatch.setattr(explorer, "_enumerate_explorer_folders", boom)
    with pytest.raises(ExplorerUnavailableError):
        explorer.get_current_explorer_folder()
    assert com == ["init", "uninit"]


def test_no_com_at_all_is_also_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken:
        @staticmethod
        def CoInitialize() -> None:
            raise OSError("no apartment")

    monkeypatch.setitem(sys.modules, "pythoncom", Broken)
    with pytest.raises(ExplorerUnavailableError):
        explorer.get_current_explorer_folder()


def test_the_foremost_of_the_open_windows_is_returned(monkeypatch: pytest.MonkeyPatch, com: list[str]) -> None:
    monkeypatch.setattr(explorer, "_enumerate_explorer_folders", lambda: {1: r"E:\mail\a", 2: r"E:\mail\b"})
    monkeypatch.setattr(explorer, "_top_level_z_order", lambda: [50, 1, 2])
    assert explorer.get_current_explorer_folder() == r"E:\mail\a"


def test_an_unreadable_z_order_falls_back_to_the_newest_window(monkeypatch: pytest.MonkeyPatch, com: list[str]) -> None:
    def refused() -> list[int]:
        raise OSError("no window list")

    monkeypatch.setattr(explorer, "_enumerate_explorer_folders", lambda: {1: r"E:\mail\a", 2: r"E:\mail\b"})
    monkeypatch.setattr(explorer, "_top_level_z_order", refused)
    assert explorer.get_current_explorer_folder() == r"E:\mail\b"
