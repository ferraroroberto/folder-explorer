"""Put a captured image, and optionally its saved file, on the Windows clipboard."""

from __future__ import annotations

import io
import os
import struct
import time
from pathlib import Path
from typing import Optional

from PIL import Image

CF_DIB = 8
CF_HDROP = 15
# The shell's name for what a paste does with the dropped files: copy, so pasting never moves the capture.
DROP_EFFECT_FORMAT = "Preferred DropEffect"
DROPEFFECT_COPY = 1
CLIPBOARD_ATTEMPTS = 5
CLIPBOARD_RETRY_S = 0.02


def _file_drop(file: Path) -> bytes:
    """A ``DROPFILES`` block naming one file: the 20-byte header (list offset, drop point, non-client flag, wide flag), then the double-null-terminated UTF-16 list."""
    return struct.pack("<IiiII", 20, 0, 0, 0, 1) + (os.path.abspath(file) + "\0\0").encode("utf-16-le")


def copy_image_to_clipboard(image: Image.Image, file: Optional[Path] = None) -> bool:
    """Put ``image`` on the clipboard as a DIB, and ``file`` as a file to paste; False when another app kept the clipboard open through every attempt.

    Both are written in one open, and each app takes the format it understands: File Explorer pastes a copy of
    the file, an image editor or a chat box pastes the image.
    """
    import win32clipboard  # noqa: PLC0415 - pywin32 only when a capture copies

    buffer = io.BytesIO()
    image.save(buffer, "BMP")
    dib = buffer.getvalue()[14:]  # the 14-byte BMP file header is not part of a DIB
    for _ in range(CLIPBOARD_ATTEMPTS):
        try:
            win32clipboard.OpenClipboard()
        except Exception:  # noqa: BLE001 - pywintypes.error: another process owns the clipboard right now
            time.sleep(CLIPBOARD_RETRY_S)
            continue
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(CF_DIB, dib)
            if file is not None:
                win32clipboard.SetClipboardData(CF_HDROP, _file_drop(file))
                effect = win32clipboard.RegisterClipboardFormat(DROP_EFFECT_FORMAT)
                win32clipboard.SetClipboardData(effect, struct.pack("<I", DROPEFFECT_COPY))
            return True
        finally:
            win32clipboard.CloseClipboard()
    return False
