"""Metadata-only directory listing: one directory handle per folder, never a file open.

On Windows each folder is opened once with ``FILE_LIST_DIRECTORY`` and paged
with ``GetFileInformationByHandleEx(FileFullDirectoryInfo)``. Every record
carries the name, ``EndOfFile`` (the logical size, what the cloud stores),
``AllocationSize`` (the bytes on this disk), the attributes and the last
write time, so a scan reads both figures without opening a single file. That
is what keeps an online-only OneDrive placeholder online-only: nothing here
reads data, so the sync client has nothing to download.

Elsewhere (CI, a developer laptop) ``os.scandir`` + ``lstat`` gives the same
record shape, with ``st_blocks`` standing in for the allocation.
"""

from __future__ import annotations

import os
import stat
import struct
import sys
from collections.abc import Iterator
from dataclasses import dataclass

FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_PINNED = 0x80000
FILE_ATTRIBUTE_UNPINNED = 0x100000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000

# A reparse tag with this bit is a name surrogate: a junction, a symlink, a
# mount point. Following one would count another folder's bytes twice, or loop.
REPARSE_TAG_NAME_SURROGATE = 0x20000000

# Any of these means the file's data is not on this disk: reading it would
# make the sync client download it.
ONLINE_ONLY_ATTRS = FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS | FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_OFFLINE

# File Explorer leaves out an item with either attribute by default: hidden
# until "Hidden items" is on, system until protected files are shown too.
HIDDEN_ATTRS = FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM

# FILETIME counts 100 ns ticks from 1601-01-01; Unix time starts 1970-01-01.
_FILETIME_UNIX_EPOCH = 116_444_736_000_000_000
_FILETIME_TICKS_PER_SECOND = 10_000_000

# FILE_FULL_DIR_INFO up to FileName: NextEntryOffset, FileIndex, four
# LARGE_INTEGER times, EndOfFile, AllocationSize, FileAttributes,
# FileNameLength, EaSize (which holds the reparse tag on a reparse point).
_RECORD = struct.Struct("<IIqqqqqqIII")
_NAME_OFFSET = _RECORD.size

# One 64 KiB page holds several hundred records; bigger pages measured no faster.
_PAGE_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class Entry:
    """One directory record, as the listing reports it."""

    name: str
    is_dir: bool
    size: int
    alloc: int
    attrs: int
    mtime: int
    reparse_tag: int


def is_link(entry: Entry) -> bool:
    """True for a junction, symlink or mount point, which the walk never follows."""
    return bool(entry.attrs & FILE_ATTRIBUTE_REPARSE_POINT) and bool(entry.reparse_tag & REPARSE_TAG_NAME_SURROGATE)


def is_online_only(attrs: int) -> bool:
    """True when the file's data lives only in the cloud."""
    return bool(attrs & ONLINE_ONLY_ATTRS)


def is_hidden(attrs: int) -> bool:
    """True for a hidden or system item, the kind File Explorer leaves out by default."""
    return bool(attrs & HIDDEN_ATTRS)


def cloud_state(attrs: int) -> str:
    """``online`` (in the cloud only), ``pinned`` (always kept here) or ``local``."""
    if is_online_only(attrs):
        return "online"
    if attrs & FILE_ATTRIBUTE_PINNED:
        return "pinned"
    return "local"


def extended_path(path: str) -> str:
    r"""``\\?\`` form of an absolute Windows path, so deep trees pass MAX_PATH."""
    if path.startswith("\\\\?\\"):
        return path
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def _filetime_to_unix(ticks: int) -> int:
    if ticks <= _FILETIME_UNIX_EPOCH:
        return 0
    return (ticks - _FILETIME_UNIX_EPOCH) // _FILETIME_TICKS_PER_SECOND


def parse_records(page: bytes | bytearray | memoryview) -> Iterator[Entry]:
    """Decode one page of FILE_FULL_DIR_INFO records, skipping ``.`` and ``..``."""
    offset = 0
    while True:
        nxt, _index, _created, _accessed, written, _changed, eof, alloc, attrs, name_len, ea = _RECORD.unpack_from(page, offset)
        start = offset + _NAME_OFFSET
        name = bytes(page[start:start + name_len]).decode("utf-16-le")
        if name not in (".", ".."):
            tag = ea if attrs & FILE_ATTRIBUTE_REPARSE_POINT else 0
            yield Entry(name, bool(attrs & FILE_ATTRIBUTE_DIRECTORY), eof, alloc, attrs, _filetime_to_unix(written), tag)
        if not nxt:
            return
        offset += nxt


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _k32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL

    _FILE_LIST_DIRECTORY = 0x1
    _FILE_SHARE_ALL = 0x7
    _OPEN_EXISTING = 3
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _INVALID_HANDLE = ctypes.c_void_p(-1).value
    _FILE_FULL_DIRECTORY_INFO = 14
    _FILE_FULL_DIRECTORY_RESTART_INFO = 15
    _ERROR_NO_MORE_FILES = 18

    def list_dir(path: str) -> Iterator[Entry]:
        """Every record in ``path``, read through one directory handle.

        The handle asks for ``FILE_LIST_DIRECTORY`` only and opens the reparse
        point itself, so neither the folder nor any file in it is read.
        """
        handle = _k32.CreateFileW(
            extended_path(path), _FILE_LIST_DIRECTORY, _FILE_SHARE_ALL, None, _OPEN_EXISTING,
            _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT, None,
        )
        if handle is None or handle == _INVALID_HANDLE:
            raise ctypes.WinError(ctypes.get_last_error())
        page = ctypes.create_string_buffer(_PAGE_BYTES)
        info_class = _FILE_FULL_DIRECTORY_RESTART_INFO
        try:
            while True:
                if not _k32.GetFileInformationByHandleEx(handle, info_class, page, _PAGE_BYTES):
                    err = ctypes.get_last_error()
                    if err == _ERROR_NO_MORE_FILES:
                        return
                    raise ctypes.WinError(err)
                info_class = _FILE_FULL_DIRECTORY_INFO
                yield from parse_records(memoryview(page).cast("B"))
        finally:
            _k32.CloseHandle(handle)

else:

    def list_dir(path: str) -> Iterator[Entry]:
        """Every record in ``path`` from ``os.scandir`` + ``lstat``; symlinks reported as links."""
        with os.scandir(path) as it:
            for item in it:
                st = item.stat(follow_symlinks=False)
                link = stat.S_ISLNK(st.st_mode)
                is_dir = stat.S_ISDIR(st.st_mode) or (link and item.is_dir())
                blocks = getattr(st, "st_blocks", None)
                alloc = blocks * 512 if blocks is not None else st.st_size
                attrs = (FILE_ATTRIBUTE_DIRECTORY if is_dir else 0) | (FILE_ATTRIBUTE_REPARSE_POINT if link else 0)
                yield Entry(item.name, is_dir, 0 if is_dir else st.st_size, 0 if is_dir else alloc,
                            attrs, int(st.st_mtime), REPARSE_TAG_NAME_SURROGATE if link else 0)
