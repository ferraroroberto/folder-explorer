"""The tray's **Screenshot** submenu: the region screenshot's settings, each saved to ``config.json`` and used from the next capture."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

import pystray

from src import config
from src.snap.core import QUALITY_CHOICES
from src.snap.service import SnapService

logger = logging.getLogger(__name__)

# How often the tray looks at config.json for a change made elsewhere (Settings in the web app).
WATCH_INTERVAL_S = 2.0


def _stamp(path: Path) -> int:
    """The file's modified time in ns, 0 when it is missing."""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def refresh_when_config_changes(refresh: Callable[[], None], stop: threading.Event, interval: float = WATCH_INTERVAL_S) -> None:
    """Call ``refresh`` whenever ``config.json`` changes, until ``stop`` is set, so a change made in Settings shows in the menu's checks.

    The menu draws its checks when it is rebuilt, not when it opens, so without this a setting changed on the web page would show stale here.
    """
    seen = _stamp(config.config_path())
    while not stop.wait(interval):
        now = _stamp(config.config_path())
        if now != seen:
            seen = now
            try:
                refresh()
            except Exception:  # noqa: BLE001 - a failed redraw must not end the watch
                logger.exception("❌ snap: menu refresh failed")


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
