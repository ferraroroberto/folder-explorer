"""Open a mail in Outlook or a folder in File Explorer on this PC, without waiting for either.

Only the web routes that accept loopback callers use these: the phone gets
Copy path and Show folder instead. Outlook opens the archived ``.msg`` itself
(``outlook.exe /f``), never the live mail, whose id changes when it moves.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Optional

from src.no_window import NO_WINDOW

logger = logging.getLogger(__name__)

_OUTLOOK_APP_PATH = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\OUTLOOK.EXE"


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
    subprocess.Popen([exe, "/f", msg_path], creationflags=NO_WINDOW, close_fds=True)
    logger.info("ℹ️ opened a mail in Outlook: %s", os.path.basename(msg_path))


def open_in_explorer(folder: str) -> None:
    """Show ``folder`` in a File Explorer window."""
    if sys.platform != "win32":
        raise FileNotFoundError("File Explorer is only on Windows")
    explorer = os.path.join(os.environ.get("SystemRoot", os.environ.get("windir", "")), "explorer.exe")
    subprocess.Popen([explorer, folder], creationflags=NO_WINDOW, close_fds=True)
    logger.info("ℹ️ opened a folder in File Explorer")
