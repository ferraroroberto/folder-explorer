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
                find_folders: Optional[Callable[[str], list[dict[str, Any]]]] = None, dark: Optional[bool] = None,
                current_explorer: Optional[Callable[[], dict[str, Any]]] = None,
                rank: Optional[Callable[[], list[dict[str, Any]]]] = None,
                progress: Optional[Callable[[str], None]] = None) -> None:
    """Show the window until it is closed or the mail is filed.

    ``on_file(folder, category, duplicate, date_prefix)`` files the mail. ``find_folders(query)`` turns on the
    "Another email folder" search (hits are ``{"path", "name"}``); ``dark`` forces a theme, ``None`` follows Windows.
    ``current_explorer()`` turns on the Explorer button: it answers ``{"ok": true, "folder_path"}`` or the sheet's error document.
    ``rank()`` gives the folders for a document still ranking: the window shows first and fills them in from a
    worker thread. ``progress(stage)`` hears ``window_shown`` and then ``suggestions_shown``, for the timings.
    """
    from src.archive_window.view import ArchiveView  # noqa: PLC0415 - tkinter only when a window opens

    window = ArchiveView(doc, on_file, find_folders, palette(dark), current_explorer=current_explorer)
    window.build()
    if progress:
        window.root.after_idle(lambda: progress("window_shown"))
    if rank is not None and window.model is not None:
        window.fill_in_background(rank, progress)
    logger.info("ℹ️ archive: the fallback window is open (%s)", "dark" if window.pal.dark else "light")
    window.root.mainloop()
