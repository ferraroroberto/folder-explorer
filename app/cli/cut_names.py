"""``email batch fix-cut-names``: the flags, the run and the error codes of the verb that renames archived mails
whose name lost its first letters (``src/email/batch/fix_cut_names.py`` holds the rename).

It touches neither Outlook nor COM, so ``email_batch`` runs it before any mailbox work. Like the other verbs it
prints one JSON document; the run cannot start (exit 2) with `error.code` ``bad_input`` (an unusable undo log),
``email_not_set_up`` (no email index to read) or ``email_busy`` (an index run holds the email lock).
"""
from __future__ import annotations

import argparse
import logging
from typing import Any, Callable

from src.email import indexer
from src.email.batch import common as batch_common
from src.email.batch import fix_cut_names as verb

logger = logging.getLogger(__name__)

VERB = "fix-cut-names"
ERROR_EMAIL_NOT_SET_UP = "email_not_set_up"
ERROR_EMAIL_BUSY = "email_busy"


def add_parser(sub: Any) -> None:
    p = sub.add_parser(
        VERB,
        help="Rename archived mails whose name lost its first letters to the full subject "
             "(a dry run unless --apply; writes an undo log first).",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="List the renames and change nothing (the default).")
    mode.add_argument("--apply", action="store_true", help="Rename the files and follow them in the index.")
    mode.add_argument("--revert", metavar="LOG", help="Undo the renames of this undo log.")
    p.add_argument("--root", default=None, help="One folder inside an email folder (default: every email folder).")
    p.add_argument("--wait", type=float, default=0.0, help="Seconds to wait for a running email run.")


def run(cfg: dict[str, Any], args: argparse.Namespace, emit: Callable[[dict[str, Any]], None],
        fail: Callable[..., int]) -> int:
    """Run the verb; ``emit`` prints the document and returns, ``fail`` prints an error document and returns exit 2."""
    try:
        if args.revert:
            document = verb.revert_cut_names(cfg, args.revert, args.wait)
        else:
            document = verb.fix_cut_names(cfg, apply=args.apply, scope=args.root, wait_s=args.wait)
    except verb.UndoLogError as exc:
        return fail(VERB, batch_common.ERROR_BAD_INPUT, str(exc))
    except indexer.NotSetUp as exc:
        return fail(VERB, ERROR_EMAIL_NOT_SET_UP, str(exc))
    except indexer.EmailBusy as exc:
        return fail(VERB, ERROR_EMAIL_BUSY, str(exc))
    except Exception as exc:
        logger.exception("fix-cut-names failed")
        return fail(VERB, batch_common.ERROR_RUN_FAILED, f"{type(exc).__name__}: {exc}")
    emit(document)
    return 0
