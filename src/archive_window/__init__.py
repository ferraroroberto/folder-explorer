"""The Archive window the Outlook macro opens when no app page is open: the sheet, in Tk.

It shows the document the Archive sheet shows (``model.py`` holds the shared rules,
``theme.py`` the app's tokens, ``view.py`` the widgets). Filing goes through the
caller's ``on_file``, which runs in this same process and COM apartment.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from src.archive_window.theme import palette

logger = logging.getLogger(__name__)


def show_window(doc: dict[str, Any], on_file: Callable[[str, Optional[str], bool, str], dict[str, Any]],
                find_folders: Optional[Callable[[str], list[dict[str, Any]]]] = None, dark: Optional[bool] = None) -> None:
    """Show the window until it is closed or the mail is filed.

    ``on_file(folder, category, duplicate, date_prefix)`` files the mail. ``find_folders(query)`` turns on the
    "Another email folder" search (hits are ``{"path", "name"}``); ``dark`` forces a theme, ``None`` follows Windows.
    """
    from src.archive_window.view import ArchiveView  # noqa: PLC0415 - tkinter only when a window opens

    window = ArchiveView(doc, on_file, find_folders, palette(dark))
    window.build()
    logger.info("ℹ️ archive: the fallback window is open (%s)", "dark" if window.pal.dark else "light")
    window.root.mainloop()
