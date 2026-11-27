"""The folder shown in the foremost open File Explorer window, for "archive into the folder I have open".

Two layers, split so the choice is testable without Windows:

- :func:`pick_foremost` is pure: given the eligible windows and the window
  z-order it says which one wins. :func:`shown_tabs` lists the windows; opening a
  folder in the foremost one (``src/desktop.py``) uses both.
- :func:`get_current_explorer_folder` asks the shell which windows are open and
  reads the z-order. It tells "the shell answered and nothing is open" (``None``)
  apart from "the shell could not be asked" (:class:`ExplorerUnavailableError`),
  because the user fixes those two differently.

"Foremost" is the highest Explorer window in the z-order, the one looked at last. The
browser or the archive window in front is not an Explorer window, so it never wins.
Nothing here reads a file: the shell reports each window's folder path.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Legacy Internet Explorer instances sit in the same shell collection and are not destinations; the
# hosting program's name tells them apart, and a name with no extension is the same program.
_EXPLORER_PROGRAM = "explorer"
# The window class of each tab inside a File Explorer window (the shown tab is its first child).
_TAB_CLASS = "ShellTabWindowClass"
# The shell service that answers with a tab's own browser, whose window is that tab.
_SID_TOP_LEVEL_BROWSER = "{4C96BE40-915C-11CF-99D3-00AA004AE837}"


def is_explorer(program_path: str) -> bool:
    """True when the shell window's hosting program is File Explorer."""
    return os.path.splitext(os.path.basename(program_path))[0].lower() == _EXPLORER_PROGRAM


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


class ExplorerUnavailableError(RuntimeError):
    """The Windows shell could not be asked at all, which is not the same answer as "no window is open"."""


def pick_foremost(by_hwnd: dict[int, T], z_order: Sequence[int]) -> tuple[int, T] | None:
    """The highest-z-order eligible window as ``(handle, value)``.

    ``by_hwnd`` is in the order the shell listed the windows (oldest first); ``z_order`` is every top-level
    window handle, topmost first, and holds handles that are not Explorer windows. When no eligible window
    is in ``z_order`` the newest listed one wins, and ``None`` means there is no eligible window.
    """
    if not by_hwnd:
        return None
    # A minimised window, or one that closed during the snapshot, is missing from the z-order; the newest is the closest stand-in.
    hwnd = next((h for h in z_order if h in by_hwnd), list(by_hwnd)[-1])
    return hwnd, by_hwnd[hwnd]


def shown_tabs() -> dict[int, Any]:
    """Window handle to the shell object of the shown tab of every File Explorer window, oldest first.

    Every tab of a window reports the window's handle, so only the shown tab speaks for it; a hidden tab
    would answer, or be navigated, where the user isn't looking.
    """
    import win32com.client  # noqa: PLC0415 - Windows only, imported where it is used

    tabs: dict[int, Any] = {}
    for window in win32com.client.Dispatch("Shell.Application").Windows():
        try:
            if is_explorer(str(window.FullName or "")) and is_shown_tab(window):
                tabs[int(window.HWND)] = window
        except Exception as exc:  # noqa: BLE001 - a window can close while the shell is listing
            logger.debug("Skipping a shell window: %s", exc)
    return tabs


def _enumerate_explorer_folders() -> dict[int, str]:
    """Window handle to folder path for every File Explorer window showing a real folder.

    This PC, Quick Access, the Recycle Bin and the like have no folder path, so they are skipped.
    """
    folders: dict[int, str] = {}
    for hwnd, window in shown_tabs().items():
        try:
            path = str(window.Document.Folder.Self.Path or "")
        except Exception as exc:  # noqa: BLE001 - a window can close while the shell is listing
            logger.debug("Skipping a shell window: %s", exc)
            continue
        if path and os.path.isdir(path):
            folders[hwnd] = path
    return folders


def top_level_z_order() -> list[int]:
    """Every top-level window handle, topmost first."""
    import win32gui  # noqa: PLC0415 - Windows only, imported where it is used

    handles: list[int] = []
    win32gui.EnumWindows(lambda hwnd, found: found.append(hwnd), handles)
    return handles


def get_current_explorer_folder() -> str | None:
    """The folder of the foremost File Explorer window, or ``None`` when none shows a real folder.

    Raises :class:`ExplorerUnavailableError` when the shell can't be asked; that is never read as "nothing is open".
    """
    try:
        import pythoncom  # noqa: PLC0415 - Windows only, imported where it is used

        # CoInitialize is reference-counted, so a thread that already has an apartment pairs it with the uninitialize below.
        pythoncom.CoInitialize()
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ explorer: can't start COM to read the open windows: %s", exc)
        raise ExplorerUnavailableError(str(exc)) from exc
    try:
        try:
            folders = _enumerate_explorer_folders()
        except Exception as exc:  # noqa: BLE001
            logger.warning("⚠️ explorer: can't list the open windows: %s", exc)
            raise ExplorerUnavailableError(str(exc)) from exc
        if not folders:
            logger.info("ℹ️ explorer: no open window shows a real folder")
            return None
        try:
            z_order = top_level_z_order()
        except Exception as exc:  # noqa: BLE001 - without a z-order the newest window still answers
            logger.warning("⚠️ explorer: can't read the window order: %s", exc)
            z_order = []
        picked = pick_foremost(folders, z_order)
        logger.info("ℹ️ explorer: the foremost window is open on a folder")
        return None if picked is None else picked[1]
    finally:
        pythoncom.CoUninitialize()
