"""The folder shown in the foremost open File Explorer window, for "archive into the folder I have open".

Two layers, split so the choice is testable without Windows:

- :func:`pick_foremost_folder` is pure: given the eligible windows and the window
  z-order it says which one wins.
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

logger = logging.getLogger(__name__)

# Legacy Internet Explorer instances sit in the same shell collection and are not destinations; the
# hosting program's name tells them apart, and a name with no extension is the same program.
_EXPLORER_PROGRAM = "explorer"


def _is_explorer(program_path: str) -> bool:
    """True when the shell window's hosting program is File Explorer."""
    return os.path.splitext(os.path.basename(program_path))[0].lower() == _EXPLORER_PROGRAM


class ExplorerUnavailableError(RuntimeError):
    """The Windows shell could not be asked at all, which is not the same answer as "no window is open"."""


def pick_foremost_folder(folders_by_hwnd: dict[int, str], z_order: Sequence[int]) -> str | None:
    """The folder of the highest-z-order eligible window.

    ``folders_by_hwnd`` is in the order the shell listed the windows (oldest first); ``z_order`` is every
    top-level window handle, topmost first, and holds handles that are not Explorer windows. When no
    eligible window is in ``z_order`` the newest listed one wins, and ``None`` means there is no eligible window.
    """
    if not folders_by_hwnd:
        return None
    for hwnd in z_order:
        folder = folders_by_hwnd.get(hwnd)
        if folder is not None:
            return folder
    # A minimised window, or one that closed during the snapshot, is missing from the z-order; the newest is the closest stand-in.
    return list(folders_by_hwnd.values())[-1]


def _enumerate_explorer_folders() -> dict[int, str]:
    """Window handle to folder path for every File Explorer window showing a real folder.

    This PC, Quick Access, the Recycle Bin and the like have no folder path, so they are skipped.
    """
    import win32com.client  # noqa: PLC0415 - Windows only, imported where it is used

    folders: dict[int, str] = {}
    for window in win32com.client.Dispatch("Shell.Application").Windows():
        try:
            if not _is_explorer(str(window.FullName or "")):
                continue
            hwnd = int(window.HWND)
            path = str(window.Document.Folder.Self.Path or "")
        except Exception as exc:  # noqa: BLE001 - a window can close while the shell is listing
            logger.debug("Skipping a shell window: %s", exc)
            continue
        if path and os.path.isdir(path):
            folders[hwnd] = path
    return folders


def _top_level_z_order() -> list[int]:
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
            z_order = _top_level_z_order()
        except Exception as exc:  # noqa: BLE001 - without a z-order the newest window still answers
            logger.warning("⚠️ explorer: can't read the window order: %s", exc)
            z_order = []
        chosen = pick_foremost_folder(folders, z_order)
        logger.info("ℹ️ explorer: the foremost window is open on a folder")
        return chosen
    finally:
        pythoncom.CoUninitialize()
