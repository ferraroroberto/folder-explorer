"""Open a mail in Outlook, a folder in File Explorer or a file in its own app on this PC, without waiting.

Only the web routes that accept loopback callers use these: the phone gets
Copy path and Show folder instead. Outlook opens the archived ``.msg`` itself
(``outlook.exe /f``), never the live mail, whose id changes when it moves.

None of this reads a file: each call hands a path to Windows (File Explorer,
or the shell's ``open`` or ``edit`` verb), and the app it starts does the rest.
Every launch goes through :func:`_spawn` or :func:`_shell`, which refuse while
``FOLDER_EXPLORER_DESKTOP=off``, so a test instance can never start anything.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Optional, Union

from src.no_window import NO_WINDOW

logger = logging.getLogger(__name__)

_OUTLOOK_APP_PATH = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\OUTLOOK.EXE"

# ``off`` refuses every launch; the e2e suite's disposable instance sets it.
DESKTOP_ENV = "FOLDER_EXPLORER_DESKTOP"
OFF_REASON = "Opening things is off for this instance"

# What each kind of item can be asked to do; the first is the double-click.
FOLDER_VERBS = ("explore",)
FILE_VERBS = ("open", "edit", "reveal")


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


def _explorer() -> str:
    return os.path.join(os.environ.get("SystemRoot", os.environ.get("windir", "")), "explorer.exe")


def outlook_exe() -> Optional[str]:
    """Outlook (classic)'s executable from its App Paths registration, or None when it isn't installed."""
    if sys.platform != "win32":
        return None
    import winreg

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, _OUTLOOK_APP_PATH) as key:
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


def open_in_explorer(folder: str) -> None:
    """Show ``folder`` in a File Explorer window."""
    _spawn([_explorer(), folder])
    logger.info("ℹ️ opened a folder in File Explorer")


def open_item(path: str, verb: str) -> None:
    """Run ``verb`` on ``path``: ``explore`` a folder, or ``open``, ``edit`` or ``reveal`` a file.

    Raises ``ValueError`` for any other verb, ``FileNotFoundError`` when nothing
    can be opened here, and ``OSError`` when Windows has no such action for the file.
    """
    if verb == "explore":
        open_in_explorer(path)
    elif verb == "reveal":
        # explorer.exe parses its own command line: /select, then the quoted path.
        _spawn('"%s" /select,"%s"' % (_explorer(), path))
        logger.info("ℹ️ showed a file in File Explorer")
    elif verb in ("open", "edit"):
        _shell(path, verb)
        logger.info("ℹ️ asked Windows to %s a %s file", verb, os.path.splitext(path)[1].lower() or "typeless")
    else:
        raise ValueError("unknown action: " + verb)
