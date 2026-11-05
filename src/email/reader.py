"""The one module that opens a scanned file: read a ``.msg`` mail, read-only.

Everything else in the app works from folder listings and index rows. This
module, and only this one, imports ``extract_msg``; ``tests/test_email_reader.py``
fails the gate if any other module does. It opens the file for reading only and
never changes it, its attributes or its pin state. Reading an online-only
OneDrive file makes the sync client download it; that is why the caller runs
it in a child process with a timeout (``src/email/body.py``) and only on a
file inside the configured email folders.

Run as ``python -m src.email.reader <path>``: one JSON document on stdout,
``{"ok": true, "text": ..., "html": ..., "attachments": [...]}`` or
``{"ok": false, "error": <code>, "message": ...}``.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)

# Error codes the caller turns into distinct messages.
ERR_GONE = "gone"
ERR_PARSE = "parse"
ERR_NOT_SET_UP = "not_set_up"
ERR_READ = "read"

# The largest body handed back, so one huge mail cannot flood the page.
MAX_BODY_CHARS = 2_000_000


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def read_message(path: str) -> dict[str, Any]:
    """The mail's plain-text body, HTML body (if any) and attachment names."""
    try:
        import extract_msg
        from extract_msg.exceptions import InvalidFileFormatError, UnrecognizedMSGTypeError
    except ImportError:
        return {"ok": False, "error": ERR_NOT_SET_UP, "message": "extract-msg isn't installed"}
    try:
        with extract_msg.Message(path) as msg:
            text = _text(msg.body)
            html = _text(getattr(msg, "htmlBody", None))
            names = []
            for att in getattr(msg, "attachments", []) or []:
                name = getattr(att, "longFilename", None) or getattr(att, "shortFilename", None)
                if name:
                    names.append(str(name))
    except FileNotFoundError:
        return {"ok": False, "error": ERR_GONE, "message": "the file is no longer there"}
    except (InvalidFileFormatError, UnrecognizedMSGTypeError, ValueError) as exc:
        return {"ok": False, "error": ERR_PARSE, "message": "not a readable mail: " + type(exc).__name__}
    except OSError as exc:
        return {"ok": False, "error": ERR_READ, "message": "the file could not be read: " + str(exc)}
    return {"ok": True, "text": text[:MAX_BODY_CHARS], "html": html[:MAX_BODY_CHARS], "attachments": names}


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(argv) != 1:
        logger.error("❌ usage: python -m src.email.reader <path to a .msg file>")
        return 2
    result = read_message(argv[0])
    sys.stdout.write(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
