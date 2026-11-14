"""The global hotkey: ``RegisterHotKey`` on a message-pump thread of its own, and the process's DPI context for the capture thread."""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes
from typing import Callable, Optional

from src.snap.core import MOD_NOREPEAT, parse_hotkey

logger = logging.getLogger(__name__)

HOTKEY_ID = 1
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
ERROR_HOTKEY_ALREADY_REGISTERED = 1409
REGISTER_TIMEOUT_S = 3.0
# DPI_AWARENESS_CONTEXT values: per-monitor v2, and unaware (the process default Python starts with).
_PER_MONITOR_V2 = -4
_UNAWARE = -1

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL


def make_thread_dpi_aware() -> bool:
    """Report physical pixels to Tk and the screen grab on the calling thread only; False when Windows has no per-thread context.

    The screen grabber switches the whole process to DPI aware on its first use, and that would
    resize the tray's other windows. Fixing the process default as unaware first makes that switch
    a no-op, so only the thread that calls this sees physical pixels (a scaled display would
    otherwise crop the wrong region).
    """
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(_UNAWARE))
        return bool(user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(_PER_MONITOR_V2)))
    except (AttributeError, OSError):
        return False


class HotkeyThread:
    """Registers ``hotkey`` and calls ``on_press(perf_counter at the press)`` on its own thread for every press."""

    def __init__(self, hotkey: str, on_press: Callable[[float], None]) -> None:
        self._hotkey = hotkey
        self._on_press = on_press
        self._thread_id = 0
        self._ready = threading.Event()
        self._error: Optional[str] = None

    def start(self) -> Optional[str]:
        """Register the hotkey; None when it is taken, else the reason it is not (already logged)."""
        threading.Thread(target=self._loop, name="snap-hotkey", daemon=True).start()
        if not self._ready.wait(REGISTER_TIMEOUT_S):
            self._error = "hotkey registration did not answer in time"
        if self._error:
            logger.error("❌ snap: %s", self._error)
        return self._error

    def stop(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def _loop(self) -> None:
        modifiers, key = parse_hotkey(self._hotkey)
        self._thread_id = kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, HOTKEY_ID, modifiers | MOD_NOREPEAT, key):
            code = ctypes.get_last_error() or kernel32.GetLastError()
            reason = (
                "is already registered by another application"
                if code == ERROR_HOTKEY_ALREADY_REGISTERED
                else f"could not be registered (Win32 error {code})"
            )
            self._error = f"hotkey {self._hotkey} {reason}; pick another in config.json (snap.hotkey)"
            self._ready.set()
            return
        self._ready.set()
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == WM_HOTKEY:
                self._on_press(time.perf_counter())
        user32.UnregisterHotKey(None, HOTKEY_ID)
