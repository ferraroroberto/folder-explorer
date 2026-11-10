"""The tray's Quick search window: folders and emails through the web app's API, in Tk.

``model.py`` holds what the window shows and does (no Tk), ``client.py`` the API calls,
``view.py`` the widgets; the colours come from ``src.archive_window.theme``. The tray
owns one :class:`QuickSearch`; its window runs on a Tk thread of its own, is built on
the first ``show()`` and only hidden after, so a second click brings the same window,
with its last tab and query, to the front.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import TYPE_CHECKING, Callable, Optional

from src.search_window.client import SearchClient

if TYPE_CHECKING:
    from src.search_window.model import QuickSearchModel

logger = logging.getLogger(__name__)

# How often the Tk thread looks for a show request from the tray's thread.
SHOW_POLL_MS = 100


class QuickSearch:
    """One window per tray. ``show()`` is safe from any thread."""

    def __init__(self, base: Callable[[], str], dark: Optional[bool] = None) -> None:
        self.client = SearchClient(base)
        self.dark = dark
        self._requests: queue.Queue[str] = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._model: Optional[QuickSearchModel] = None

    def show(self) -> None:
        """Open the window, or bring the open one to the front."""
        with self._lock:
            self._requests.put("show")
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="quick-search-tk", daemon=True)
                self._thread.start()

    def _run(self) -> None:
        # tkinter only on this thread: every Tk call below happens here, never on the tray's.
        from src.archive_window.theme import palette  # noqa: PLC0415 - tkinter only when a window opens
        from src.search_window.model import QuickSearchModel  # noqa: PLC0415
        from src.search_window.view import QuickSearchView  # noqa: PLC0415

        try:
            if self._model is None:
                self._model = QuickSearchModel(self.client)
            view = QuickSearchView(self._model, palette(self.dark))
            view.build()

            def poll() -> None:
                try:
                    while True:
                        self._requests.get_nowait()
                        view.show()
                except queue.Empty:
                    pass
                view.root.after(SHOW_POLL_MS, poll)

            poll()
            logger.info("ℹ️ quick search: the window is open (%s)", "dark" if view.pal.dark else "light")
            view.root.mainloop()
        except Exception:  # noqa: BLE001 - the tray outlives a broken window; the next click builds a new one
            logger.exception("❌ quick search: the window stopped")
