"""The tray's region screenshot: one Tk thread that owns the overlay, the hotkey, and the settings dialogs.

The tray builds one :class:`SnapService` and calls :meth:`~SnapService.start`; every Tk call happens on
the ``snap-tk`` thread it starts, and the tray's menu only posts requests to it (``ask_prefix``,
``ask_folder``) or writes a setting (``set_setting``). The settings live in the ``snap`` block of
``config.json`` and are read at each capture, so a change applies from the next one; the hotkey is
registered once, at start.
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
from typing import Any, Callable, Optional

from src import config
from src.snap import settings
from src.snap.core import ConfigError, validate_prefix
from src.snap.folder import open_save_folder
from src.snap.hotkey import HotkeyThread, make_thread_dpi_aware
from src.snap.overlay import CaptureOverlay

logger = logging.getLogger(__name__)

START_TIMEOUT_S = 10.0


class SnapService:
    """Starts and stops the screenshot hotkey; ``error`` says why it is not running, or is None."""

    def __init__(self, on_change: Optional[Callable[[], None]] = None) -> None:
        # Called after a setting is saved, so the tray can redraw the checks in its menu.
        self.on_change = on_change
        self.error: Optional[str] = None
        self._write_lock = threading.Lock()
        self._started = threading.Event()
        self._root: Optional[tk.Tk] = None
        self._overlay: Optional[CaptureOverlay] = None
        self._hotkey: Optional[HotkeyThread] = None
        self._press_t0 = 0.0

    def current(self) -> settings.SnapConfig:
        """The settings as ``config.json`` holds them now."""
        return settings.resolve(config.load()["snap"])

    def start(self) -> Optional[str]:
        """Start the Tk thread and register the hotkey; None when the screenshot is ready, else why it is not."""
        threading.Thread(target=self._run, name="snap-tk", daemon=True).start()
        if not self._started.wait(START_TIMEOUT_S) and self.error is None:
            self.error = "the screenshot did not start in time"
            logger.error("❌ snap: %s", self.error)
        return self.error

    def stop(self) -> None:
        logger.info("ℹ️ snap: stopping")
        if self._hotkey is not None:
            self._hotkey.stop()
        self._post("<<SnapQuit>>")

    def set_setting(self, key: str, value: Any) -> None:
        """Write one setting to ``config.json``; a bad value or file is reported, never half-applied."""
        with self._write_lock:
            try:
                config.save_snap({key: value})
            except (ValueError, OSError) as exc:
                logger.error("❌ snap: could not save %s: %s", key, exc)
                return
        logger.info("ℹ️ snap: setting %s = %s saved", key, value)
        if self.on_change is not None:
            self.on_change()

    def ask_prefix(self) -> None:
        self._post("<<SnapPrefix>>")

    def ask_folder(self) -> None:
        self._post("<<SnapFolder>>")

    def open_folder(self) -> None:
        """Show the save folder in File Explorer, making it first when no capture has yet."""
        folder = self.current().folder
        try:
            open_save_folder(folder)
        except (OSError, ValueError) as exc:
            logger.error("❌ snap: could not open %s: %s", folder, exc)

    def _post(self, event: str) -> None:
        if self._root is not None:
            self._root.event_generate(event, when="tail")

    def _on_press(self, pressed_at: float) -> None:
        self._press_t0 = pressed_at
        self._post("<<SnapHotkey>>")

    def _run(self) -> None:
        if not make_thread_dpi_aware():
            logger.warning("⚠️ snap: no per-thread DPI context; on a scaled display the crop may be off")
        try:
            root = self._build()
        except Exception:  # noqa: BLE001 - the tray outlives a screenshot that cannot start
            self.error = "the overlay could not be built"
            logger.exception("❌ snap: %s", self.error)
            self._started.set()
            return
        config_now = self.current()
        self._hotkey = HotkeyThread(config_now.hotkey, self._on_press)
        self.error = self._hotkey.start()
        if self.error is None:
            logger.info("✅ snap: ready: %s -> %s (quality %d, clipboard %s)", config_now.hotkey, config_now.folder,
                        config_now.jpeg_quality, config_now.copy_to_clipboard)
        self._started.set()
        try:
            root.mainloop()
        finally:
            root.destroy()

    def _build(self) -> tk.Tk:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)  # dialogs opened from the tray must not land behind other windows
        overlay = CaptureOverlay(root, self.current, self.set_setting)
        root.bind("<<SnapHotkey>>", lambda _event: overlay.begin(self._press_t0))
        root.bind("<<SnapQuit>>", lambda _event: root.quit())
        root.bind("<<SnapPrefix>>", lambda _event: self._ask_prefix())
        root.bind("<<SnapFolder>>", lambda _event: self._ask_folder())
        self._root, self._overlay = root, overlay
        return root

    def _modal(self, ask: Callable[[], Any]) -> Any:
        """Run a dialog on the Tk thread with the hotkey held off, so a capture cannot start underneath it."""
        if self._overlay is None or self._overlay.in_use:
            return None
        self._overlay.in_use = True
        try:
            return ask()
        finally:
            self._overlay.in_use = False

    def _ask_prefix(self) -> None:
        def ask() -> Optional[str]:
            current = self.current().file_prefix
            while True:
                answer = simpledialog.askstring(
                    "Snap", "Filename prefix (files are named <prefix>_date_time.jpg):", initialvalue=current, parent=self._root
                )
                if answer is None:
                    return None
                try:
                    return validate_prefix(answer)
                except ConfigError as exc:
                    messagebox.showerror("Snap", str(exc), parent=self._root)
                    current = answer

        prefix = self._modal(ask)
        if prefix is not None:
            self.set_setting("file_prefix", prefix)

    def _ask_folder(self) -> None:
        chosen = self._modal(
            lambda: filedialog.askdirectory(parent=self._root, title="Snap save folder",
                                            initialdir=str(self.current().folder), mustexist=False)
        )
        if chosen:
            self.set_setting("folder", str(Path(chosen)))
