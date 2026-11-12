"""Open a mail in Outlook, a folder in File Explorer, a file in its own app or a document window on this PC, without waiting.

Only the web routes that accept loopback callers and the "Open with" launcher
(``app/launcher.py``, a document window) use these: the phone gets Copy path
and Show folder instead. Outlook opens the archived ``.msg`` itself
(``outlook.exe /f``), never the live mail, whose id changes when it moves.

None of this reads a file: each call hands a path to Windows (File Explorer,
or the shell's ``open`` or ``edit`` verb), and the app it starts does the rest.
Every launch goes through :func:`_spawn`, :func:`_shell` or :func:`_navigate`,
which refuse while ``FOLDER_EXPLORER_DESKTOP=off``, so a test instance can never
start anything.

A folder opens through the shell's ``explore`` verb, which the Explorer already
running answers without starting a new ``explorer.exe``, or, with the
``desktop.reuse_explorer_window`` setting on, in the foremost Explorer window.
:mod:`src.window_front` then brings that window forward and logs the timings.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from typing import Any, Optional, Union

from src import config, window_front
from src.explorer_window import is_explorer, top_level_z_order
from src.no_window import NO_WINDOW

logger = logging.getLogger(__name__)

_APP_PATHS = "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\"

# ``off`` refuses every launch; the e2e suite's disposable instance sets it.
DESKTOP_ENV = "FOLDER_EXPLORER_DESKTOP"
OFF_REASON = "Opening things is off for this instance"

# What each kind of item can be asked to do; the first is the double-click.
FOLDER_VERBS = ("explore",)
FILE_VERBS = ("open", "edit", "reveal")

# A local or OneDrive folder answers its existence check in well under a millisecond; a slow
# or offline share can block for seconds, so past this the open goes ahead unconfirmed.
PATH_CHECK_TIMEOUT_S = 0.25


class GoneError(OSError):
    """The item isn't at its path any more: a different answer from "nothing can be opened here"."""


def desktop_off() -> bool:
    return os.environ.get(DESKTOP_ENV, "").strip().lower() == "off"


def _check() -> None:
    if desktop_off():
        raise FileNotFoundError(OFF_REASON)
    if sys.platform != "win32":
        raise FileNotFoundError("File Explorer is only on Windows")


def _spawn(command: Union[list[str], str]) -> None:
    """Start a program and return at once; a string is passed to Windows as the whole command line."""
    _check()
    subprocess.Popen(command, creationflags=NO_WINDOW, close_fds=True)


def _shell(path: str, verb: str) -> None:
    """Run the shell's ``verb`` on ``path``: the program it starts is meant to show its window."""
    _check()
    os.startfile(path, verb)  # type: ignore[attr-defined]  # Windows only, and _check stops anything else


def _navigate(window: Any, folder: str) -> None:
    """Point an open Explorer window at ``folder``."""
    _check()
    window.Navigate2(folder)


def _explorer() -> str:
    return os.path.join(os.environ.get("SystemRoot", os.environ.get("windir", "")), "explorer.exe")


def outlook_exe() -> Optional[str]:
    """Outlook (classic)'s executable, or None when it isn't installed."""
    return _app_exe("OUTLOOK.EXE")


def _app_exe(exe: str) -> Optional[str]:
    """A program's executable from its App Paths registration, or None when it isn't installed."""
    if sys.platform != "win32":
        return None
    import winreg

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, _APP_PATHS + exe) as key:
                value, _ = winreg.QueryValueEx(key, "")
        except OSError:
            continue
        if value and os.path.isfile(str(value)):
            return str(value)
    return None


def open_in_outlook(msg_path: str) -> None:
    """Show the ``.msg`` in Outlook; raises ``FileNotFoundError`` when Outlook isn't installed."""
    exe = outlook_exe()
    if exe is None:
        raise FileNotFoundError("Outlook (classic) isn't installed on this PC")
    _spawn([exe, "/f", msg_path])
    logger.info("ℹ️ opened a mail in Outlook: %s", os.path.basename(msg_path))


def _ms(since: float) -> float:
    return (time.perf_counter() - since) * 1000


def ensure_there(path: str, folder: bool, started: float) -> None:
    """Raise :class:`GoneError` when ``path`` is missing; a check that doesn't answer in time lets the open go ahead.

    The check runs on its own thread and is waited for at most
    :data:`PATH_CHECK_TIMEOUT_S`. Logs how long the request took to reach it and what it found.
    """
    answer: list[bool] = []
    checking = time.perf_counter()
    probe = threading.Thread(target=lambda: answer.append(os.path.isdir(path) if folder else os.path.isfile(path)),
                             name="open-check", daemon=True)
    probe.start()
    probe.join(PATH_CHECK_TIMEOUT_S)
    found = "there" if answer == [True] else "missing" if answer else "not confirmed, opening anyway"
    logger.info("ℹ️ open: request ready at %.0f ms, existence check %.0f ms (%s)",
                (checking - started) * 1000, _ms(checking), found)
    if answer == [False]:
        raise GoneError("it is no longer there")


def reuse_window_on() -> bool:
    return bool(config.load()["desktop"]["reuse_explorer_window"])


def _reuse_window(folder: str) -> Optional[int]:
    """Navigate the shown tab of the foremost File Explorer window to ``folder``; its handle, or None when there is none to reuse."""
    try:
        import pythoncom  # noqa: PLC0415 - Windows only, imported where it is used
        import win32com.client  # noqa: PLC0415

        pythoncom.CoInitialize()
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ open: can't reach the open Explorer windows, opening a new one: %s", exc)
        return None
    try:
        windows: dict[int, Any] = {}
        for window in win32com.client.Dispatch("Shell.Application").Windows():
            try:
                if is_explorer(str(window.FullName or "")) and window_front.is_shown_tab(window):
                    windows[int(window.HWND)] = window
            except Exception:  # noqa: BLE001,S112 - a window can close while the shell lists it
                continue
        if not windows:
            logger.info("ℹ️ open: no Explorer window to reuse, opening a new one")
            return None
        hwnd = next((h for h in top_level_z_order() if h in windows), list(windows)[-1])
        _navigate(windows[hwnd], folder)
        return hwnd
    except FileNotFoundError:
        raise
    except Exception as exc:  # noqa: BLE001 - a new window still shows the folder
        logger.warning("⚠️ open: couldn't reuse the Explorer window, opening a new one: %s", exc)
        return None
    finally:
        pythoncom.CoUninitialize()


def open_in_explorer(folder: str, started: Optional[float] = None) -> None:
    """Show ``folder`` in File Explorer and return at once; ``started`` is when the request arrived, for the timings.

    Raises ``FileNotFoundError`` when nothing can be opened here and
    :class:`GoneError` when Windows finds no such folder.
    """
    started = time.perf_counter() if started is None else started
    _check()
    reused = _reuse_window(folder) if reuse_window_on() else None
    before: set[int] = set()
    if reused is None:
        before = window_front.explorer_hwnds()
        try:
            _shell(folder, "explore")
        except FileNotFoundError as exc:
            raise GoneError("it is no longer there") from exc
    logger.info("ℹ️ open: File Explorer asked for the folder in %s window at %.0f ms",
                "the reused" if reused is not None else "a new", _ms(started))
    window_front.follow_in_background(folder, before, started, reused)


def open_item(path: str, verb: str, started: Optional[float] = None) -> None:
    """Run ``verb`` on ``path``: ``explore`` a folder, or ``open``, ``edit`` or ``reveal`` a file.

    Raises ``ValueError`` for any other verb, ``FileNotFoundError`` when nothing
    can be opened here, and ``OSError`` when Windows has no such action for the file.
    """
    if verb == "explore":
        open_in_explorer(path, started)
    elif verb == "reveal":
        # explorer.exe parses its own command line: /select, then the quoted path.
        _spawn('"%s" /select,"%s"' % (_explorer(), path))
        logger.info("ℹ️ showed a file in File Explorer")
    elif verb in ("open", "edit"):
        _shell(path, verb)
        logger.info("ℹ️ asked Windows to %s a %s file", verb, os.path.splitext(path)[1].lower() or "typeless")
    else:
        raise ValueError("unknown action: " + verb)


def open_app_window(url: str) -> None:
    """Show ``url`` (the app's own /doc page) in an Edge app window, or in the default browser without Edge.

    Raises ``FileNotFoundError`` when nothing can be opened here.
    """
    _check()
    edge = _app_exe("msedge.exe")
    if edge is not None:
        _spawn([edge, "--app=" + url])
        logger.info("ℹ️ opened a document window in Edge")
    else:
        _shell(url, "open")
        logger.info("ℹ️ opened a document page in the default browser")
