"""Showing the screenshot save folder in File Explorer: the tray menu and Settings both open it this way."""

from __future__ import annotations

from pathlib import Path

from src import desktop
from src.runtime_files import make_dirs


def open_save_folder(folder: Path) -> None:
    """Make ``folder`` when no capture has yet, then show it in File Explorer.

    Raises ``OSError`` or ``ValueError`` when the folder cannot be made (``make_dirs`` refuses one under the code),
    and ``desktop.GoneError`` or ``FileNotFoundError`` when Windows cannot show it.
    """
    make_dirs(folder)
    desktop.open_in_explorer(str(folder))
