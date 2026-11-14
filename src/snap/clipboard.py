"""Put a captured image on the Windows clipboard."""

from __future__ import annotations

import io
import time

from PIL import Image

CF_DIB = 8
CLIPBOARD_ATTEMPTS = 5
CLIPBOARD_RETRY_S = 0.02


def copy_image_to_clipboard(image: Image.Image) -> bool:
    """Put ``image`` on the clipboard as a DIB; False when another app kept the clipboard open through every attempt."""
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
            return True
        finally:
            win32clipboard.CloseClipboard()
    return False
