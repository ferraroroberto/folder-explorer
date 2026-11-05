"""The Outlook bridge's COM calls: the running Outlook, the mail the user has at hand, and showing a mail.

Only ``app/outlook.py`` calls these, in its own process. None of them starts
Outlook: the bridge files or shows a mail the user is looking at, so it needs
the Outlook they have open, and one started here would be hidden from them.
"""

from __future__ import annotations

import importlib.util
import logging
from typing import Any

from src.outlook import process
from src.outlook.mapi import OL_CLASS_MAIL_ITEM, OutlookUnavailableError, safe_com

logger = logging.getLogger(__name__)

NOT_REACHABLE = "Outlook isn't reachable: open Outlook (classic) and try again."


def pywin32_installed() -> bool:
    """Whether the COM bridge's pywin32 resolves, without importing it."""
    return importlib.util.find_spec("win32com") is not None


def attach() -> Any:
    """The running Outlook's MAPI namespace; raises ``OutlookUnavailableError`` when none is running."""
    app = process.get_active_application()
    if app is None:
        raise OutlookUnavailableError(NOT_REACHABLE)
    return app


def _as_mail(raw: Any) -> Any | None:
    """``raw`` as a full MailItem, or ``None`` for a meeting request, a report or nothing at all."""
    if raw is None or safe_com(lambda: raw.Class, None) != OL_CLASS_MAIL_ITEM:
        return None
    import win32com.client  # noqa: PLC0415

    return win32com.client.Dispatch(raw)


def mail_by_ids(app: Any, entry_id: str, store_id: str) -> Any | None:
    """The mail the macro named, fetched again from its store, or ``None`` when it is gone.

    The macro passes the ids at click time and returns at once, so the mail
    is read here from nothing but those strings: no reference from Outlook's
    own thread is ever used, and a fast second click cannot swap the mail.
    """
    try:
        namespace = app.GetNamespace("MAPI")
        raw = namespace.GetItemFromID(entry_id, store_id) if store_id else namespace.GetItemFromID(entry_id)
    except Exception as exc:
        logger.warning("⚠️ GetItemFromID failed: %s: %s", type(exc).__name__, exc)
        return None
    return _as_mail(raw)


def mail_at_hand(app: Any) -> Any | None:
    """The mail open in Outlook's front window, else the first one selected in its main window."""
    window = safe_com(lambda: app.ActiveWindow(), None)
    if window is not None and safe_com(lambda: window.Class, None) == _OL_INSPECTOR_CLASS:
        return _as_mail(safe_com(lambda: window.CurrentItem, None))
    explorer = safe_com(lambda: app.ActiveExplorer(), None)
    selection = safe_com(lambda: explorer.Selection, None) if explorer is not None else None
    if selection is None or not safe_com(lambda: int(selection.Count), 0):
        return None
    return _as_mail(safe_com(lambda: selection.Item(1), None))


# OlObjectClass.olInspector: the window of one opened item.
_OL_INSPECTOR_CLASS = 35


def folder_name(item: Any) -> str:
    """The Outlook folder the mail sits in, for the sheet's "where" line."""
    return safe_com(lambda: str(item.Parent.Name), "")


def display(item: Any) -> None:
    """Open the mail in its own Outlook window, without waiting on it."""
    item.Display(False)
