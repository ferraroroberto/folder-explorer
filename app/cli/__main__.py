"""``python -m app.cli``: scan from the command line, with no app or tray running.

    python -m app.cli scan              scan every configured root
    python -m app.cli scan --root c     scan one root (repeat --root for more)
    python -m app.cli roots             list the roots and their last scan

Exit codes: 0 every root scanned, 1 a root failed, 2 another scan holds the
lock (nothing was scanned), 3 an unknown root key.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from src import config, scan_runner, store
from src.roots import resolve_roots
from src.scan_lock import ScanBusy

logger = logging.getLogger("app.cli")

EXIT_OK, EXIT_FAILED, EXIT_BUSY, EXIT_UNKNOWN_ROOT = 0, 1, 2, 3


def _gib(n: int) -> str:
    return "%.1f GiB" % (n / 2**30)


def cmd_scan(keys: list[str]) -> int:
    try:
        results = scan_runner.run_scans(keys or None, trigger="cli")
    except ScanBusy:
        logger.warning("⚠️ another scan is running; nothing scanned")
        return EXIT_BUSY
    except KeyError as exc:
        logger.error("❌ %s", exc.args[0])
        return EXIT_UNKNOWN_ROOT
    failed = [r for r in results if r["status"] != store.STATUS_OK]
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
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan", help="scan roots now")
    scan.add_argument("--root", action="append", default=[], help="root key (default: all roots)")
    sub.add_parser("roots", help="list roots and their last scan")
    args = parser.parse_args(argv)
    return cmd_scan(args.root) if args.command == "scan" else cmd_roots()


if __name__ == "__main__":
    sys.exit(main())
