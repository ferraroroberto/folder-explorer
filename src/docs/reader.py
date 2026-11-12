"""The only reader of a document's bytes: a Markdown file, or an image beside one.

Every read is checked first, in this order, against the item's fresh listing
(its folder read through the directory handle in ``src/winfs.py``, which
opens no file): the extension, a link (never followed), the size cap, and the
cloud state. A file whose data is only in OneDrive is refused unless the
caller is the one "Download and open" tap. The open itself asks Windows not
to recall the data (``FILE_FLAG_OPEN_NO_RECALL``), so a placeholder the
listing missed fails instead of downloading. At most one byte past the cap is
read, so a file that grew since the listing is refused too.

Callers name the file by a path they rebuilt from the scan index, never one a
client sent; ``src.docs.locate`` keeps a link inside its root.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

from src import winfs

logger = logging.getLogger(__name__)

DOC_EXTS = frozenset({".md", ".markdown"})
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp", ".svg": "image/svg+xml"}
# A long handbook is well under 2 MB of Markdown; past that it is generated output, not something to read here.
DOC_MAX_BYTES = 2 * 1024 * 1024
# A photo straight off a phone is a few MB; 10 MB keeps one document's images from stalling the phone over the tailnet.
IMAGE_MAX_BYTES = 10 * 1024 * 1024

REASON_MESSAGES = {
    "not_document": "Only Markdown files and the images beside them open here",
    "too_large": "Too large to show here",
    "cloud_only": "Only in OneDrive",
    "gone": "It is no longer there",
    "unreadable": "Windows could not read it",
    # The writer's refusals (src/docs/writer.py), keyed the same way so the page has one vocabulary.
    "not_utf8": "Not UTF-8 text, so it opens read-only",
    "edit_too_large": "Too large to edit here",
    "not_writable": "Windows would not let it be saved",
}


class Refused(Exception):
    """A read the viewer refuses, with its reason code and the line the page shows."""

    def __init__(self, reason: str) -> None:
        super().__init__(REASON_MESSAGES[reason])
        self.reason = reason
        self.message = REASON_MESSAGES[reason]


def is_document(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in DOC_EXTS


def is_image(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in IMAGE_TYPES


def response_headers(ext: str) -> dict[str, str]:
    """An image's headers: its type, no sniffing, and a sandbox so an SVG opened on its own runs nothing."""
    return {"Content-Type": IMAGE_TYPES[ext.lower()], "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
            "Cache-Control": "private, no-cache"}


def _listed(path: str) -> Optional[winfs.Entry]:
    """The file's record in a fresh listing of its folder, or None when the folder or the file is gone."""
    folder, name = os.path.split(path)
    try:
        return next((e for e in winfs.list_dir(folder) if e.name.lower() == name.lower()), None)
    except OSError:
        return None


def find_document(folder: str, name: str) -> Optional[winfs.Entry]:
    """A Markdown file called ``name`` in a fresh listing of ``folder``: what a scan missed, found without opening it.

    None when the folder or the file is gone, or the entry is a folder, a link or not Markdown.
    """
    if not is_document(name) or name != os.path.basename(name):
        return None
    entry = _listed(os.path.join(folder, name))
    return entry if entry is not None and not entry.is_dir and not winfs.is_link(entry) else None


if sys.platform == "win32":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]

    _GENERIC_READ = 0x80000000
    _FILE_SHARE_ALL = 0x7
    _OPEN_EXISTING = 3
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_FLAG_OPEN_NO_RECALL = 0x00100000
    _INVALID_HANDLE = ctypes.c_void_p(-1).value

    def _open_read(path: str, limit: int, recall: bool) -> bytes:
        """Up to ``limit`` bytes of ``path``; without ``recall`` a cloud-only file fails rather than downloads."""
        flags = _FILE_FLAG_OPEN_REPARSE_POINT | (0 if recall else _FILE_FLAG_OPEN_NO_RECALL)
        handle = _k32.CreateFileW(winfs.extended_path(path), _GENERIC_READ, _FILE_SHARE_ALL, None, _OPEN_EXISTING, flags, None)
        if handle is None or handle == _INVALID_HANDLE:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except OSError:
            _k32.CloseHandle(handle)
            raise
        with os.fdopen(fd, "rb") as f:
            return f.read(limit)

else:

    def _open_read(path: str, limit: int, recall: bool) -> bytes:
        """Up to ``limit`` bytes of ``path``, refusing a symlink; no cloud placeholders exist off Windows."""
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as f:
            return f.read(limit)


def _read(path: str, cap: int, index_online: bool, download: bool) -> tuple[bytes, int]:
    entry = _listed(path)
    if entry is None or entry.is_dir:
        raise Refused("gone")
    if winfs.is_link(entry):
        raise Refused("not_document")
    if entry.size > cap:
        raise Refused("too_large")
    online = winfs.is_online_only(entry.attrs)
    if index_online != online:
        logger.info("ℹ️ doc: the index and a fresh listing disagree on a %s file's cloud state; the listing decides",
                    os.path.splitext(path)[1].lower())
    if online and not download:
        raise Refused("cloud_only")
    try:
        data = _open_read(path, cap + 1, recall=download)
    except FileNotFoundError as exc:
        raise Refused("gone") from exc
    except OSError as exc:
        # The listing said the data is here; a failure now (a placeholder that turned cloud-only, a lock) is logged as itself.
        logger.warning("⚠️ doc: read failed after a local listing: %s", exc)
        raise Refused("unreadable") from exc
    if len(data) > cap:
        raise Refused("too_large")
    if download:
        logger.info("ℹ️ doc: downloaded one %s file on request (%d bytes)", os.path.splitext(path)[1].lower(), len(data))
    return data, entry.mtime


def read_document(path: str, index_online: bool, download: bool = False) -> bytes:
    """A Markdown file's bytes, up to :data:`DOC_MAX_BYTES`; raises :class:`Refused`.

    ``index_online`` is the scan's cloud flag for the file; a fresh listing of
    its folder decides, since the file may have been downloaded or freed since.
    ``download`` is the one tap that lets a cloud-only file come down.
    """
    return read_document_listed(path, index_online, download)[0]


def read_document_listed(path: str, index_online: bool, download: bool = False, cap: int = DOC_MAX_BYTES) -> tuple[bytes, int]:
    """A Markdown file's bytes and the modified time its fresh listing gave, up to ``cap`` bytes; the editor's read."""
    if not is_document(path):
        raise Refused("not_document")
    return _read(path, cap, index_online, download)


def read_image(path: str, index_online: bool, download: bool = False) -> bytes:
    """An image's bytes, up to :data:`IMAGE_MAX_BYTES`, under the same checks as :func:`read_document`."""
    if not is_image(path):
        raise Refused("not_document")
    return _read(path, IMAGE_MAX_BYTES, index_online, download)[0]
