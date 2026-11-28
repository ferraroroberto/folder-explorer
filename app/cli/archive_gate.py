"""``email batch`` on a machine with the Outlook archive block switched off: one JSON document, exit 2, no Outlook call.

Runs before ``email_batch`` is imported, so a machine without the block never loads the Outlook client or reads a
request. The answer is the envelope every other "cannot start" uses, with the ``outlook_unavailable`` code the callers
(other local apps) already handle.
"""

from __future__ import annotations

import json
import sys
from typing import Optional

from src import config, features
from src.email.batch import common as batch_common

EXIT_CANNOT_START = 2
ERROR_OUTLOOK_UNAVAILABLE = "outlook_unavailable"


def refusal(batch_args: list[str]) -> Optional[int]:
    """The exit code after printing the refusal while the archive block is off, else None."""
    if features.enabled(config.load(), "archive"):
        return None
    verb = next((a for a in batch_args if not a.startswith("-")), "")
    document = batch_common.error_document(verb, ERROR_OUTLOOK_UNAVAILABLE,
                                           "Outlook archiving is switched off on this machine (" + features.OFF_SENTENCE + ")")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    json.dump(document, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    sys.stdout.flush()
    return EXIT_CANNOT_START
