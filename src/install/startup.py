"""The installer's one machine-level registration: a shortcut to ``tray.bat`` in the user's Startup folder.

Opt-in (``scripts\\install.bat --startup``), because a PC may already start the tray some other way (a scheduler job, another
launcher), and a second start entry would race the first. Detection reads only the Startup folder's own entries:
any one that names this checkout's folder and ``tray.bat`` counts as present, whoever made it, and is reported kept.
``--uninstall`` removes only the shortcut this module names and only when it points at this checkout.

Standard library only, plus one ``powershell.exe -Command`` for the shortcut itself (the shell's ``WScript.Shell``
object writes ``.lnk`` files); a ``-Command`` string is not a script file, so no execution policy applies to it.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

from src.no_window import NO_WINDOW

POWERSHELL_EXE = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
SHORTCUT_NAME = "folder-explorer tray.lnk"
_ENTRY_SUFFIXES = (".lnk", ".bat", ".cmd", ".vbs", ".url")
_CREATE_TIMEOUT_S = 60

# Created with WindowStyle 7 (minimized): tray.bat returns once the tray is started, so its console only flashes.
_CREATE = (
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:FE_LNK); "
    "$s.TargetPath = $env:FE_TARGET; $s.WorkingDirectory = $env:FE_DIR; $s.WindowStyle = 7; $s.Save()"
)


def startup_dir(env: Mapping[str, str]) -> Path | None:
    """The user's Startup folder, or None when ``APPDATA`` is not set (not confirmed)."""
    appdata = env.get("APPDATA", "").strip()
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _names_checkout(data: bytes, root: Path) -> bool:
    # A .lnk stores its target as ANSI or UTF-16; a .bat/.cmd/.vbs as text. Both needles must appear in one encoding.
    needles = (root.name.lower(), "tray.bat")
    for text in (data.decode("latin-1"), data.decode("utf-16-le", "ignore")):
        low = text.lower()
        if all(n in low for n in needles):
            return True
    return False


def find_entries(folder: Path, root: Path) -> list[Path]:
    """The Startup entries that start this checkout's tray."""
    if not folder.is_dir():
        return []
    found = []
    for entry in sorted(folder.iterdir()):
        if entry.suffix.lower() not in _ENTRY_SUFFIXES or not entry.is_file():
            continue
        try:
            if _names_checkout(entry.read_bytes(), root):
                found.append(entry)
        except OSError:
            continue
    return found


def create_shortcut(folder: Path, root: Path) -> Path:
    """Write the Startup shortcut to this checkout's ``tray.bat``; raises ``RuntimeError`` when it was not written."""
    link = folder / SHORTCUT_NAME
    # runtime-write-exempt: the user's own Startup folder, made only when --startup asks for the shortcut
    folder.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "FE_LNK": str(link), "FE_TARGET": str(root / "tray.bat"), "FE_DIR": str(root)}
    done = subprocess.run([POWERSHELL_EXE, "-NoProfile", "-NonInteractive", "-Command", _CREATE], env=env,
                          capture_output=True, text=True, timeout=_CREATE_TIMEOUT_S, check=False,
                          creationflags=NO_WINDOW)
    if done.returncode != 0 or not link.is_file():
        raise RuntimeError(f"the Startup shortcut was not written (exit {done.returncode}): {done.stderr.strip()[:300]}")
    return link


def removable(folder: Path, root: Path) -> Path | None:
    """This module's own shortcut when it points at this checkout, else None (a foreign entry is never removed)."""
    link = folder / SHORTCUT_NAME
    return link if link in find_entries(folder, root) else None
