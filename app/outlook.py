"""``python -m app.outlook``: the Outlook bridge, each verb in its own process with its own COM apartment.

    pythonw -m app.outlook archive --entry-id <id> --store-id <id>
        the Outlook macro's entry, fire and forget: reads the mail, then hands it to the
        Archive sheet of an app open on this PC, or shows the same suggestions in a small
        window when no page is open
    python -m app.outlook prepare --current [--defer-ranking]
    python -m app.outlook prepare --entry-id <id> --store-id <id> [--defer-ranking]
        the Archive sheet's opening document for the open or selected mail, or for one mail;
        --defer-ranking answers before ranking, and the server ranks with its warm index
    python -m app.outlook file --entry-id <id> --store-id <id> --folder <folder> [--category <name>] [--duplicate]
            [--date-prefix auto|on|off]
        save that mail into an email folder, tag it, and start indexing the folder
    python -m app.outlook show --message-id <id>
        Find in mailbox: open the live copy of an archived mail in Outlook
    python -m app.outlook explorer
        the folder open in the foremost File Explorer window, when it is inside an email folder
        (no mail and no Outlook involved: it asks the Windows shell)

``prepare``, ``file``, ``show`` and ``explorer`` print one JSON document, ``{"ok": true, ...}`` or
``{"ok": false, "error": {"code", "message"}}``, and exit 0 or 2. No verb starts Outlook:
each attaches to the one the user has open. The web server runs them as children with a
timeout, so a COM modal hangs the child, never the server.
"""

from __future__ import annotations

import argparse
import json
import logging
import ssl
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Optional

from app.webapp.bind import resolve_bind
from src import config, store
from src.email import archive_sheet
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
from src.stage_timer import StageTimer, process_age_ms

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


def prepare(cfg: dict[str, Any], entry_id: Optional[str], store_id: str, defer_ranking: bool = False,
            timer: Optional[StageTimer] = None) -> dict[str, Any]:
    """The sheet's document; ``entry_id`` None means the mail open or selected in Outlook.

    ``defer_ranking`` returns it before ranking (``ranking`` true) with ``rank_input``, for a
    caller that ranks itself: the server, whose index is warm, or the window, after it shows.
    """
    app = bridge.attach()
    if timer:
        timer.mark("attach")
    item = bridge.mail_at_hand(app) if entry_id is None else bridge.mail_by_ids(app, entry_id, store_id)
    if item is None:
        raise SheetError(ERR_NO_MAIL if entry_id is None else ERR_GONE)
    if timer:
        timer.mark("get_item")
    client = OutlookClient()
    doc, data = archive_sheet.opening(client, cfg, item, bridge.folder_name(item), timer)
    if defer_ranking:
        doc["rank_input"] = archive_sheet.rank_input(data)
    else:
        doc = archive_sheet.ranked(cfg, doc, data)
    entry, store_id = client.item_ids(item)
    doc["ids"] = {"entry_id": entry, "store_id": store_id}
    return doc


def file_one(cfg: dict[str, Any], entry_id: str, store_id: str, folder: str, category: Optional[str],
             duplicate: bool = False, date_prefix: str = "auto") -> dict[str, Any]:
    """File the mail the ids name, then start indexing its folder so search finds it."""
    from src.email import indexer  # noqa: PLC0415 - only filing starts the indexer; the macro's entry starts faster without it

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
    for scheme in ("https", "http"):
        try:
            status, answer = _post_json("%s://127.0.0.1:%d/api/archive/handoff" % (scheme, port), doc, HANDOFF_TIMEOUT_S)
        except OSError:
            continue
        if status < 400:
            shown = bool(answer.get("shown"))
            logger.info("ℹ️ archive: handed to the app, %s", "a page shows it" if shown else "no page is open")
            return shown
        logger.warning("⚠️ archive: the app refused the hand-off: HTTP %s", status)
        return False
    logger.info("ℹ️ archive: the app isn't running, so the fallback window opens")
    return False


def _post_json(url: str, doc: dict[str, Any], timeout: float) -> tuple[int, dict[str, Any]]:
    """POST ``doc`` to the app on loopback; the status and the JSON answer. ``OSError`` when nothing answers.

    The standard library's client, not ``requests``: the macro's entry is a new process per
    click, and importing ``requests`` costs it more than this one call does.
    """
    unverified = ssl.create_default_context()
    unverified.check_hostname = False
    unverified.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, data=json.dumps(doc).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=unverified) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {}
    except ValueError as exc:
        raise OSError("the app's answer isn't JSON") from exc


def archive(cfg: dict[str, Any], entry_id: str, store_id: str, timer: Optional[StageTimer] = None) -> int:
    """The macro's entry: the sheet in the app when a page is open, otherwise the fallback window.

    The mail goes out before any ranking: the app's page shows it at once and the server ranks
    it with its warm index; the window shows it at once and ranks in the background.
    """
    timer = timer or StageTimer("archive")
    with _apartment():
        doc = _guarded(lambda: prepare(cfg, entry_id, store_id, defer_ranking=True, timer=timer))
        shown = hand_to_page(dict(doc, from_macro=True), cfg)
        timer.mark("handoff")
        if shown:
            timer.log(logger)
            return EXIT_OK
        from app.webapp.state import EMAIL_FOLDER_HITS, email_folder_hits  # noqa: PLC0415 - only the window searches here
        from src.archive_window import show_window  # noqa: PLC0415 - tkinter only when no page is open

        data = archive_sheet.rank_data(doc.pop("rank_input", None))
        doc.pop("ids", None)
        rank = (lambda: archive_sheet.rank(cfg, data, doc["roots"])) if doc.get("ok") and data is not None else None
        if rank is None:
            doc.pop("ranking", None)
        timer.mark("window_imports")

        def progress(stage: str) -> None:
            timer.mark(stage)
            if stage == "suggestions_shown" or rank is None:
                timer.log(logger)

        show_window(doc, lambda folder, category, duplicate, date_prefix: _guarded(
            lambda: file_one(cfg, entry_id, store_id, folder, category, duplicate, date_prefix)),
                    lambda query: email_folder_hits(cfg, query, EMAIL_FOLDER_HITS),
                    current_explorer=lambda: _guarded(lambda: archive_sheet.explorer_folder(cfg)),
                    rank=rank, progress=progress)
    return EXIT_OK


def _setup_logging() -> None:
    """Log to ``outlook.log`` in the runtime-data folder, and to stderr when there is one (pythonw has none)."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)] if sys.stderr is not None else []
    try:
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
            p.add_argument("--defer-ranking", action="store_true",
                           help="answer before ranking, with what ranking reads, for a caller that ranks itself")
        if verb == "file":
            p.add_argument("--folder", required=True)
            p.add_argument("--category", default="")
            p.add_argument("--duplicate", action="store_true", help="file another copy of a mail that is already archived")
            p.add_argument("--date-prefix", choices=sorted(DATE_PREFIX_CHOICES), default="auto",
                           help="date the file name: auto (the folder's own form), on or off")
    sub.add_parser("show").add_argument("--message-id", required=True)
    sub.add_parser("explorer")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    # The interpreter's start-up and this module's imports, before any code of ours ran.
    startup = process_age_ms()
    timer = StageTimer("outlook")
    if startup is not None and argv is None:
        timer.add("startup", startup)
    args = _parser().parse_args(argv)
    timer.name = args.verb
    _setup_logging()
    cfg = config.load()
    timer.mark("config")
    if args.verb == "archive":
        return archive(cfg, args.entry_id, args.store_id, timer)
    if args.verb == "prepare":
        if args.current == bool(args.entry_id):
            doc = error_document(ERR_FAILED, "prepare needs --current or --entry-id, not both")
        else:
            with _apartment():
                doc = _guarded(lambda: prepare(cfg, None if args.current else args.entry_id, args.store_id,
                                               args.defer_ranking, timer))
            timer.log(logger)
    elif args.verb == "file":
        with _apartment():
            doc = _guarded(lambda: file_one(cfg, args.entry_id, args.store_id, args.folder, args.category or None, args.duplicate,
                                             args.date_prefix))
    elif args.verb == "explorer":
        with _apartment():
            doc = _guarded(lambda: archive_sheet.explorer_folder(cfg))
    else:
        with _apartment():
            doc = _guarded(lambda: show(cfg, args.message_id))
    _emit(doc)
    return EXIT_OK if doc.get("ok") else EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
