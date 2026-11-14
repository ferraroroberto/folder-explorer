"""The selection overlay: a frozen copy of the screen, a drag rectangle, the loupe, and the save.

Everything here runs on the Tk thread of :class:`src.snap.service.SnapService`. The overlay window is
built once, borderless and top-most, so a hotkey only grabs the virtual screen with ``mss`` and paints
it. The loupe is drawn from the frame already grabbed, so showing it never grabs the screen again.
"""

from __future__ import annotations

import ctypes
import io
import logging
import time
import tkinter as tk
from datetime import datetime
from typing import Any, Callable, List, Optional, Tuple

import mss
from PIL import Image, ImageTk

from src.atomic_write import atomic_write_bytes
from src.runtime_files import make_dirs
from src.snap.clipboard import copy_image_to_clipboard
from src.snap.core import (
    ARROW_KEYS,
    LOUPE_CELLS,
    LOUPE_CURSOR_GAP,
    LOUPE_IMAGE_SIDE,
    LOUPE_TEXT_HEIGHT,
    LOUPE_ZOOM,
    Rect,
    loupe_label,
    loupe_source_box,
    monitor_for,
    nudge_point,
    place_loupe,
    screen_to_image,
    selection_rect,
    selection_size,
    unique_path,
    window_geometry,
)
from src.snap.settings import SnapConfig

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32


class Loupe:
    """Magnifier drawn on the overlay canvas from the frame already captured."""

    def __init__(self, canvas: tk.Canvas) -> None:
        self._canvas = canvas
        self._photo = ImageTk.PhotoImage(Image.new("RGB", (LOUPE_IMAGE_SIDE, LOUPE_IMAGE_SIDE)))
        total_height = LOUPE_IMAGE_SIDE + LOUPE_TEXT_HEIGHT
        self._size = (LOUPE_IMAGE_SIDE, total_height)
        hidden = {"state": "hidden"}
        self._panel = canvas.create_rectangle(0, 0, LOUPE_IMAGE_SIDE, total_height, fill="#111827", outline="white", **hidden)
        self._picture = canvas.create_image(0, 0, anchor="nw", image=self._photo, **hidden)
        self._hline = canvas.create_line(0, 0, 0, 0, fill="#ff3b30", **hidden)
        self._vline = canvas.create_line(0, 0, 0, 0, fill="#ff3b30", **hidden)
        self._cell = canvas.create_rectangle(0, 0, 0, 0, outline="#ff3b30", width=2, **hidden)
        self._text = canvas.create_text(0, 0, anchor="w", fill="white", font=("Consolas", 10), **hidden)
        self._items = (self._panel, self._picture, self._hline, self._vline, self._cell, self._text)

    def hide(self) -> None:
        for item in self._items:
            self._canvas.itemconfigure(item, state="hidden")

    def show(self, frozen: Image.Image, center: Tuple[int, int], label: str, monitor: Rect) -> None:
        """Repaint around ``center`` (image pixels) and place the box on ``monitor``, flipped away from its edges."""
        tile = frozen.crop(tuple(loupe_source_box(center)))
        self._photo.paste(tile.resize((LOUPE_IMAGE_SIDE, LOUPE_IMAGE_SIDE), Image.Resampling.NEAREST))
        left, top = place_loupe(center, self._size, monitor, LOUPE_CURSOR_GAP)
        side = LOUPE_IMAGE_SIDE
        middle = (LOUPE_CELLS // 2) * LOUPE_ZOOM
        coords = self._canvas.coords
        coords(self._panel, left, top, left + side, top + self._size[1])
        coords(self._picture, left, top)
        coords(self._hline, left, top + middle + LOUPE_ZOOM // 2, left + side, top + middle + LOUPE_ZOOM // 2)
        coords(self._vline, left + middle + LOUPE_ZOOM // 2, top, left + middle + LOUPE_ZOOM // 2, top + side)
        coords(self._cell, left + middle, top + middle, left + middle + LOUPE_ZOOM, top + middle + LOUPE_ZOOM)
        coords(self._text, left + 6, top + side + LOUPE_TEXT_HEIGHT // 2)
        self._canvas.itemconfigure(self._text, text=label)
        for item in self._items:
            self._canvas.itemconfigure(item, state="normal")


class CaptureOverlay:
    """One capture at a time: ``begin`` freezes the screen and shows the overlay, a release saves, Esc or a right-click cancels."""

    def __init__(self, root: tk.Tk, get_config: Callable[[], SnapConfig], set_setting: Callable[[str, Any], None]) -> None:
        self._get_config = get_config
        self._set_setting = set_setting
        self.config = get_config()
        self.in_use = False
        self.overlay = tk.Toplevel(root)
        self.overlay.withdraw()
        self.overlay.overrideredirect(True)
        self.overlay.attributes("-topmost", True)
        self.canvas = tk.Canvas(self.overlay, highlightthickness=0, bd=0, cursor="crosshair", bg="black")
        self.canvas.pack(fill="both", expand=True)
        self._image_item = self.canvas.create_image(0, 0, anchor="nw")
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._frozen: Optional[Image.Image] = None
        self._origin = (0, 0)
        self._drag_start: Optional[Tuple[int, int]] = None
        self._rect_items: Tuple[int, int] = (
            self.canvas.create_rectangle(0, 0, 0, 0, outline="white", width=1, state="hidden"),
            self.canvas.create_rectangle(0, 0, 0, 0, outline="black", dash=(4, 4), width=1, state="hidden"),
        )
        self.loupe = Loupe(self.canvas)
        self._monitors: List[Rect] = []
        self._t0 = 0.0
        self._bind()

    def _bind(self) -> None:
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        # <Motion> also fires while the button is held, so one handler serves hover and drag.
        self.canvas.bind("<Motion>", lambda event: self._pointer_moved(event.x_root, event.y_root))
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", lambda _event: self._cancel("right-click"))
        self.overlay.bind("<Escape>", lambda _event: self._cancel("Esc"))
        self.overlay.bind("<F6>", lambda _event: self._toggle_loupe())
        for key in ARROW_KEYS:
            self.overlay.bind(f"<{key}>", lambda _event, key=key: self._nudge(key, fast=False))
            self.overlay.bind(f"<Shift-{key}>", lambda _event, key=key: self._nudge(key, fast=True))

    def begin(self, t0: float) -> None:
        """Freeze the screen and show the overlay; ``t0`` is when the hotkey was pressed, for the logged timings."""
        if self.in_use:
            return
        self.in_use = True
        self._t0 = t0 or time.perf_counter()
        try:
            with mss.MSS() as grabber:
                virtual = grabber.monitors[0]
                shot = grabber.grab(virtual)
                screens = grabber.monitors[1:] or [virtual]
            t_grab = time.perf_counter()
            frozen = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            self._frozen = frozen
            self._origin = (virtual["left"], virtual["top"])
            self._monitors = [
                Rect(
                    screen["left"] - virtual["left"],
                    screen["top"] - virtual["top"],
                    screen["left"] - virtual["left"] + screen["width"],
                    screen["top"] - virtual["top"] + screen["height"],
                )
                for screen in screens
            ]
            self._photo = ImageTk.PhotoImage(frozen)
            self.canvas.itemconfigure(self._image_item, image=self._photo)
            for item in self._rect_items:
                self.canvas.itemconfigure(item, state="hidden")
            self.loupe.hide()
            self.overlay.geometry(window_geometry(virtual["left"], virtual["top"], shot.width, shot.height))
            self.overlay.deiconify()
            self.overlay.update()
            self.overlay.focus_force()
            t_overlay = time.perf_counter()
        except Exception:  # noqa: BLE001 - a failed grab must leave the hotkey usable for the next press
            self.in_use = False
            logger.exception("❌ snap: capture failed")
            return
        logger.info(
            "ℹ️ snap: overlay up: %dx%d at %+d%+d, grab %.0f ms, hotkey-to-overlay %.0f ms",
            shot.width, shot.height, virtual["left"], virtual["top"],
            (t_grab - self._t0) * 1000, (t_overlay - self._t0) * 1000,
        )
        if (self.overlay.winfo_rootx(), self.overlay.winfo_rooty()) != self._origin:
            logger.warning(
                "⚠️ snap: overlay sits at %d,%d instead of %s; the crop follows screen coordinates, so it stays correct",
                self.overlay.winfo_rootx(), self.overlay.winfo_rooty(), self._origin,
            )
        # The settings are read and the loupe painted after the hotkey-to-overlay time is taken, so neither adds to the logged figure.
        self.config = self._get_config()
        t_loupe = time.perf_counter()
        self._pointer_moved(*self.overlay.winfo_pointerxy())
        logger.info("ℹ️ snap: first loupe paint %.1f ms after the overlay", (time.perf_counter() - t_loupe) * 1000)

    def _image_point(self, event: tk.Event) -> Tuple[int, int]:
        return screen_to_image(event.x_root, event.y_root, self._origin)

    def _on_press(self, event: tk.Event) -> None:
        self._drag_start = self._image_point(event)

    def _pointer_moved(self, screen_x: int, screen_y: int) -> None:
        """Follow the pointer (moved by the mouse or by an arrow key): stretch the drag rectangle and repaint the loupe."""
        if self._frozen is None:
            return
        point = screen_to_image(screen_x, screen_y, self._origin)
        if self._drag_start is not None:
            x0, y0 = self._drag_start
            for item in self._rect_items:
                self.canvas.coords(item, x0, y0, point[0], point[1])
                self.canvas.itemconfigure(item, state="normal")
        if not self.config.show_loupe:
            return
        size = None
        if self._drag_start is not None:
            size = selection_size(self._drag_start, point, self._frozen.size)
        self.loupe.show(self._frozen, point, loupe_label((screen_x, screen_y), size), monitor_for(point, self._monitors))

    def _screen_bounds(self) -> Rect:
        width, height = self._frozen.size
        return Rect(self._origin[0], self._origin[1], self._origin[0] + width, self._origin[1] + height)

    def _nudge(self, key: str, fast: bool) -> None:
        """Move the real cursor one pixel (ten with Shift), so an edge can be placed exactly before or during a drag."""
        if self._frozen is None:
            return
        target = nudge_point(self.overlay.winfo_pointerxy(), key, fast, self._screen_bounds())
        user32.SetCursorPos(target[0], target[1])
        self._pointer_moved(*target)

    def _toggle_loupe(self) -> None:
        if self._frozen is None:
            return
        shown = not self.config.show_loupe
        self._set_setting("show_loupe", shown)
        self.config = self._get_config()
        if self.config.show_loupe:
            self._pointer_moved(*self.overlay.winfo_pointerxy())
        else:
            self.loupe.hide()

    def _on_release(self, event: tk.Event) -> None:
        if self._drag_start is None or self._frozen is None:
            return
        rect = selection_rect(self._drag_start, self._image_point(event), self._frozen.size)
        if rect is None:
            self._cancel("selection too small")
            return
        t_release = time.perf_counter()
        self._close_overlay()
        crop = self._frozen.crop(rect)
        try:
            data = io.BytesIO()
            crop.save(data, "JPEG", quality=self.config.jpeg_quality)
            make_dirs(self.config.folder)
            path = unique_path(self.config.folder, datetime.now(), self.config.file_prefix)
            atomic_write_bytes(path, data.getvalue())
        except Exception:  # noqa: BLE001 - an unwritable folder must not leave the overlay state stuck
            logger.exception("❌ snap: saving the capture failed")
            self._finish()
            return
        copied = copy_image_to_clipboard(crop) if self.config.copy_to_clipboard else None
        if copied is False:
            logger.warning("⚠️ snap: clipboard stayed locked by another app; the file was saved anyway")
        logger.info(
            "✅ snap: saved %s (%dx%d, %d bytes), clipboard=%s, release-to-file %.0f ms, hotkey-to-file %.0f ms",
            path, crop.width, crop.height, path.stat().st_size, copied,
            (time.perf_counter() - t_release) * 1000, (time.perf_counter() - self._t0) * 1000,
        )
        self._finish()

    def _cancel(self, reason: str) -> None:
        if not self.in_use:
            return
        logger.info("ℹ️ snap: cancelled (%s); nothing saved", reason)
        self._close_overlay()
        self._finish()

    def _close_overlay(self) -> None:
        self.overlay.withdraw()
        self.overlay.update_idletasks()

    def _finish(self) -> None:
        self._drag_start = None
        self._frozen = None
        self.in_use = False
