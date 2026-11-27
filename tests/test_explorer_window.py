"""Which File Explorer window wins, and the shell being unreachable versus nothing being open (invented paths, no COM)."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

import src.explorer_window as explorer
from src.explorer_window import ExplorerUnavailableError, pick_foremost


def test_no_eligible_windows_returns_none() -> None:
    assert pick_foremost({}, [111, 222]) is None


def test_single_window_wins() -> None:
    assert pick_foremost({222: r"E:\mail\alpha"}, [999, 222]) == (222, r"E:\mail\alpha")


def test_highest_z_order_window_wins_not_the_first_listed() -> None:
    # The shell lists oldest first; the window looked at last is the one highest in the z-order.
    folders = {111: r"E:\mail\old", 222: r"E:\mail\current"}
    assert pick_foremost(folders, [222, 111]) == (222, r"E:\mail\current")


def test_windows_that_are_not_explorer_never_block_the_pick() -> None:
    # The browser and the archive window sit in front but are not in the eligible mapping.
    assert pick_foremost({111: r"E:\mail\alpha"}, [900, 901, 111]) == (111, r"E:\mail\alpha")


def test_the_newest_window_wins_when_the_z_order_has_no_match() -> None:
    folders = {111: r"E:\mail\old", 222: r"E:\mail\newest"}
    assert pick_foremost(folders, []) == (222, r"E:\mail\newest")


def test_only_the_explorer_program_hosts_a_destination() -> None:
    assert explorer.is_explorer(r"C:\Windows\EXPLORER.EXE") and explorer.is_explorer("/win/explorer")
    assert not explorer.is_explorer(r"C:\Program Files\Internet Explorer\iexplore.exe") and not explorer.is_explorer("")


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
    monkeypatch.setattr(explorer, "top_level_z_order", lambda: [50, 1, 2])
    assert explorer.get_current_explorer_folder() == r"E:\mail\a"


def test_an_unreadable_z_order_falls_back_to_the_newest_window(monkeypatch: pytest.MonkeyPatch, com: list[str]) -> None:
    def refused() -> list[int]:
        raise OSError("no window list")

    monkeypatch.setattr(explorer, "_enumerate_explorer_folders", lambda: {1: r"E:\mail\a", 2: r"E:\mail\b"})
    monkeypatch.setattr(explorer, "top_level_z_order", refused)
    assert explorer.get_current_explorer_folder() == r"E:\mail\b"


def test_a_hidden_tab_never_speaks_for_its_window(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, com: list[str]) -> None:
    shown, hidden = tmp_path / "shown", tmp_path / "hidden"
    shown.mkdir()
    hidden.mkdir()

    class Tab:
        def __init__(self, folder: Path, is_shown: bool) -> None:
            # Every tab of a window reports the window's handle.
            self.HWND, self.FullName, self.is_shown = 1, r"C:\Windows\explorer.exe", is_shown
            self.Document = types.SimpleNamespace(Folder=types.SimpleNamespace(Self=types.SimpleNamespace(Path=str(folder))))

    tabs = [Tab(shown, True), Tab(hidden, False)]  # the shown tab is listed first, so a last-one-wins dict keeps the hidden one
    fake_client = types.SimpleNamespace(Dispatch=lambda name: types.SimpleNamespace(Windows=lambda: tabs))
    monkeypatch.setitem(sys.modules, "win32com", types.SimpleNamespace(client=fake_client))
    monkeypatch.setitem(sys.modules, "win32com.client", fake_client)
    monkeypatch.setattr(explorer, "is_shown_tab", lambda window: window.is_shown)
    assert explorer._enumerate_explorer_folders() == {1: str(shown)}
    monkeypatch.setattr(explorer, "top_level_z_order", lambda: [1])
    assert explorer.get_current_explorer_folder() == str(shown)
    monkeypatch.setattr(explorer, "is_shown_tab", lambda window: False)  # no tab can be placed: nothing is claimed
    assert explorer.get_current_explorer_folder() is None


def test_every_module_that_lists_shell_windows_keeps_to_the_shown_tab() -> None:
    # Tabs share their window's handle, so a reader that keys by handle without this check answers a hidden tab.
    # shown_tabs() is that one reader; window_front keeps every tab on purpose (it matches the folder an open just showed).
    src = Path(__file__).resolve().parent.parent / "src"
    readers = [p for p in src.rglob("*.py") if 'Dispatch("Shell.Application")' in p.read_text(encoding="utf-8")]
    assert sorted(p.name for p in readers) == ["explorer_window.py", "window_front.py"]
