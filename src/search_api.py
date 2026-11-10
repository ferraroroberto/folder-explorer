"""The versioned search contract another app calls (``/api/v1/search/...``): states, hit shapes and the page link.

The routes in ``app/webapp/routers/search_v1.py`` do the reading; this module
decides what each answer says, so the shapes ``docs/service-api.md`` documents
live in one place. Every answer carries ``schema_version`` and a ``state``:

- ``ok``: the part searched (no match is still ``ok``, with an empty list);
- ``not-configured``: this PC cannot search that part yet, ``reason`` says why;
- ``indexing``: the part has nothing to search yet and its first index is being built (for email, until the
  first run has finished ``ok``, though the index file exists from that run's start);
- ``error``: the index is there but could not be read, ``reason`` carries the error.

A caller tells "folder-explorer is down" by the request failing, never by an
empty list, and tells the other three cases apart by ``state``. A part that is
``not-configured`` because its first run failed, stopped or could not be told
says so in ``reason``, and the status answer carries the run's own ``run`` state
(``idle``, ``running``, ``ok``, ``failed``, ``interrupted`` or ``unknown``, see
``docs/run-state.md``) so a caller never matches the prose.
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote

from src.email.search import MARK_END, MARK_START
from src.email.settings import EmailPart
from src.run_state import Derived, RunState

SCHEMA_VERSION = 1
OK, NOT_CONFIGURED, INDEXING, ERROR = "ok", "not-configured", "indexing", "error"
# HTTP status per state: a caller that reads only the status still tells a refusal from a result.
HTTP_STATUS = {OK: 200, NOT_CONFIGURED: 503, INDEXING: 503, ERROR: 500}

# Results per call when the caller names no limit, and the most one call returns.
DEFAULT_LIMIT = 30
MAX_LIMIT = 200

# The folder-hit and email-hit fields of the contract, in the order docs/service-api.md lists them.
FOLDER_FIELDS = ("root", "root_name", "kind", "id", "name", "path", "depth", "score", "size", "files", "people")
EMAIL_FIELDS = ("id", "subject", "from_name", "from_addr", "recipients", "date", "folder", "path", "filename",
                "snippet", "flagged", "attachments", "size")


def doc(state: str, reason: Optional[str] = None, **fields: Any) -> dict[str, Any]:
    """One answer: the version, the state, why it isn't ``ok``, then the part's own fields."""
    return {"schema_version": SCHEMA_VERSION, "state": state, "reason": reason, **fields}


def page_link(mode: str, q: str) -> str:
    """The app's own Search tab for the same words, relative to the app's address."""
    return "/#search?mode=" + mode + "&q=" + quote(q.strip(), safe="")


def _unfinished(what: str, run: Derived) -> str:
    """Why the first ``what`` has not produced anything to search, in the words of the run's state."""
    if run.state is RunState.FAILED:
        return "the first %s failed: %s" % (what, run.reason)
    if run.state is RunState.INTERRUPTED:
        return "the first %s did not finish: %s" % (what, run.reason)
    return "can't tell whether the first %s is still going: %s" % (what, run.reason)


def folders_state(configured_roots: int, scanned_roots: int, scan: Optional[Derived]) -> tuple[str, Optional[str]]:
    """Whether folder search can answer, from the roots set up, the roots with a finished scan and the scan state.

    ``scan`` is only read when no root has a finished scan; ``None`` then reads as no scan having run.
    """
    if scanned_roots:
        return OK, None
    if not configured_roots:
        return NOT_CONFIGURED, "no folders to scan are set up here"
    if scan is not None and scan.state is RunState.RUNNING:
        return INDEXING, "the first scan is running"
    if scan is not None and scan.state in (RunState.FAILED, RunState.INTERRUPTED, RunState.UNKNOWN):
        return NOT_CONFIGURED, _unfinished("scan", scan)
    return NOT_CONFIGURED, "no root has a finished scan yet"


def emails_state(part: EmailPart, run: Optional[Derived], first_build: Optional[Derived]) -> tuple[str, Optional[str]]:
    """Whether email search can answer, from the email part's settings and the email run state.

    ``run`` is read only when the part is not ready; ``first_build`` is the run state while this app's own
    index has no run that finished ``ok`` (``indexer.first_build``), else ``None``.
    """
    if not part.ready:
        if run is not None and run.state is RunState.RUNNING and part.roots:
            return INDEXING, "the email index is being built"
        return NOT_CONFIGURED, part.problem
    if first_build is None:
        return OK, None
    if first_build.state is RunState.RUNNING:
        return INDEXING, "the first email index is being built"
    return NOT_CONFIGURED, _unfinished("email index run", first_build)


def folder_hit(hit: dict[str, Any]) -> dict[str, Any]:
    """One folder result in the contract's shape."""
    return {k: hit[k] for k in FOLDER_FIELDS}


def email_hit(hit: dict[str, Any]) -> dict[str, Any]:
    """One email hit in the contract's shape; ``attachments`` and ``size`` are null on an index without them."""
    return {k: hit.get(k) for k in EMAIL_FIELDS}


MARKS = {"start": MARK_START, "end": MARK_END}
