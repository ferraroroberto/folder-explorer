"""The work area of the monitor the Archive window sits on: the screen minus the taskbar, in the pixels Tk positions in.

Plain Windows calls, no file is touched. Anywhere they fail, or off Windows, the whole screen stands in.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import tkinter as tk
from ctypes import wintypes

logger = logging.getLogger(__name__)

Rect = tuple[int, int, int, int]

# MonitorFrom*'s "the nearest monitor if none holds it".
_NEAREST = 2


class _Point(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _Rect(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("monitor", _Rect), ("work", _Rect), ("flags", wintypes.DWORD)]


def _monitor_work(root: tk.Misc) -> Rect:
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    user32.MonitorFromPoint.argtypes = [_Point, wintypes.DWORD]
    user32.MonitorFromPoint.restype = wintypes.HANDLE
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MonitorInfo)]
    if root.winfo_viewable():
        monitor = user32.MonitorFromWindow(root.winfo_id(), _NEAREST)
    else:
        # Not on screen yet: the monitor the user is working on, where the pointer is.
        point = _Point()
        user32.GetCursorPos(ctypes.byref(point))
        monitor = user32.MonitorFromPoint(point, _NEAREST)
    info = _MonitorInfo()
    info.size = ctypes.sizeof(_MonitorInfo)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        raise OSError("GetMonitorInfoW failed")
    return info.work.left, info.work.top, info.work.right, info.work.bottom


def work_area(root: tk.Misc) -> Rect:
    """``(left, top, right, bottom)`` of the monitor ``root`` is on, or the one the pointer is on while it is not shown."""
    if sys.platform == "win32":
        try:
            return _monitor_work(root)
        except (OSError, AttributeError, tk.TclError):
            logger.warning("⚠️ archive window: the monitor's work area couldn't be read; using the whole screen", exc_info=True)
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()
