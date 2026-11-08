"""``python -m app.cli folders search``: folder search over every root's index, one JSON document on stdout.

    python -m app.cli folders search --query "<words>" [--limit N] --json

For another local app (life-os's folders lookup) that wants the Search tab's
folder results without the web app running. The document is the Search tab's
own answer plus ``schema_version`` and ``query``: ``results`` (each with
``path``, the full path on disk, ``display``, with the configured leading
components hidden, and ``score``, 0 to 1, 1 when every word matched exactly), ``total``, ``truncated`` and ``needs_rescan`` (the roots
whose index predates folder search). The configured ``folder_search`` options
apply: folders of people fold into one result, and ``skip_levels`` shortens
``display`` only.

Exit codes: 0 searched (no match is still 0, with empty ``results``), 1 the
query has no words, 4 not set up here (no root has a finished scan). A refusal
is still one document: ``{"schema_version": 1, "error": {"code", "message"}}``.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from src import config, folder_search
from src.roots import resolve_roots

SCHEMA_VERSION = 1
EXIT_OK, EXIT_BAD_QUERY, EXIT_NOT_SET_UP = 0, 1, 4
# The most results one call returns: the search reads this many candidates per root.
MAX_LIMIT = folder_search.CANDIDATES


def _limit(raw: str) -> int:
    value = int(raw)
    if not 1 <= value <= MAX_LIMIT:
        raise argparse.ArgumentTypeError("--limit must be between 1 and %d" % MAX_LIMIT)
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli folders")
    sub = parser.add_subparsers(dest="verb", required=True)
    search = sub.add_parser("search", help="search folders by the words in their path, as JSON")
    search.add_argument("--query", required=True, help="words that must all appear in the folder's path")
    search.add_argument("--limit", type=_limit, default=folder_search.LIMIT, help="results to return")
    search.add_argument("--json", action="store_true", required=True, help="print one JSON document")
    return parser


def _emit(doc: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps({"schema_version": SCHEMA_VERSION, **doc}, ensure_ascii=False) + "\n")


def run_search(query: str, limit: int) -> int:
    """Search every scanned root and print the document; the exit code says which case it was."""
    if not folder_search.parse_terms(query):
        _emit({"error": {"code": "bad_query", "message": "--query has no words to search for"}})
        return EXIT_BAD_QUERY
    cfg = config.load()
    opts = cfg["folder_search"]
    with folder_search.open_scanned(resolve_roots(cfg["roots"])) as indexes:
        if not indexes:
            _emit({"error": {"code": "not_set_up", "message": "no root has a finished scan yet"}})
            return EXIT_NOT_SET_UP
        found = folder_search.search(indexes, query, opts["prune_people"], opts["skip_levels"], limit,
                                      opts["onedrive_prevails"])
    _emit({"query": query, **found})
    return EXIT_OK


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = _parser().parse_args(argv)
    return run_search(args.query, args.limit)
