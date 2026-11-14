"""The Windows tray: owns the webapp process, watches it, and offers Quick search / Open / Rescan / Screenshot / Restart / Quit.

Started by ``tray.bat`` as ``pythonw -m app.tray``. One tray per session (a
named mutex); the webapp start retries with backoff, a dead webapp is
respawned, and a wedged one (listening, not answering) is only reported.
Every start, retry, respawn and recovery is written to ``watchdog.log`` in
the runtime-data folder, the one record a console-less tray leaves.

The region screenshot (``src.snap``) runs in this process on a Tk thread of its own, its settings in the
**Screenshot** submenu.

A left click runs the menu's default item, which ``tray.left_click`` picks at
click time: the Quick search window (``src.search_window``) or the web app.
"""

from __future__ import annotations

import io
import logging
import os
import sys
import threading
import webbrowser

# Under pythonw there is no console: anything that touches sys.stdout or
# sys.stderr would crash the tray, so bind them to a null sink first.
for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, io.TextIOWrapper(open(os.devnull, "wb"), encoding="utf-8"))

import pystray  # noqa: E402 -- after the stream rebind above
from PIL import Image  # noqa: E402

from app.tray.single_instance import SingleInstance  # noqa: E402
from app.tray.snap_menu import snap_menu_item  # noqa: E402
from app.tray.watchdog import BreadcrumbLog, HealthWatchdog, retry_with_backoff  # noqa: E402
from app.webapp.bind import resolve_bind  # noqa: E402
from app.webapp.manager import WebappManager  # noqa: E402
from src import config, scan_runner, store  # noqa: E402
from src.config import PROJECT_ROOT  # noqa: E402
from src.scan_lock import ScanBusy  # noqa: E402
from src.search_window import QuickSearch  # noqa: E402
from src.snap.service import SnapService  # noqa: E402

logger = logging.getLogger(__name__)

MUTEX_NAME = r"Local\folder-explorer-tray"


def left_click_searches() -> bool:
    """A left click opens Quick search (the default) rather than the web app; read per click, so Settings applies at once."""
    return config.load()["tray"]["left_click"] == "quick_search"


def local_base() -> str:
    """The web app's loopback address over the scheme its bind uses now, for the Quick search window's API calls."""
    return resolve_bind(config.load()).local_url


def main() -> int:
    log_dir = store.data_dir()
    logging.basicConfig(level=logging.INFO, filename=str(log_dir / "tray.log"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", encoding="utf-8")
    crumbs = BreadcrumbLog(log_dir / "watchdog.log")
    guard = SingleInstance(MUTEX_NAME)
    if not guard.acquired:
        crumbs("tray already running in this session; exiting")
        return 0
    manager = WebappManager()
    stop = threading.Event()

    def start_webapp() -> None:
        crumbs("webapp start attempt")
        manager.start()

    def respawn(failures: int) -> None:
        if manager.owns_running():
            crumbs("webapp listening but not answering /healthz after %d probes; leaving it running" % failures)
            return
        crumbs("webapp down after %d probes; respawning" % failures)
        try:
            manager.start()
            crumbs("webapp respawned")
        except Exception as exc:  # noqa: BLE001 -- the tray must outlive a failed respawn
            crumbs("webapp respawn failed: %s" % exc)
            watchdog.rearm()

    watchdog = HealthWatchdog(manager.healthy, respawn, lambda: crumbs("webapp recovered"))

    def boot() -> None:
        try:
            retry_with_backoff(start_webapp, on_attempt_failed=lambda n, e: crumbs("start attempt %d failed: %s" % (n, e)))
            crumbs("webapp started")
        except Exception as exc:  # noqa: BLE001 -- recorded; the watchdog keeps trying
            crumbs("webapp failed to start after every retry: %s" % exc)
        watchdog.run(stop)

    quick = QuickSearch(local_base)
    snap = SnapService()

    def on_open(icon: "pystray.Icon", item: object) -> None:
        webbrowser.open(manager.url)

    def on_quick_search(icon: "pystray.Icon", item: object) -> None:
        quick.show()

    def on_rescan(icon: "pystray.Icon", item: object) -> None:
        try:
            scan_runner.start_background(None, "tray")
            icon.notify("Scanning every root in the background.", "Folder explorer")
        except ScanBusy:
            icon.notify("A scan is already running.", "Folder explorer")

    def on_restart(icon: "pystray.Icon", item: object) -> None:
        crumbs("webapp restart from the tray menu")
        try:
            manager.restart()
        except Exception as exc:  # noqa: BLE001
            crumbs("restart failed: %s" % exc)

    def on_quit(icon: "pystray.Icon", item: object) -> None:
        stop.set()
        snap.stop()
        manager.stop()
        icon.stop()

    threading.Thread(target=boot, name="webapp-boot", daemon=True).start()
    # The screenshot's Tk thread and hotkey come up beside the tray icon, so a slow start never delays it.
    threading.Thread(target=snap.start, name="snap-start", daemon=True).start()
    icon = pystray.Icon(
        "folder-explorer",
        Image.open(PROJECT_ROOT / "assets" / "tray" / "folder-explorer.ico"),
        "Folder explorer",
        menu=pystray.Menu(
            pystray.MenuItem("Quick search", on_quick_search, default=lambda _item: left_click_searches()),
            pystray.MenuItem("Open", on_open, default=lambda _item: not left_click_searches()),
            pystray.MenuItem("Rescan now", on_rescan),
            snap_menu_item(snap),
            pystray.MenuItem("Restart web app", on_restart),
            pystray.MenuItem("Quit", on_quit),
        ),
    )
    snap.on_change = icon.update_menu
    icon.run()
    guard.release()
    return 0
