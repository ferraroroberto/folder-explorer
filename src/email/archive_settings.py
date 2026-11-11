"""The archive and Outlook settings the batch verbs read, from ``config.json``.

Every accessor takes the loaded config (``src.config.load()``) and is the one
place its key is read, so the batch layer never reaches into the dict itself.
The archive roots are the email folders (``email.roots``): the only folders a
verb may write, rename or delete in. The index the verbs read and write is
this app's own ``emails.sqlite3``, never another app's.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from src.email.mailboxes import Mailbox
from src.email.schema import own_index_path

# Windows MAX_PATH is 260 including the terminating NUL; 255 leaves headroom
# for the OS, COM marshalling and long-path prefixes. The archiver shortens a
# file name so the path it writes never passes it.
DEFAULT_MAX_PATH_LENGTH = 255

# The third ``archive.date_prefix`` value beside true and false: infer the
# form per destination folder from what it already holds.
DATE_PREFIX_AUTO = "auto"

# Where ``apply`` parks a filed mail, and the categories that make it obvious
# in Outlook which mails a batch run touched (``revert`` removes the first).
DEFAULT_ARCHIVE_FOLDER = "Archive"
DEFAULT_ARCHIVED_CATEGORY = "Archived by task-os"
DEFAULT_DISMISSED_CATEGORY = "Dismissed by task-os"

# The body preview a live mail is read with, the same length the index stores.
BODY_PREVIEW_LENGTH = 500


def _section(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    value = cfg.get(name)
    return value if isinstance(value, dict) else {}


def _text(cfg: dict[str, Any], section: str, key: str) -> Optional[str]:
    value = _section(cfg, section).get(key)
    return str(value).strip() if value and str(value).strip() else None


def index_path(cfg: dict[str, Any]) -> Path:
    """This app's email index: the batch verbs' one index, whatever ``email.index_path`` names."""
    return own_index_path()


def get_archive_roots(cfg: dict[str, Any]) -> list[str]:
    """The archive roots: the configured email folders, blanks dropped."""
    return [p for p in (_section(cfg, "email").get("roots") or []) if isinstance(p, str) and p.strip()]


def get_max_path_length(cfg: dict[str, Any]) -> int:
    value = _section(cfg, "archive").get("max_path_length")
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else DEFAULT_MAX_PATH_LENGTH


def get_date_prefix_mode(cfg: dict[str, Any]) -> bool | str:
    """``archive.date_prefix``: True, False or ``"auto"`` (infer per destination folder)."""
    value = _section(cfg, "archive").get("date_prefix")
    if isinstance(value, str) and value.strip().lower() == DATE_PREFIX_AUTO:
        return DATE_PREFIX_AUTO
    return bool(value)


def get_suggestion_limits(cfg: dict[str, Any]) -> tuple[int, float]:
    """``(max_suggestions, min_score)`` for the folder suggester."""
    archive = _section(cfg, "archive")
    top = archive.get("max_suggestions")
    floor = archive.get("min_score")
    return (top if isinstance(top, int) and not isinstance(top, bool) and top > 0 else 3,
            float(floor) if isinstance(floor, (int, float)) and not isinstance(floor, bool) else 0.05)


def get_outlook_archive_folder(cfg: dict[str, Any]) -> str:
    """The Outlook folder ``apply`` moves a filed mail into; a blank value falls back, never the root."""
    return _text(cfg, "outlook", "archive_folder") or DEFAULT_ARCHIVE_FOLDER


def get_outlook_category(cfg: dict[str, Any]) -> str:
    """The category ``apply`` stamps and ``revert`` removes."""
    return _text(cfg, "outlook", "category") or DEFAULT_ARCHIVED_CATEGORY


def get_outlook_add_category(cfg: dict[str, Any]) -> bool:
    """Whether the Archive window and sheet offer the archived category and filing stamps it (off unless set)."""
    return _section(cfg, "outlook").get("add_category") is True


def get_outlook_dismiss_category(cfg: dict[str, Any]) -> str:
    """The category ``dismiss`` stamps on a mail it moved without keeping a file."""
    return _text(cfg, "outlook", "dismiss_category") or DEFAULT_DISMISSED_CATEGORY


def get_outlook_self_address(cfg: dict[str, Any]) -> Optional[str]:
    """The address ``draft`` blind-copies, or None to read the default account's from Outlook."""
    return _text(cfg, "outlook", "self_address")


def get_mailbox_archive_folder(cfg: dict[str, Any], mailbox: Optional[Mailbox]) -> str:
    """The mailbox's own ``archive_folder`` when the registry sets one, else ``outlook.archive_folder``."""
    if mailbox is not None and mailbox.archive_folder:
        return mailbox.archive_folder
    return get_outlook_archive_folder(cfg)
