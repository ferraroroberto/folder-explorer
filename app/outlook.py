"""``python -m app.outlook``: the Outlook bridge, each verb in its own process with its own COM apartment.

    pythonw -m app.outlook archive --entry-id <id> --store-id <id>
        the Outlook macro's entry, fire and forget: reads the mail, then hands it to the
        Archive sheet of an app open on this PC, or shows the same suggestions in a small
        window when no page is open
    python -m app.outlook prepare --current
    python -m app.outlook prepare --entry-id <id> --store-id <id>
        the Archive sheet's opening document for the open or selected mail, or for one mail
    python -m app.outlook file --entry-id <id> --store-id <id> --folder <folder> [--category <name>] [--duplicate]
            [--date-prefix auto|on|off]
        save that mail into an email folder, tag it, and start indexing the folder
    python -m app.outlook show --message-id <id>
        Find in mailbox: open the live copy of an archived mail in Outlook

``prepare``, ``file`` and ``show`` print one JSON document, ``{"ok": true, ...}`` or
``{"ok": false, "error": {"code", "message"}}``, and exit 0 or 2. No verb starts Outlook:
each attaches to the one the user has open. The web server runs them as children with a
timeout, so a COM modal hangs the child, never the server.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Optional

import requests
import urllib3

from src import config, store
from src.email import archive_sheet, indexer
from src.email.archive import DATE_PREFIX_CHOICES
from src.email.archive_settings import get_mailbox_archive_folder
from src.email.archive_sheet import (
    ERR_FAILED,
    ERR_GONE,
    ERR_NO_COM,
    ERR_NO_MAIL,
    ERR_NOT_IN_MAILBOX,
    ERR_OUTLOOK,
    SheetError,
    error_document,
)
from src.email.mailboxes import MailboxRegistryError, load_mailbox_registry
from src.outlook import bridge
from src.outlook.client import OutlookClient
from src.outlook.mapi import MailboxNotInOutlookError, OutlookUnavailableError
from app.webapp.bind import resolve_bind

logger = logging.getLogger("app.outlook")

EXIT_OK, EXIT_REFUSED = 0, 2
# How long the macro's entry waits for the app to take the mail before it opens its own window.
HANDOFF_TIMEOUT_S = 3.0


def _emit(document: dict[str, Any]) -> None:
    """Print the one JSON document, as UTF-8 whatever the console code page."""
    if sys.stdout is None:
        return
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    json.dump(document, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    sys.stdout.flush()


@contextmanager
def _apartment() -> Iterator[None]:
    """A single-threaded COM apartment on this thread, or nothing when pywin32 isn't installed."""
    if not bridge.pywin32_installed():
        yield
        return
    import pythoncom  # noqa: PLC0415

    pythoncom.CoInitialize()
    try:
        yield
    finally:
        pythoncom.CoUninitialize()


def _guarded(work: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run one step; every failure comes back as its own error document, never a traceback."""
    try:
        if not bridge.pywin32_installed():
            raise SheetError(ERR_NO_COM)
        return work()
    except OutlookUnavailableError:
        logger.warning("⚠️ Outlook isn't reachable")
        return error_document(ERR_OUTLOOK)
    except SheetError as exc:
        logger.warning("⚠️ %s: %s", exc.code, exc)
        return error_document(exc.code, str(exc))
    except Exception as exc:
        logger.exception("❌ the Outlook step failed")
        return error_document(ERR_FAILED, "%s: %s" % (type(exc).__name__, exc))


def prepare(cfg: dict[str, Any], entry_id: Optional[str], store_id: str) -> dict[str, Any]:
    """The sheet's opening document; ``entry_id`` None means the mail open or selected in Outlook."""
    app = bridge.attach()
    item = bridge.mail_at_hand(app) if entry_id is None else bridge.mail_by_ids(app, entry_id, store_id)
    if item is None:
        raise SheetError(ERR_NO_MAIL if entry_id is None else ERR_GONE)
    client = OutlookClient()
    doc = archive_sheet.suggest(client, cfg, item, bridge.folder_name(item))
    entry, store_id = client.item_ids(item)
    doc["ids"] = {"entry_id": entry, "store_id": store_id}
    return doc


def file_one(cfg: dict[str, Any], entry_id: str, store_id: str, folder: str, category: Optional[str],
             duplicate: bool = False, date_prefix: str = "auto") -> dict[str, Any]:
    """File the mail the ids name, then start indexing its folder so search finds it."""
    item = bridge.mail_by_ids(bridge.attach(), entry_id, store_id)
    if item is None:
        raise SheetError(ERR_GONE)
    doc = archive_sheet.file_mail(OutlookClient(), cfg, item, folder, category, duplicate, date_prefix)
    try:
        indexer.spawn("archive", doc["folder_path"])
        doc["indexing"] = True
    except OSError as exc:
        logger.warning("⚠️ the filed mail's folder was not indexed: %s", exc)
        doc["indexing"] = False
    return doc


def show(cfg: dict[str, Any], message_id: str) -> dict[str, Any]:
    """Open the live copy of the mail with this Message-ID, looking in every registry mailbox."""
    try:
        registry = load_mailbox_registry()
    except MailboxRegistryError as exc:
        raise SheetError(ERR_FAILED, str(exc)) from exc
    bridge.attach()
    for mailbox in registry.mailboxes.values():
        client = OutlookClient(mailbox)
        try:
            item = client.find_live_copy(message_id, get_mailbox_archive_folder(cfg, mailbox))
        except MailboxNotInOutlookError:
            continue
        if item is not None:
            bridge.display(item)
            where = bridge.folder_name(item)
            logger.info("ℹ️ find in mailbox: opened the live copy from %s", where or "a folder")
            return {"ok": True, "folder": where, "mailbox": mailbox.alias}
    raise SheetError(ERR_NOT_IN_MAILBOX)


def hand_to_page(doc: dict[str, Any], cfg: dict[str, Any]) -> bool:
    """Give the prepared mail to the app; True when a page on this PC is open to show it.

    The app answers on loopback over its own scheme; 127.0.0.1 is not the name
    the tailnet certificate carries, so the certificate is not verified here.
    """
    port = resolve_bind(cfg).port
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    for scheme in ("https", "http"):
        try:
            r = requests.post("%s://127.0.0.1:%d/api/archive/handoff" % (scheme, port), json=doc,
                              timeout=HANDOFF_TIMEOUT_S, verify=False)
        except requests.RequestException:
            continue
        if r.ok:
            shown = bool(r.json().get("shown"))
            logger.info("ℹ️ archive: handed to the app, %s", "a page shows it" if shown else "no page is open")
            return shown
        logger.warning("⚠️ archive: the app refused the hand-off: HTTP %s", r.status_code)
        return False
    logger.info("ℹ️ archive: the app isn't running, so the fallback window opens")
    return False


def archive(cfg: dict[str, Any], entry_id: str, store_id: str) -> int:
    """The macro's entry: the sheet in the app when a page is open, otherwise the fallback window."""
    with _apartment():
        doc = _guarded(lambda: prepare(cfg, entry_id, store_id))
        if hand_to_page(dict(doc, from_macro=True), cfg):
            return EXIT_OK
        from app.archive_window import show_window  # noqa: PLC0415 - tkinter only when no page is open

        show_window(doc, lambda folder, category: _guarded(lambda: file_one(cfg, entry_id, store_id, folder, category)))
    return EXIT_OK


def _setup_logging() -> None:
    """Log to ``outlook.log`` in the runtime-data folder, and to stderr when there is one (pythonw has none)."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)] if sys.stderr is not None else []
    try:
        # runtime-write-exempt: outlook.log sits in the runtime-data folder
        handlers.append(logging.FileHandler(store.data_dir() / "outlook.log", encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S", handlers=handlers)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.outlook", description="The Outlook bridge.")
    sub = parser.add_subparsers(dest="verb", required=True)
    for verb in ("archive", "prepare", "file"):
        p = sub.add_parser(verb)
        p.add_argument("--entry-id", required=verb != "prepare")
        p.add_argument("--store-id", default="")
        if verb == "prepare":
            p.add_argument("--current", action="store_true", help="the mail open or selected in Outlook")
        if verb == "file":
            p.add_argument("--folder", required=True)
            p.add_argument("--category", default="")
            p.add_argument("--duplicate", action="store_true", help="file another copy of a mail that is already archived")
            p.add_argument("--date-prefix", choices=sorted(DATE_PREFIX_CHOICES), default="auto",
                           help="date the file name: auto (the folder's own form), on or off")
    sub.add_parser("show").add_argument("--message-id", required=True)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    _setup_logging()
    cfg = config.load()
    if args.verb == "archive":
        return archive(cfg, args.entry_id, args.store_id)
    if args.verb == "prepare":
        if args.current == bool(args.entry_id):
            doc = error_document(ERR_FAILED, "prepare needs --current or --entry-id, not both")
        else:
            with _apartment():
                doc = _guarded(lambda: prepare(cfg, None if args.current else args.entry_id, args.store_id))
    elif args.verb == "file":
        with _apartment():
            doc = _guarded(lambda: file_one(cfg, args.entry_id, args.store_id, args.folder, args.category or None, args.duplicate,
                                             args.date_prefix))
    else:
        with _apartment():
            doc = _guarded(lambda: show(cfg, args.message_id))
    _emit(doc)
    return EXIT_OK if doc.get("ok") else EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
