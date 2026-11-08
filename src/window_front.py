"""Find the File Explorer window an open just showed, bring it to the front, and log how long each step took.

An open hands the folder to Windows and returns; the window appears a few hundred
milliseconds later, often behind the browser, because Windows refuses
``SetForegroundWindow`` to a process the user isn't typing in (the foreground
lock). :func:`follow_in_background` waits for the window on its own thread,
restores it when minimised, brings it forward, and logs the milliseconds from
the click to "window shown" and to "in front", which is how a slow PC tells
where its time goes.

Nothing here starts a program or reads a file: the shell reports each window's
folder as a string, and the comparison is on that string, never a stat.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Iterable
from typing import Optional

from src.explorer_window import is_explorer

logger = logging.getLogger(__name__)

# The window class of every File Explorer folder window, and of each tab inside one (the shown tab is its first child).
_CABINET_CLASS = "CabinetWClass"
_TAB_CLASS = "ShellTabWindowClass"
# The shell service that answers with a tab's own browser, whose window is that tab.
_SID_TOP_LEVEL_BROWSER = "{4C96BE40-915C-11CF-99D3-00AA004AE837}"
# Long enough for a cold Explorer on a slow PC; past it the open is logged as not seen.
FOLLOW_TIMEOUT_S = 5.0
_POLL_S = 0.015
# A window that already showed the folder before the open stands in only once no new one has appeared by then.
_EXISTING_GRACE_S = 1.5


def explorer_hwnds() -> set[int]:
    """Every visible File Explorer folder window, by handle: the "before" an open's new window is told apart from.

    Windows keeps a hidden Explorer window ready and shows the next folder in it, so
    a hidden window counts as new when it appears.
    """
    import win32gui  # noqa: PLC0415 - Windows only, imported where it is used

    found: set[int] = set()

    def keep(hwnd: int, _extra: None) -> bool:
        if win32gui.GetClassName(hwnd) == _CABINET_CLASS and win32gui.IsWindowVisible(hwnd):
            found.add(hwnd)
        return True

    win32gui.EnumWindows(keep, None)
    return found


def same_folder(shown: str, folder: str) -> bool:
    """True when the shell's path for a window names ``folder``, ignoring case, slashes and a trailing separator."""
    def norm(path: str) -> str:
        return os.path.normcase(path).rstrip("\\/")

    return bool(shown) and norm(shown) == norm(folder)


def pick_window(shown: dict[int, list[str]], folder: str, before: Iterable[int], waited_s: float,
                reused: Optional[int] = None) -> Optional[int]:
    """The window that answers the open, or ``None`` while there is none yet.

    ``shown`` maps each Explorer window (oldest first) to the folders of its tabs,
    which share the window's handle. A reused window answers once it shows the
    folder. Otherwise a window that wasn't open before wins; one that already
    showed the folder answers only after the grace period.
    """
    def shows(hwnd: int) -> bool:
        return any(same_folder(path, folder) for path in shown.get(hwnd, []))

    if reused is not None:
        return reused if shows(reused) else None
    earlier = set(before)
    matching = [hwnd for hwnd in shown if shows(hwnd)]
    new = [hwnd for hwnd in matching if hwnd not in earlier]
    if new:
        return new[-1]
    if matching and waited_s >= _EXISTING_GRACE_S:
        return matching[-1]
    return None


def _shown_folders(shell: object) -> dict[int, list[str]]:
    """Each Explorer window's handle to its tabs' folder paths as the shell reports them ('' for This PC and the like)."""
    shown: dict[int, list[str]] = {}
    for window in shell.Windows():  # type: ignore[attr-defined]
        try:
            if is_explorer(str(window.FullName or "")):
                shown.setdefault(int(window.HWND), []).append(str(window.Document.Folder.Self.Path or ""))
        except Exception:  # noqa: BLE001 - a window can close or still be loading while the shell lists it
            continue
    return shown


def is_shown_tab(window: object) -> bool:
    """True when this shell window is the tab its File Explorer window shows; False when that can't be told.

    Every tab of a window reports the window's handle, so navigating any but the
    shown one would change a tab the user can't see.
    """
    try:
        import pythoncom  # noqa: PLC0415 - Windows only, imported where it is used
        import pywintypes  # noqa: PLC0415
        import win32gui  # noqa: PLC0415
        from win32com.shell import shell  # noqa: PLC0415

        provider = window._oleobj_.QueryInterface(pythoncom.IID_IServiceProvider)  # type: ignore[attr-defined]
        browser = provider.QueryService(pywintypes.IID(_SID_TOP_LEVEL_BROWSER), shell.IID_IShellBrowser)
        return bool(browser.GetWindow() == win32gui.FindWindowEx(int(window.HWND), 0, _TAB_CLASS, None))  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - a tab that can't be placed is never reused
        logger.debug("Can't tell whether a tab is the shown one: %s", exc)
        return False


def _set_foreground(hwnd: int) -> None:
    import win32gui  # noqa: PLC0415 - Windows only, imported where it is used

    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception as exc:  # noqa: BLE001 - a refusal is read back from GetForegroundWindow
        logger.debug("SetForegroundWindow refused: %s", exc)


def bring_to_front(hwnd: int) -> bool:
    """Restore ``hwnd`` if minimised and make it the foreground window; True when it really is in front.

    First joins the foreground window's input queue (the attach-thread-input
    pattern), which Windows accepts from a background process; when that is
    still refused, an ALT press around the call lifts the lock, as a key press does.
    """
    import win32api  # noqa: PLC0415 - Windows only, imported where it is used
    import win32con  # noqa: PLC0415
    import win32gui  # noqa: PLC0415
    import win32process  # noqa: PLC0415

    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    foreground = win32gui.GetForegroundWindow()
    if foreground == hwnd:
        return True
    mine = win32api.GetCurrentThreadId()
    theirs = win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
    attached = False
    if theirs and theirs != mine:
        try:
            win32process.AttachThreadInput(mine, theirs, True)
            attached = True
        except Exception as exc:  # noqa: BLE001 - the ALT fallback below still gets its turn
            logger.debug("AttachThreadInput refused: %s", exc)
    try:
        win32gui.BringWindowToTop(hwnd)
        _set_foreground(hwnd)
    finally:
        if attached:
            win32process.AttachThreadInput(mine, theirs, False)
    if win32gui.GetForegroundWindow() == hwnd:
        return True
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    try:
        _set_foreground(hwnd)
    finally:
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    return win32gui.GetForegroundWindow() == hwnd


def _ms(since: float) -> float:
    return (time.perf_counter() - since) * 1000


def follow(folder: str, before: set[int], started: float, reused: Optional[int] = None) -> None:
    """Wait for the window showing ``folder``, bring it forward and log the times from ``started`` (the request)."""
    try:
        import pythoncom  # noqa: PLC0415 - Windows only, imported where it is used
        import win32com.client  # noqa: PLC0415

        pythoncom.CoInitialize()
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ open: can't watch for the File Explorer window: %s", exc)
        return
    try:
        shell = win32com.client.Dispatch("Shell.Application")
        polling = time.perf_counter()
        hwnd = None
        while hwnd is None and time.perf_counter() - polling < FOLLOW_TIMEOUT_S:
            hwnd = pick_window(_shown_folders(shell), folder, before, time.perf_counter() - polling, reused)
            if hwnd is None:
                time.sleep(_POLL_S)
        if hwnd is None:
            logger.warning("⚠️ open: no File Explorer window showed the folder within %.0f s", FOLLOW_TIMEOUT_S)
            return
        shown_ms = _ms(started)
        in_front = bring_to_front(hwnd)
        if in_front:
            logger.info("ℹ️ open: window shown at %.0f ms, in front at %.0f ms", shown_ms, _ms(started))
        else:
            logger.warning("⚠️ open: window shown at %.0f ms, but Windows kept it behind (foreground lock)", shown_ms)
    except Exception as exc:  # noqa: BLE001 - the open itself already happened; this only reports on it
        logger.warning("⚠️ open: lost track of the File Explorer window: %s", exc)
    finally:
        pythoncom.CoUninitialize()


def follow_in_background(folder: str, before: set[int], started: float, reused: Optional[int] = None) -> None:
    """Run :func:`follow` on a daemon thread, so the open's caller returns at once."""
    threading.Thread(target=follow, args=(folder, before, started, reused), name="explorer-front", daemon=True).start()
