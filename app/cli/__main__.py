"""``python -m app.cli``: scan and index from the command line, with no app or tray running.

    python -m app.cli scan              scan every configured root, then index the email folders
    python -m app.cli scan --root c     scan one root (repeat --root for more)
    python -m app.cli scan --no-email   scan only, leave the email index as it is
    python -m app.cli roots             list the roots and their last scan
    python -m app.cli email index [--root FOLDER] [--wait S] [--fresh]
                                        index the email folders (or one folder below one);
                                        --fresh lists them now instead of using the last scan
    python -m app.cli email import --from FILE
                                        copy another app's email index in, once
    python -m app.cli email status      the email index's size and its last runs
    python -m app.cli email config --json
                                        {"roots": [...], "db": "<index>", "own": true|false} for consumers
    python -m app.cli email batch <verb> ...
                                        the Outlook archive verbs (plan, apply, revert, dismiss, renumber,
                                        draft, read, send, mailboxes, find, sync): one JSON document on
                                        stdout; verbs, flags and exit codes in app/cli/email_batch.py

``scan`` exit codes: 0 every root scanned, 1 a root failed, 2 another scan
holds the lock (nothing was scanned), 3 an unknown root key. ``email index``:
0 indexed, 1 the run failed, 2 busy (another email run held the lock past
``--wait``), 3 the folder is not inside an email folder, 4 not set up here.
``email import``: 0 imported, 1 refused (the index exists, or the file is not
an email index).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

from src import config, scan_runner, store
from src.email import indexer
from src.email.schema import import_index, own_index_path
from src.email.settings import email_part
from src.roots import resolve_roots
from src.scan_lock import ScanBusy

logger = logging.getLogger("app.cli")

EXIT_OK, EXIT_FAILED, EXIT_BUSY, EXIT_UNKNOWN_ROOT = 0, 1, 2, 3
EXIT_NOT_SET_UP = 4


def _gib(n: int) -> str:
    return "%.1f GiB" % (n / 2**30)


def cmd_email_index(scope: Optional[str], wait_s: float, trigger: str, fresh: bool = False) -> int:
    try:
        result = indexer.run(scope, trigger=trigger, wait_s=wait_s, fresh=fresh)
    except indexer.NotSetUp as exc:
        logger.info("ℹ️ email index not run: %s", exc)
        return EXIT_NOT_SET_UP
    except indexer.UnknownFolder as exc:
        logger.error("❌ %s", exc.args[0])
        return EXIT_UNKNOWN_ROOT
    except indexer.EmailBusy:
        logger.warning("⚠️ another email run held the lock for %.0f s; nothing indexed", wait_s)
        return EXIT_BUSY
    if result["status"] != store.STATUS_OK:
        logger.error("❌ email index failed: %s", result["message"])
        return EXIT_FAILED
    return EXIT_OK


def cmd_email_import(source: str) -> int:
    try:
        import_index(Path(source), own_index_path())
    except (FileExistsError, ValueError, OSError) as exc:
        logger.error("❌ import refused: %s", exc)
        return EXIT_FAILED
    return EXIT_OK


def cmd_email_config() -> int:
    cfg = config.load()
    part = email_part(cfg, resolve_roots(cfg["roots"]))
    sys.stdout.write(json.dumps({"roots": part.roots, "db": str(part.index_path) if part.index_path else None,
                                 "own": part.own}) + "\n")
    return EXIT_OK


def cmd_email_status() -> int:
    path = own_index_path()
    logger.info("ℹ️ email index: %s (%s)", path, "%.1f MB" % (path.stat().st_size / 2**20) if path.exists() else "not built")
    for r in store.email_runs(5):
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["started_at"]))
        logger.info("ℹ️ %s %-6s %-11s %s", when, r["scope"][:40], r["status"], r["message"] or "")
    return EXIT_OK


def cmd_scan(keys: list[str], email: bool = True) -> int:
    try:
        results = scan_runner.run_scans(keys or None, trigger="cli")
    except ScanBusy:
        logger.warning("⚠️ another scan is running; nothing scanned")
        return EXIT_BUSY
    except KeyError as exc:
        logger.error("❌ %s", exc.args[0])
        return EXIT_UNKNOWN_ROOT
    failed = [r for r in results if r["status"] != store.STATUS_OK]
    if email:
        # The disk lock is free again: the email stage takes its own and reads this scan's index.
        cmd_email_index(None, 0.0, "cli")
    for r in results:
        if r["status"] == store.STATUS_OK:
            logger.info("✅ %s: %d files in %.1f s", r["root"], r["files"], r["seconds"])
        else:
            logger.error("❌ %s: %s", r["root"], r.get("message", "failed"))
    return EXIT_FAILED if failed else EXIT_OK


def cmd_roots() -> int:
    for root in resolve_roots(config.load()["roots"]):
        cur = store.current_index(root.key)
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(cur["finished_at"])) if cur else "never"
        size = _gib(int(cur["size"])) + " size, " + _gib(int(cur["alloc"])) + " on disk" if cur else ""
        logger.info("ℹ️ %-12s %-24s %-7s last scan %s %s", root.key, root.name, root.kind, when, size)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:2] == ["email", "batch"]:
        # Before any logging is set up here: the batch's stdout carries only its JSON document.
        from app.cli import email_batch

        return email_batch.main(argv[2:])
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan", help="scan roots now")
    scan.add_argument("--root", action="append", default=[], help="root key (default: all roots)")
    scan.add_argument("--no-email", action="store_true", help="leave the email index as it is")
    sub.add_parser("roots", help="list roots and their last scan")
    email = sub.add_parser("email", help="the email index").add_subparsers(dest="verb", required=True)
    index = email.add_parser("index", help="index the email folders now")
    index.add_argument("--root", default=None, help="one folder inside an email folder (default: every email folder)")
    index.add_argument("--wait", type=float, default=0.0, help="seconds to wait for a running email run")
    index.add_argument("--fresh", action="store_true", help="list the email folders now, not from the last scan")
    index.add_argument("--trigger", default="cli", help=argparse.SUPPRESS)
    imp = email.add_parser("import", help="copy another app's email index in, once")
    imp.add_argument("--from", dest="source", required=True, help="the email index file to copy")
    email.add_parser("status", help="the email index and its last runs")
    conf = email.add_parser("config", help="the email folders and index, as JSON")
    conf.add_argument("--json", action="store_true", required=True, help="print one JSON document")
    email.add_parser("batch", help="the Outlook archive verbs, one JSON document on stdout (email batch --help)")
    args = parser.parse_args(argv)
    if args.command == "scan":
        return cmd_scan(args.root, email=not args.no_email)
    if args.command == "email":
        if args.verb == "index":
            return cmd_email_index(args.root, args.wait, args.trigger, args.fresh)
        if args.verb == "config":
            return cmd_email_config()
        return cmd_email_import(args.source) if args.verb == "import" else cmd_email_status()
    return cmd_roots()


if __name__ == "__main__":
    sys.exit(main())
