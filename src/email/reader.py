"""The one module that opens a scanned file: read a ``.msg`` mail, read-only.

Everything else in the app works from folder listings and index rows. This
module, and only this one, imports ``extract_msg``; ``tests/test_email_reader.py``
fails the gate if any other module does. It opens the file for reading only and
never changes it, its attributes or its pin state. Reading an online-only
OneDrive file makes the sync client download it; that is why only the email
stage (a child process with a download budget) and "Show all" (a child
process with a timeout) call it, and only on a file inside the email folders.

A mail whose text is not in the code page it declares raises
``UnicodeDecodeError`` inside extract-msg; that one error, and no other, gets
a second read as Windows-1252, logged once per file.

Run as ``python -m src.email.reader <path>``: one JSON document on stdout,
``{"ok": true, "text": ..., "html": ..., "attachments": [...]}`` or
``{"ok": false, "error": <code>, "message": ...}``.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Callable

from src.email.naming import clean_subject, normalize_message_id
from src.email.preview import clean_preview

logger = logging.getLogger(__name__)

# Error codes the callers turn into distinct messages.
ERR_GONE = "gone"
ERR_PARSE = "parse"
ERR_NOT_SET_UP = "not_set_up"
ERR_READ = "read"

# The largest body handed back, so one huge mail cannot flood the page.
MAX_BODY_CHARS = 2_000_000
# The fallback code page for a mail whose declared one does not decode.
FALLBACK_ENCODING = "cp1252"

# MAPI property keys (id + type): PidTagFlagStatus and PidTagInternetMessageId.
_PR_FLAG_STATUS = "10900003"
_PR_INTERNET_MESSAGE_ID = "1035001F"


class ReadError(Exception):
    """A mail that could not be read, with the code the caller reports."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value).strip()


def _prop(msg: Any, key: str) -> Any:
    try:
        return msg.getPropertyVal(key)
    except (AttributeError, TypeError, ValueError):
        return None


def _attachments(msg: Any) -> list[str]:
    names = []
    try:
        atts = list(getattr(msg, "attachments", []) or [])
    except Exception:  # noqa: BLE001 -- an attachment list that will not load still leaves the mail readable
        return []
    for att in atts:
        try:
            name = getattr(att, "longFilename", None) or getattr(att, "shortFilename", None)
        except Exception:  # noqa: BLE001 -- one unreadable attachment is skipped, the rest are kept
            continue
        if name:
            names.append(str(name))
    return names


def _date(msg: Any) -> str:
    raw = getattr(msg, "date", None)
    if not raw:
        return ""
    return raw.isoformat() if isinstance(raw, datetime) else str(raw)


def _facts(msg: Any, preview_chars: int) -> dict[str, Any]:
    flag = _prop(msg, _PR_FLAG_STATUS)
    mid = getattr(msg, "messageId", None) or _prop(msg, _PR_INTERNET_MESSAGE_ID)
    names = _attachments(msg)
    to, cc = _text(msg.to), _text(getattr(msg, "cc", None))
    return {
        "subject": clean_subject(_text(msg.subject), strip_msg_suffix=True), "sender": _text(msg.sender),
        "recipients": to, "to_addrs": to, "cc_addrs": cc,
        "date_sent": _date(msg), "body_preview": clean_preview(_text(msg.body))[:preview_chars],
        "flag_status": int(flag or 0) if str(flag or 0).isdigit() else 0,
        "message_id": normalize_message_id(mid),
        "attachment_names": "\n".join(names), "has_attachments": 1 if names else 0,
    }


def _body(msg: Any, preview_chars: int) -> dict[str, Any]:
    return {"text": _text(msg.body)[:MAX_BODY_CHARS], "html": _text(getattr(msg, "htmlBody", None))[:MAX_BODY_CHARS],
            "attachments": _attachments(msg)}


def _read(path: str, take: Callable[[Any, int], dict[str, Any]], preview_chars: int) -> dict[str, Any]:
    """Open ``path`` and apply ``take``; one Windows-1252 retry on ``UnicodeDecodeError`` only."""
    try:
        import extract_msg
        from extract_msg.exceptions import (
            InvalidFileFormatError,
            UnrecognizedMSGTypeError,
        )
    except ImportError as exc:
        raise ReadError(ERR_NOT_SET_UP, "extract-msg isn't installed") from exc
    try:
        try:
            with extract_msg.Message(path) as msg:
                return take(msg, preview_chars)
        except UnicodeDecodeError:
            logger.info("ℹ️ %s: not in its declared code page, read again as Windows-1252", os.path.basename(path))
            with extract_msg.Message(path, overrideEncoding=FALLBACK_ENCODING) as msg:
                return take(msg, preview_chars)
    except FileNotFoundError as exc:
        raise ReadError(ERR_GONE, "the file is no longer there") from exc
    except (InvalidFileFormatError, UnrecognizedMSGTypeError, UnicodeDecodeError, ValueError) as exc:
        raise ReadError(ERR_PARSE, "not a readable mail: " + type(exc).__name__) from exc
    except OSError as exc:
        raise ReadError(ERR_READ, "the file could not be read: " + str(exc)) from exc


def read_facts(path: str, preview_chars: int) -> dict[str, Any]:
    """The fields the email index stores for one mail; raises ``ReadError``."""
    return _read(path, _facts, preview_chars)


def read_message(path: str) -> dict[str, Any]:
    """The mail's plain-text body, HTML body (if any) and attachment names, as the JSON the child prints."""
    try:
        return {"ok": True, **_read(path, _body, 0)}
    except ReadError as exc:
        return {"ok": False, "error": exc.code, "message": str(exc)}


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(argv) != 1:
        logger.error("❌ usage: python -m src.email.reader <path to a .msg file>")
        return 2
    sys.stdout.write(json.dumps(read_message(argv[0])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
