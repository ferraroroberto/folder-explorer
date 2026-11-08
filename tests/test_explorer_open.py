"""Opening a folder fast: new window or the reused one, the capped existence check, and bringing the window forward.

Nothing here touches the shell: the launch seams in ``src.desktop`` are
recorders, the window watch is replaced, and the foreground calls run against
fake ``win32`` modules. Every path is invented.
"""

from __future__ import annotations

import sys
import time
import types
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.webapp.server import app
from src import config, desktop, window_front
from tests.conftest import write_config

FOLDER = r"X:\Invented\Projects"
LOCAL = ("127.0.0.1", 50000)


@pytest.fixture()
def opened(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    calls: dict[str, list[Any]] = {"shell": [], "follow": [], "reuse": []}
    monkeypatch.setattr(desktop, "_shell", lambda path, verb: calls["shell"].append((path, verb)))
    monkeypatch.setattr(desktop.window_front, "explorer_hwnds", lambda: {11, 12})
    monkeypatch.setattr(desktop.window_front, "follow_in_background",
                        lambda folder, before, started, reused: calls["follow"].append((folder, before, reused)))
    return calls


def test_a_folder_opens_in_a_new_window_by_default(opened: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop, "_reuse_window", lambda folder: pytest.fail("reused a window with the setting off"))
    desktop.open_in_explorer(FOLDER)
    assert opened["shell"] == [(FOLDER, "explore")]
    assert opened["follow"] == [(FOLDER, {11, 12}, None)]


def test_the_reuse_setting_navigates_the_open_window_instead(opened: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop, "reuse_window_on", lambda: True)
    monkeypatch.setattr(desktop, "_reuse_window", lambda folder: opened["reuse"].append(folder) or 77)
    desktop.open_in_explorer(FOLDER)
    assert opened["reuse"] == [FOLDER] and opened["shell"] == []
    assert opened["follow"] == [(FOLDER, set(), 77)]


def test_with_no_window_to_reuse_a_new_one_opens(opened: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop, "reuse_window_on", lambda: True)
    monkeypatch.setattr(desktop, "_reuse_window", lambda folder: None)
    desktop.open_in_explorer(FOLDER)
    assert opened["shell"] == [(FOLDER, "explore")] and opened["follow"][0][2] is None


def test_the_reused_window_is_picked_in_z_order_and_only_navigated(monkeypatch: pytest.MonkeyPatch) -> None:
    class Window:
        def __init__(self, hwnd: int, program: str) -> None:
            self.HWND, self.FullName, self.went = hwnd, program, []

        def Navigate2(self, folder: str) -> None:  # noqa: N802 - the shell's own method name
            self.went.append(folder)

    windows = [Window(1, r"C:\Windows\explorer.exe"), Window(2, r"C:\Windows\explorer.exe"), Window(3, r"C:\Other\iexplore.exe")]
    shell = types.SimpleNamespace(Windows=lambda: windows)
    fake_client = types.SimpleNamespace(Dispatch=lambda name: shell)
    monkeypatch.setitem(sys.modules, "pythoncom",
                        types.SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None))
    monkeypatch.setitem(sys.modules, "win32com", types.SimpleNamespace(client=fake_client))
    monkeypatch.setitem(sys.modules, "win32com.client", fake_client)
    monkeypatch.setattr(desktop, "top_level_z_order", lambda: [3, 2, 1])
    monkeypatch.setattr(desktop.window_front, "is_shown_tab", lambda window: True)
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    assert desktop._reuse_window(FOLDER) == 2
    assert windows[1].went == [FOLDER] and windows[0].went == [] and windows[2].went == []


def test_only_the_shown_tab_of_a_window_is_ever_navigated(monkeypatch: pytest.MonkeyPatch) -> None:
    class Tab:
        def __init__(self, shown: bool) -> None:
            self.HWND, self.FullName, self.shown, self.went = 1, r"C:\Windows\explorer.exe", shown, []

        def Navigate2(self, folder: str) -> None:  # noqa: N802 - the shell's own method name
            self.went.append(folder)

    tabs = [Tab(True), Tab(False)]  # the shown tab first, a hidden one listed after it
    fake_client = types.SimpleNamespace(Dispatch=lambda name: types.SimpleNamespace(Windows=lambda: tabs))
    monkeypatch.setitem(sys.modules, "pythoncom", types.SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None))
    monkeypatch.setitem(sys.modules, "win32com", types.SimpleNamespace(client=fake_client))
    monkeypatch.setitem(sys.modules, "win32com.client", fake_client)
    monkeypatch.setattr(desktop, "top_level_z_order", lambda: [1])
    monkeypatch.setattr(desktop.window_front, "is_shown_tab", lambda tab: tab.shown)
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    assert desktop._reuse_window(FOLDER) == 1
    assert tabs[0].went == [FOLDER] and tabs[1].went == []
    monkeypatch.setattr(desktop.window_front, "is_shown_tab", lambda tab: False)
    assert desktop._reuse_window(FOLDER) is None  # no tab it can place: a new window opens instead


def test_a_tab_that_cannot_be_placed_is_not_the_shown_one() -> None:
    assert window_front.is_shown_tab(object()) is False


def test_a_folder_windows_cannot_find_reads_as_gone(opened: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(path: str, verb: str) -> None:
        raise FileNotFoundError(2, "The system cannot find the file specified")

    monkeypatch.setattr(desktop, "_shell", missing)
    with pytest.raises(desktop.GoneError):
        desktop.open_in_explorer(FOLDER)
    assert opened["follow"] == []


def test_the_off_switch_refuses_before_the_reuse_or_the_watch(opened: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(desktop.DESKTOP_ENV, "off")
    monkeypatch.setattr(desktop, "reuse_window_on", lambda: True)
    monkeypatch.setattr(desktop, "_reuse_window", lambda folder: pytest.fail("reached the reuse"))
    with pytest.raises(FileNotFoundError, match="off") as raised:
        desktop.open_in_explorer(FOLDER)
    assert not isinstance(raised.value, desktop.GoneError) and opened == {"shell": [], "follow": [], "reuse": []}


def test_the_existence_check_answers_there_missing_or_not_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop.os.path, "isdir", lambda p: True)
    desktop.ensure_there(FOLDER, True, time.perf_counter())
    monkeypatch.setattr(desktop.os.path, "isfile", lambda p: False)
    with pytest.raises(desktop.GoneError):
        desktop.ensure_there(FOLDER + r"\notes.txt", False, time.perf_counter())
    monkeypatch.setattr(desktop.os.path, "isdir", lambda p: time.sleep(2) or False)
    began = time.perf_counter()
    desktop.ensure_there(FOLDER, True, began)  # a share that doesn't answer lets the open go ahead
    assert time.perf_counter() - began < desktop.PATH_CHECK_TIMEOUT_S + 0.5


def test_a_folder_gone_after_the_check_answers_410(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.fixture_tree import build_tree

    write_config(isolated, roots=[str(build_tree(isolated / "Invented root"))])

    def gone(path: str, verb: str) -> None:
        raise FileNotFoundError(2, "The system cannot find the file specified")

    monkeypatch.setattr(desktop, "_shell", gone)
    with TestClient(app, client=LOCAL) as c:
        assert c.post("/api/scan", json={}).status_code == 202
        for _ in range(100):
            if not c.get("/api/scan").json()["scanning"]:
                break
            time.sleep(0.05)
        root = c.get("/api/roots").json()["roots"][0]["key"]
        body = c.get("/api/folder", params={"root": root}).json()
        photos = next(it["id"] for it in body["items"] if it["name"] == "Photos")
        r = c.post("/api/open", json={"root": root, "scan_id": body["scan_id"], "kind": "folder", "id": photos, "verb": "explore"})
    assert r.status_code == 410 and r.json()["detail"] == "it is no longer there"


@pytest.mark.parametrize(("shown", "folder"), [
    (r"x:\invented\projects", FOLDER), (FOLDER + "\\", FOLDER), ("X:/Invented/Projects", FOLDER), ("Z:\\", "z:"),
])
def test_same_folder_ignores_case_slashes_and_a_trailing_separator(shown: str, folder: str) -> None:
    assert window_front.same_folder(shown, folder)


def test_same_folder_tells_other_folders_and_empty_paths_apart() -> None:
    assert not window_front.same_folder(r"X:\Invented\Projects old", FOLDER)
    assert not window_front.same_folder("", FOLDER)


def test_the_new_window_wins_and_an_existing_one_only_after_the_grace_period() -> None:
    shown = {5: [FOLDER], 6: [r"X:\Invented\Other"], 9: [r"X:\Invented\Other", FOLDER]}
    assert window_front.pick_window(shown, FOLDER, before={5, 6}, waited_s=0) == 9
    only_old = {5: [FOLDER], 6: [r"X:\Invented\Other"]}
    assert window_front.pick_window(only_old, FOLDER, before={5, 6}, waited_s=0) is None
    assert window_front.pick_window(only_old, FOLDER, before={5, 6}, waited_s=window_front._EXISTING_GRACE_S) == 5
    assert window_front.pick_window({6: [r"X:\Invented\Other"]}, FOLDER, before={6}, waited_s=9) is None


def test_a_reused_window_answers_once_it_shows_the_folder() -> None:
    assert window_front.pick_window({7: [r"X:\Invented\Before"]}, FOLDER, before=set(), waited_s=0, reused=7) is None
    # its tabs share the handle: the navigated tab showing the folder is enough
    assert window_front.pick_window({7: [r"X:\Invented\Other tab", FOLDER], 8: [FOLDER]}, FOLDER, before=set(),
                                    waited_s=0, reused=7) == 7


class _FakeWin32:
    """Just enough of win32gui, win32process, win32api and win32con to drive ``bring_to_front``."""

    SW_RESTORE, VK_MENU, KEYEVENTF_KEYUP = 9, 18, 2

    def __init__(self, iconic: bool, accept_after: str) -> None:
        self.iconic, self.accept_after, self.foreground, self.log = iconic, accept_after, 100, []
        self.alt_down = False

    def IsIconic(self, hwnd: int) -> bool:  # noqa: N802 - the win32 names
        return self.iconic

    def ShowWindow(self, hwnd: int, cmd: int) -> None:  # noqa: N802
        self.log.append(("show", cmd))
        self.iconic = False

    def GetForegroundWindow(self) -> int:  # noqa: N802
        return self.foreground

    def GetCurrentThreadId(self) -> int:  # noqa: N802
        return 1

    def GetWindowThreadProcessId(self, hwnd: int) -> tuple[int, int]:  # noqa: N802
        return (2, 0)

    def AttachThreadInput(self, mine: int, theirs: int, on: bool) -> None:  # noqa: N802
        self.log.append(("attach", on))

    def BringWindowToTop(self, hwnd: int) -> None:  # noqa: N802
        self.log.append(("top", hwnd))

    def SetForegroundWindow(self, hwnd: int) -> None:  # noqa: N802
        if self.accept_after == "attach" or (self.accept_after == "alt" and self.alt_down):
            self.foreground = hwnd
        self.log.append(("fg", hwnd))

    def keybd_event(self, key: int, scan: int, flags: int, extra: int) -> None:
        self.alt_down = flags != self.KEYEVENTF_KEYUP
        self.log.append(("alt", self.alt_down))


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeWin32) -> None:
    for name in ("win32gui", "win32process", "win32api", "win32con"):
        monkeypatch.setitem(sys.modules, name, fake)


def test_a_minimised_window_is_restored_and_brought_forward_by_attaching(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeWin32(iconic=True, accept_after="attach")
    _install(monkeypatch, fake)
    assert window_front.bring_to_front(42) is True
    assert fake.log[0] == ("show", fake.SW_RESTORE)
    assert fake.log[1:] == [("attach", True), ("top", 42), ("fg", 42), ("attach", False)]


def test_a_refused_foreground_gets_the_alt_press_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeWin32(iconic=False, accept_after="alt")
    _install(monkeypatch, fake)
    assert window_front.bring_to_front(42) is True
    assert fake.log[-3:] == [("alt", True), ("fg", 42), ("alt", False)]


def test_a_window_windows_keeps_behind_is_reported_not_claimed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeWin32(iconic=False, accept_after="never")
    _install(monkeypatch, fake)
    assert window_front.bring_to_front(42) is False
    assert fake.log[-1] == ("alt", False)


def test_follow_logs_the_shown_and_front_times(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    shown: list[dict[int, list[str]]] = [{}, {3: [FOLDER]}]
    monkeypatch.setitem(sys.modules, "pythoncom", types.SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None))
    fake_client = types.SimpleNamespace(Dispatch=lambda name: object())
    monkeypatch.setitem(sys.modules, "win32com", types.SimpleNamespace(client=fake_client))
    monkeypatch.setitem(sys.modules, "win32com.client", fake_client)
    monkeypatch.setattr(window_front, "_shown_folders", lambda shell: shown.pop(0) if len(shown) > 1 else shown[0])
    fronted: list[int] = []
    monkeypatch.setattr(window_front, "bring_to_front", lambda hwnd: fronted.append(hwnd) or True)
    with caplog.at_level("INFO", logger="src.window_front"):
        window_front.follow(FOLDER, set(), time.perf_counter())
    assert fronted == [3]
    assert any("window shown at" in r.getMessage() and "in front at" in r.getMessage() for r in caplog.records)


def test_follow_says_when_no_window_showed_the_folder(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setitem(sys.modules, "pythoncom", types.SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None))
    fake_client = types.SimpleNamespace(Dispatch=lambda name: object())
    monkeypatch.setitem(sys.modules, "win32com", types.SimpleNamespace(client=fake_client))
    monkeypatch.setitem(sys.modules, "win32com.client", fake_client)
    monkeypatch.setattr(window_front, "_shown_folders", lambda shell: {})
    monkeypatch.setattr(window_front, "FOLLOW_TIMEOUT_S", 0.05)
    with caplog.at_level("INFO", logger="src.window_front"):
        window_front.follow(FOLDER, set(), time.perf_counter())
    assert any("no File Explorer window showed the folder" in r.getMessage() for r in caplog.records)


def test_the_reuse_setting_is_off_by_default_and_only_takes_true_or_false(isolated: Path) -> None:
    assert config.load()["desktop"] == {"reuse_explorer_window": False}
    with TestClient(app, client=LOCAL) as c:
        assert c.post("/api/config", json={"desktop": {"reuse_explorer_window": "yes"}}).status_code == 400
        assert c.post("/api/config", json={"desktop": {"reuse_explorer_window": True, "other": 1}}).status_code == 400
        saved = c.post("/api/config", json={"desktop": {"reuse_explorer_window": True}})
        assert saved.status_code == 200 and saved.json()["config"]["desktop"] == {"reuse_explorer_window": True}
    assert desktop.reuse_window_on() is True
