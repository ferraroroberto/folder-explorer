"""The versioned archive API for other apps: ``/api/v1/archive/{plan,apply,revert,index}``, shapes in ``docs/service-api.md``.

Each call runs the CLI's own verb (``python -m app.cli email batch <verb>``, or
``email index``) as a windowless child with a timeout, so the semantics are the
CLI's and an Outlook dialog hangs the child, never this server. Only a caller
on this PC may use it: the verbs write user files and move mail. One Outlook
verb runs at a time. ``src.archive_api`` owns the outcomes and the envelope.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.webapp.auth import is_pc_itself
from app.webapp.routers.archive import OFF_REASON, bridge_off
from src import archive_api
from src.email import body as child

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/archive")

# Seconds a freshly started Outlook gets to answer (the CLI's --start-timeout), inside the call's own budget.
START_TIMEOUT_S = 60.0
# A plan walks the whole Inbox over COM and an apply saves each mail's files, so the budget is generous.
BATCH_TIMEOUT_S = 600.0
# A scoped re-index lists one folder and reads its new mail; the wait for another email run comes on top.
INDEX_TIMEOUT_S = 900.0
DEFAULT_WAIT_S = 60.0
MAX_WAIT_S = 600.0

_OUTLOOK = threading.Lock()


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanBody(_Body):
    candidates: int = Field(10, ge=0, le=100)
    message_ids: list[str] = []
    since: Optional[str] = None
    search: list[str] = []
    ref: Optional[str] = None
    folder: Literal["inbox", "sent"] = "inbox"
    mailbox: Optional[str] = None


class ApplyBody(_Body):
    decisions: list[dict[str, Any]]
    renumber: bool = False
    category: Optional[str] = None
    mailbox: Optional[str] = None


class RevertBody(_Body):
    items: list[dict[str, Any]]
    renumber: bool = False
    category: Optional[str] = None
    mailbox: Optional[str] = None


class IndexBody(_Body):
    root: str = Field(min_length=1)
    wait: float = Field(DEFAULT_WAIT_S, ge=0, le=MAX_WAIT_S)


def _respond(verb: str, body: dict[str, Any], status: int) -> JSONResponse:
    logger.info("ℹ️ archive v1 %s: %s%s", verb, body["outcome"], " (%s)" % body["code"] if body["code"] else "")
    return JSONResponse(body, status_code=status)


def _spawn(argv: list[str], timeout: float) -> "subprocess.CompletedProcess[str]":
    return child.run_child([sys.executable, "-m", "app.cli", *argv], timeout)


def _common(body: "ApplyBody | RevertBody | PlanBody") -> list[str]:
    """The flags every Outlook verb shares, one ``--flag=value`` argument each so a value starting with ``--`` is never a flag."""
    flags = ["--mailbox=%s" % body.mailbox] if body.mailbox is not None else []
    if not isinstance(body, PlanBody):
        flags += ["--renumber"] if body.renumber else []
        flags += ["--category=%s" % body.category] if body.category is not None else []
    return flags


def _batch(request: Request, verb: str, flags: list[str], payload: Optional[tuple[str, list[dict[str, Any]]]] = None
           ) -> JSONResponse:
    """Run one ``email batch`` verb in a child and answer with its document classified."""
    if not is_pc_itself(request):
        return _respond(verb, *archive_api.refused(verb, archive_api.NOT_THIS_PC, "archiving works on the PC only", 403))
    if bridge_off():
        return _respond(verb, *archive_api.refused(verb, archive_api.OUTLOOK_OFF, OFF_REASON, 503))
    if not _OUTLOOK.acquire(blocking=False):
        return _respond(verb, *archive_api.refused(
            verb, archive_api.BUSY, "an archive call is already running: one Outlook, one call at a time", 409))
    try:
        with tempfile.TemporaryDirectory(prefix="folder-explorer-archive-") as scratch:
            # The payload carries archive paths (a path carries the mail's subject): it lives for this call only.
            if payload is not None:
                path = Path(scratch) / (payload[0] + ".json")
                # runtime-write-exempt: a scratch folder in the system temp, removed when this call ends
                path.write_text(json.dumps(payload[1], ensure_ascii=False), encoding="utf-8")
                flags = ["--%s=%s" % (payload[0], path), *flags]
            argv = ["email", "batch", "--start-timeout=%s" % START_TIMEOUT_S, verb, *flags]
            try:
                proc = _spawn(argv, BATCH_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                return _respond(verb, *archive_api.unanswered(
                    verb, archive_api.TIMEOUT, "no answer within %.0f s: Outlook may be showing a dialog nobody can "
                    "see, and nothing is confirmed" % BATCH_TIMEOUT_S, 504))
            except OSError as exc:
                return _respond(verb, *archive_api.unanswered(
                    verb, archive_api.SPAWN_FAILED, "the archiver could not start: %s" % exc, 502))
    finally:
        _OUTLOOK.release()
    try:
        doc = json.loads(proc.stdout or "")
    except ValueError:
        logger.warning("⚠️ archive v1 %s exited %s with no document: %s", verb, proc.returncode, (proc.stderr or "")[-300:])
        doc = None
    return _respond(verb, *archive_api.from_document(verb, doc, proc.returncode))


@router.post("/plan")
def plan(request: Request, body: PlanBody) -> JSONResponse:
    """Rank archive folders for the mails of the Inbox (or Sent Items), or for the ones the filters select."""
    flags = ["--candidates=%d" % body.candidates, "--folder=%s" % body.folder, *_common(body)]
    flags += ["--message-id=%s" % m for m in body.message_ids] + ["--search=%s" % t for t in body.search]
    flags += ["--since=%s" % body.since] if body.since is not None else []
    flags += ["--ref=%s" % body.ref] if body.ref is not None else []
    return _batch(request, "plan", flags)


@router.post("/apply")
def apply(request: Request, body: ApplyBody) -> JSONResponse:
    """File each decided mail: write its ``.msg``, move it out of the Inbox and tag it."""
    return _batch(request, "apply", _common(body), ("decisions", body.decisions))


@router.post("/revert")
def revert(request: Request, body: RevertBody) -> JSONResponse:
    """Undo an apply: delete the files and put each mail back in the Inbox."""
    return _batch(request, "revert", _common(body), ("items", body.items))


@router.post("/index")
def index(request: Request, body: IndexBody) -> JSONResponse:
    """Re-index one folder inside the email folders, so the email index confirms what a run filed there."""
    if not is_pc_itself(request):
        return _respond("index", *archive_api.refused("index", archive_api.NOT_THIS_PC, "archiving works on the PC only", 403))
    argv = ["email", "index", "--root=%s" % body.root, "--wait=%s" % body.wait]
    try:
        proc = _spawn(argv, INDEX_TIMEOUT_S + body.wait)
    except subprocess.TimeoutExpired:
        return _respond("index", *archive_api.unanswered(
            "index", archive_api.TIMEOUT, "the re-index did not finish within %.0f s" % INDEX_TIMEOUT_S, 504))
    except OSError as exc:
        return _respond("index", *archive_api.unanswered(
            "index", archive_api.SPAWN_FAILED, "the re-index could not start: %s" % exc, 502))
    return _respond("index", *archive_api.from_index_exit(proc.returncode))
