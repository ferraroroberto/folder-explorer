"""The tray's **Screenshot** submenu: the region screenshot's settings, each saved to ``config.json`` and used from the next capture."""

from __future__ import annotations

from typing import Callable

import pystray

from src.snap.core import QUALITY_CHOICES
from src.snap.service import SnapService


def snap_menu_item(snap: SnapService) -> pystray.MenuItem:
    """The submenu: clipboard, JPEG quality, filename prefix, loupe, then the save folder; a hotkey that could not be taken shows first."""

    def pick_quality(quality: int) -> Callable[[], None]:
        return lambda: snap.set_setting("jpeg_quality", quality)

    def qualities() -> pystray.Menu:
        return pystray.Menu(
            *(
                pystray.MenuItem(
                    str(quality),
                    pick_quality(quality),
                    checked=lambda _item, quality=quality: snap.current().jpeg_quality == quality,
                    radio=True,
                )
                for quality in QUALITY_CHOICES
            )
        )

    menu = pystray.Menu(
        pystray.MenuItem(lambda _item: f"Not running: {snap.error}", None, enabled=False, visible=lambda _item: snap.error is not None),
        pystray.MenuItem(
            "Copy to clipboard on save",
            lambda: snap.set_setting("copy_to_clipboard", not snap.current().copy_to_clipboard),
            checked=lambda _item: snap.current().copy_to_clipboard,
        ),
        pystray.MenuItem(
            "Show loupe (F6)",
            lambda: snap.set_setting("show_loupe", not snap.current().show_loupe),
            checked=lambda _item: snap.current().show_loupe,
        ),
        pystray.MenuItem("JPEG quality", qualities()),
        pystray.MenuItem(lambda _item: f"Filename prefix: {snap.current().file_prefix}...", lambda: snap.ask_prefix()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open folder", lambda: snap.open_folder()),
        pystray.MenuItem("Change folder...", lambda: snap.ask_folder()),
    )
    return pystray.MenuItem(lambda _item: f"Screenshot ({snap.current().hotkey})", menu)
