"""The only writer of a user's file outside the email folders: a Markdown file saved from the editor.

It writes the one file it is named, never another and never a new one: the
path is rebuilt from the scan index by the caller, it must be ``.md`` or
``.markdown``, and it must still be listed in its folder as a plain local file
(a link, a cloud-only placeholder or a vanished file is refused before any
byte moves). Every save reads the file again through ``src/docs/reader.py``
and compares its version (the listing's modified time, the size and a hash of
the bytes) with the one the editor opened; any difference is a
:class:`Conflict`, never a silent overwrite. The new bytes go through
``src/atomic_write.py``, so the file is the old copy or the new one, never half.

The file keeps its shape: a UTF-8 byte-order mark, its line ends and whether
it ends in a newline. An unchanged text writes nothing, so a file with mixed
line ends survives a save untouched. A file that is not UTF-8, or is over
:data:`EDIT_MAX_BYTES`, opens read-only. Overwriting a newer copy, the one
deliberate clobber, first backs that copy up in the runtime-data folder.
"""

from __future__ import annotations

import codecs
import hashlib
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from src import runtime_files, store
from src.atomic_write import atomic_write_bytes
from src.docs import reader

logger = logging.getLogger(__name__)

# Half the viewer's cap: a page past 1 MB is generated output, and a phone's text field slows well before it.
EDIT_MAX_BYTES = 1024 * 1024
BACKUP_FOLDER = "doc-backups"
CONFLICT_MESSAGE = "Changed on disk since you opened it"


class Conflict(Exception):
    """The file changed since the editor opened it: its current text (None when it is no longer editable) and version."""

    def __init__(self, text: Optional[str], version: str) -> None:
        super().__init__(CONFLICT_MESSAGE)
        self.text = text
        self.version = version


@dataclass(frozen=True)
class Shape:
    """What a save keeps from the file on disk: the byte-order mark, the line end and the final newline."""

    bom: bool
    newline: str
    trailing: bool


def version_of(data: bytes, mtime: int) -> str:
    """The version an edit is checked against; the hash catches a same-size change within the listing's one-second mtime."""
    return "%d.%d.%s" % (mtime, len(data), hashlib.sha256(data).hexdigest()[:24])


def decode(data: bytes) -> Optional[tuple[str, Shape]]:
    """The file's text with ``\\n`` line ends, and its shape; None when it is not UTF-8."""
    bom = data.startswith(codecs.BOM_UTF8)
    try:
        text = data[len(codecs.BOM_UTF8):].decode("utf-8") if bom else data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    crlf = text.count("\r\n")
    newline = "\r\n" if crlf > text.count("\n") - crlf else "\n"
    text = text.replace("\r\n", "\n")
    return text, Shape(bom, newline, text.endswith("\n"))


def encode(text: str, shape: Shape) -> bytes:
    """``text`` as the file's bytes: its line end, its final newline (or none), its byte-order mark."""
    text = text.replace("\r\n", "\n")
    if shape.trailing and text and not text.endswith("\n"):
        text += "\n"
    elif not shape.trailing and text.endswith("\n"):
        text = text[:-1]
    body = text.replace("\n", shape.newline).encode("utf-8")
    return codecs.BOM_UTF8 + body if shape.bom else body


def source(path: str, index_online: bool, download: bool = False) -> dict[str, Any]:
    """What the editor opens: ``{version, editable, text}``, or ``{version, editable: False, reason, detail}`` when it can't edit."""
    data, mtime = reader.read_document_listed(path, index_online, download)
    version = version_of(data, mtime)
    decoded = decode(data) if len(data) <= EDIT_MAX_BYTES else None
    if decoded is None:
        reason = "edit_too_large" if len(data) > EDIT_MAX_BYTES else "not_utf8"
        return {"version": version, "editable": False, "reason": reason, "detail": reader.REASON_MESSAGES[reason]}
    return {"version": version, "editable": True, "text": decoded[0]}


def _current(path: str, index_online: bool) -> tuple[bytes, str]:
    try:
        data, mtime = reader.read_document_listed(path, index_online, cap=EDIT_MAX_BYTES)
    except reader.Refused as exc:
        if exc.reason == "too_large":
            raise reader.Refused("edit_too_large") from exc
        raise
    return data, version_of(data, mtime)


def _backup(path: str, data: bytes) -> Path:
    """Keep the copy an overwrite replaces, named by when and what it was; nothing reads it back but the owner."""
    folder = store.data_dir() / BACKUP_FOLDER
    runtime_files.make_dirs(folder)
    stamp = time.strftime("%Y%m%d-%H%M%S") + "-%06d" % (time.time_ns() // 1000 % 1_000_000)
    target = folder / ("%s-%s" % (stamp, os.path.basename(path)))
    atomic_write_bytes(target, data)
    return target


def save(path: str, text: str, version: str, index_online: bool, overwrite: bool = False) -> str:
    """Write ``text`` over the Markdown file at ``path`` when it is still at ``version``; returns the new version.

    Raises :class:`Conflict` when the file changed since ``version`` and
    ``reader.Refused`` for a file it may not write. ``overwrite`` is the
    confirmed "Overwrite theirs": ``version`` is then the newer copy's, which is
    backed up before it is replaced.
    """
    if not reader.is_document(path):
        raise reader.Refused("not_document")
    data, current = _current(path, index_online)
    decoded = decode(data)
    if current != version:
        logger.info("ℹ️ doc: save refused, the file changed since it was opened")
        raise Conflict(decoded[0] if decoded else None, current)
    if decoded is None:
        raise reader.Refused("not_utf8")
    if text == decoded[0]:
        return current
    new = encode(text, decoded[1])
    if len(new) > EDIT_MAX_BYTES:
        raise reader.Refused("edit_too_large")
    if overwrite:
        logger.info("ℹ️ doc: overwriting a newer copy; the replaced one is kept as %s", _backup(path, data).name)
    try:
        atomic_write_bytes(Path(path), new)
    except OSError as exc:
        logger.warning("⚠️ doc: save failed: %s", exc)
        raise reader.Refused("not_writable") from exc
    logger.info("ℹ️ doc: saved a Markdown file (%d -> %d bytes)", len(data), len(new))
    return _current(path, index_online)[1]
