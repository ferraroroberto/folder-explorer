"""The Archive sheet and the Outlook macro's hand-off: PC-only, every Outlook step in a child.

The server makes no COM call. Each Outlook step runs ``python -m app.outlook``
as a windowless child with a timeout, so a COM dialog hangs the child, never
the server. What filing a prepared mail needs (its EntryID and StoreID) stays
here under a token; the page only ever sends back that token, a folder and a
category, and the child checks the folder against the email folders again.
Filing answers the page as soon as the child says the mail is on disk; the
tagging and the indexing that follow are collected from ``/archive/followup``.
Each offered folder carries its place in the scan (root, scan id, folder id),
so the sheet's Open asks ``/api/open`` by id, never by a path.

The macro's entry hands its mail to ``/archive/handoff`` before any ranking.
A page on this PC holds a poll of ``/archive/pending`` open while it is
visible, so the server knows whether one is open to show the mail, and the
sheet opens the moment it arrives; this process then ranks the folders with
its warm index and the next poll brings them. When no page is open, the entry
opens its own window instead.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Callable, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request

from app.webapp.auth import is_pc_itself
from app.webapp.state import ScanChanged, locate_folder, sheet_folders, sheet_subfolders
from src import config, macro_home
from src.config import PROJECT_ROOT
from src.desktop import outlook_exe
from src.email import archive_sheet
from src.email import body as child
from src.email.archive import DATE_PREFIX_CHOICES
from src.email.archive_sheet import ERR_FAILED, ERR_TIMEOUT, error_document
from src.email.suggester import EmailData
from src.outlook.bridge import pywin32_installed
from src.roots import resolve_roots

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

MACRO_FILE = PROJECT_ROOT / "assets" / "ArchiveMacro.bas"

# One COM read of the mail plus a folder listing per suggestion.
PREPARE_TIMEOUT_S = 45.0
# Saving the .msg and its attachments into a OneDrive folder can take a while.
FILE_TIMEOUT_S = 90.0
# What follows a save (one COM call to tag the mail, one process start to index its folder).
FOLLOW_UP_TIMEOUT_S = 60.0
# A follow-up's result waits this long for its page to ask for it.
FOLLOW_UP_TTL_S = 10 * 60.0
SHOW_TIMEOUT_S = 30.0
# Listing the shell's open windows is quick; a longer wait means the shell is stuck.
EXPLORER_TIMEOUT_S = 15.0
# A page that polled within this many seconds is open to show a mail from the macro
# (a waiting poll counts as polling on every tick, and a visible page asks again at once).
PAGE_LIVE_S = 8.0
# The longest a page's poll may wait for a hand-off: an idle page asks about twice a minute.
PENDING_WAIT_MAX_S = 25.0
# How often a waiting poll looks for a hand-off, which is the most it delays one.
PENDING_TICK_S = 0.025
# How long a prepared mail can still be filed, and how many are kept at once.
PREPARED_TTL_S = 30 * 60.0
PREPARED_MAX = 20
# A hand-off no page picked up within this long is dropped; the macro opened its own window.
HANDOFF_TTL_S = 60.0
# ``off`` keeps this process from ever starting the Outlook bridge: the e2e suite's
# disposable instance sets it, so no test can reach this PC's Outlook.
OUTLOOK_ENV = "FOLDER_EXPLORER_OUTLOOK"
OFF_REASON = "Outlook actions are off for this instance"


def bridge_off() -> bool:
    return os.environ.get(OUTLOOK_ENV, "").strip().lower() == "off"


class _Prepared:
    """Prepared mails by token, the macro's pending hand-off, and when a page last polled.

    ``pending`` is (when it was handed, the page's document, the hand-off's start); ``latest`` is the
    token of the newest hand-off, so a slow ranking of an older mail never replaces a newer one.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.items: dict[str, tuple[float, dict[str, str], dict[str, Any]]] = {}
        self.pending: Optional[tuple[float, dict[str, Any], float]] = None
        self.latest: Optional[str] = None
        self.last_poll = 0.0

    def keep(self, doc: dict[str, Any]) -> dict[str, Any]:
        """The page's copy of ``doc``: its ids kept here, a token in their place."""
        public = {k: v for k, v in doc.items() if k != "ids"}
        if not doc.get("ok") or not isinstance(doc.get("ids"), dict):
            return public
        token = uuid.uuid4().hex
        now = time.monotonic()
        with self.lock:
            for old in [t for t, (at, _, _) in self.items.items() if now - at > PREPARED_TTL_S]:
                del self.items[old]
            while len(self.items) >= PREPARED_MAX:
                del self.items[min(self.items, key=lambda t: self.items[t][0])]
            self.items[token] = (now, {k: str(v) for k, v in doc["ids"].items()}, public)
        return dict(public, token=token)

    def ids(self, token: str) -> Optional[dict[str, str]]:
        with self.lock:
            item = self.items.get(token)
        return item[1] if item and time.monotonic() - item[0] <= PREPARED_TTL_S else None

    def drop(self, token: str) -> None:
        with self.lock:
            self.items.pop(token, None)


PREPARED = _Prepared()


class _FollowUps:
    """What followed each filing, by its token: running, or its document until the page takes it."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.items: dict[str, tuple[float, Optional[dict[str, Any]]]] = {}

    def start(self, token: str) -> None:
        now = time.monotonic()
        with self.lock:
            for old in [t for t, (at, _) in self.items.items() if now - at > FOLLOW_UP_TTL_S]:
                del self.items[old]
            self.items[token] = (now, None)

    def finish(self, token: str, doc: dict[str, Any]) -> None:
        with self.lock:
            if token in self.items:
                self.items[token] = (time.monotonic(), doc)

    def drop(self, token: str) -> None:
        with self.lock:
            self.items.pop(token, None)

    def take(self, token: str) -> tuple[str, Optional[dict[str, Any]]]:
        """``("done", doc)`` once (the entry goes), ``("running", None)``, or ``("unknown", None)``."""
        with self.lock:
            item = self.items.get(token)
            if item is None:
                return "unknown", None
            if item[1] is None:
                return "running", None
            del self.items[token]
            return "done", item[1]


FOLLOW_UPS = _FollowUps()


def _pc_only(request: Request) -> None:
    if not is_pc_itself(request):
        raise HTTPException(status_code=403, detail="this works on the PC only")


def bridge(args: list[str], timeout: float) -> dict[str, Any]:
    """Run one ``app.outlook`` verb as a child and return its document; a hang or no answer is its own error."""
    if bridge_off():
        return error_document(ERR_FAILED, OFF_REASON + ".")
    try:
        proc = child.run_child([sys.executable, "-m", "app.outlook", *args], timeout)
    except subprocess.TimeoutExpired:
        logger.warning("⚠️ outlook %s: no answer within %.0f s", args[0], timeout)
        return error_document(ERR_TIMEOUT)
    try:
        doc = json.loads(proc.stdout or "")
    except ValueError:
        logger.warning("⚠️ outlook %s exited %s with no document: %s", args[0], proc.returncode, (proc.stderr or "")[-300:])
        return error_document(ERR_FAILED, "The Outlook bridge stopped without an answer (exit %s)." % proc.returncode)
    if not doc.get("ok"):
        logger.info("ℹ️ outlook %s: %s", args[0], (doc.get("error") or {}).get("code"))
    return doc


def _next_line(lines: "queue.Queue[Optional[str]]", timeout: float) -> tuple[str, Optional[str]]:
    """The child's next stdout line: ``("line", text)``, ``("eof", None)`` or ``("timeout", None)``."""
    try:
        line = lines.get(timeout=timeout)
    except queue.Empty:
        return "timeout", None
    return ("eof", None) if line is None else ("line", line)


def _document(line: Optional[str]) -> Optional[dict[str, Any]]:
    try:
        doc = json.loads(line or "")
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def bridge_file(args: list[str], timeout: float, follow_up_timeout: float,
                on_follow_up: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Run ``app.outlook file`` as a child and answer on its first line, the save's document.

    The child goes on to tag the mail and index the folder; a thread hands its second line to
    ``on_follow_up``, or an error document when it hangs or stops without one, so a failure after
    the page has its answer is still heard.
    """
    if bridge_off():
        return error_document(ERR_FAILED, OFF_REASON + ".")
    proc = child.start_child([sys.executable, "-m", "app.outlook", *args])
    lines: queue.Queue[Optional[str]] = queue.Queue()

    def read() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=read, name="outlook-file-read", daemon=True).start()
    kind, line = _next_line(lines, timeout)
    if kind == "timeout":
        proc.kill()
        logger.warning("⚠️ outlook file: no answer within %.0f s", timeout)
        return error_document(ERR_TIMEOUT)
    doc = _document(line)
    if doc is None:
        logger.warning("⚠️ outlook file exited %s with no document", proc.wait())
        return error_document(ERR_FAILED, "The Outlook bridge stopped without an answer (exit %s)." % proc.returncode)
    if not doc.get("files"):
        logger.info("ℹ️ outlook file: %s", (doc.get("error") or {}).get("code"))
        threading.Thread(target=proc.wait, name="outlook-file-exit", daemon=True).start()
        return doc

    def follow() -> None:
        kind, line = _next_line(lines, follow_up_timeout)
        after = _document(line)
        if kind == "timeout":
            proc.kill()
            after = error_document(ERR_TIMEOUT, "The mail was filed, but Outlook didn't answer while tagging it, "
                                                "so the category and the indexing aren't confirmed.")
        elif after is None:
            after = error_document(ERR_FAILED, "The mail was filed, but what follows the save stopped without an answer, "
                                               "so the category and the indexing aren't confirmed.")
        proc.wait()
        logger.info("ℹ️ outlook file: the follow-up ended %s", "ok" if after.get("ok") else (after.get("error") or {}).get("code"))
        on_follow_up(after)

    threading.Thread(target=follow, name="outlook-file-follow-up", daemon=True).start()
    return doc


def archive_status(cfg: dict[str, Any], pc: bool) -> dict[str, Any]:
    """Whether the Archive card and Find in mailbox run here, and why not when they don't."""
    reason = None
    if bridge_off():
        reason = OFF_REASON
    elif not pc:
        reason = "Archiving works on the PC"
    elif not pywin32_installed():
        reason = "Not set up here: pywin32 isn't installed"
    elif outlook_exe() is None:
        reason = "Not set up here: Outlook (classic) isn't installed"
    elif not (cfg.get("email") or {}).get("roots"):
        reason = "Not set up here: no email folders are set"
    return {"ready": reason is None, "reason": reason}


def with_locations(doc: dict[str, Any]) -> dict[str, Any]:
    """``doc`` with each suggested folder's place in the scan (``location``, ``None`` when unscanned)."""
    if doc.get("ok") and doc.get("suggestions"):
        roots = resolve_roots(config.load()["roots"])
        for s in doc["suggestions"]:
            s["location"] = locate_folder(roots, str(s.get("folder_path", "")))
    return doc


def ranked_doc(doc: dict[str, Any], data: EmailData) -> dict[str, Any]:
    """``doc`` (a document still ranking) with its folders, ranked in this process, whose index is warm.

    A ranking that fails offers no folder, so the sheet still files through "Find a folder".
    """
    try:
        return archive_sheet.ranked(config.load(), doc, data)
    except Exception:
        logger.exception("❌ archive: ranking the folders failed")
        return dict(doc, suggestions=[], ranking=False)


def _rank_later(work: Callable[[], None]) -> None:
    """Rank a handed-over mail off the request, so the macro's entry hears ``shown`` at once."""
    threading.Thread(target=work, name="archive-rank", daemon=True).start()


def _publish_ranked(public: dict[str, Any], data: EmailData, started: float) -> None:
    """Rank the handed-over mail and give the page its folders, unless a newer hand-off came in meanwhile."""
    full = with_locations(ranked_doc(public, data))
    with PREPARED.lock:
        if PREPARED.latest != public.get("token"):
            logger.info("ℹ️ archive: a newer hand-off came in, so this mail's folders are dropped")
            return
        PREPARED.pending = (time.monotonic(), full, started)


def _take_pending() -> Optional[dict[str, Any]]:
    """The waiting hand-off, once; logs how long it took to reach the page since the macro's entry handed it."""
    now = time.monotonic()
    with PREPARED.lock:
        PREPARED.last_poll = now
        item, PREPARED.pending = PREPARED.pending, None
    if item is None or now - item[0] > HANDOFF_TTL_S:
        return None
    _, doc, started = item
    logger.info("⏱️ archive hand-off ms: %s reached the page=%.0f", "mail" if doc.get("ranking") else "folders",
                (now - started) * 1000)
    return doc


@router.post("/archive/current")
def current(request: Request) -> dict[str, Any]:
    """The sheet's document for the mail open or selected in Outlook; the child reads it, this process ranks it."""
    _pc_only(request)
    doc = bridge(["prepare", "--current", "--defer-ranking"], PREPARE_TIMEOUT_S)
    data = archive_sheet.rank_data(doc.pop("rank_input", None))
    if doc.get("ok") and data is not None:
        doc = ranked_doc(doc, data)
    return PREPARED.keep(with_locations(doc))


@router.post("/archive/handoff")
def handoff(request: Request, doc: dict[str, Any] = Body(...)) -> dict[str, bool]:
    """The macro's entry gives its mail; ``shown`` says whether a page is open to show it.

    A mail still ranking is shown at once and ranked here right after, and the page gets the same
    token again with its folders filled in.
    """
    _pc_only(request)
    started = time.monotonic()
    data = archive_sheet.rank_data(doc.pop("rank_input", None))
    ranking = bool(doc.get("ok") and doc.get("ranking") and data is not None)
    if doc.get("ranking") and not ranking:
        doc = dict(doc, ranking=False)
    public = PREPARED.keep(with_locations(doc))
    now = time.monotonic()
    with PREPARED.lock:
        shown = now - PREPARED.last_poll <= PAGE_LIVE_S
        if shown:
            PREPARED.pending = (now, public, started)
            PREPARED.latest = public.get("token")
    if shown and ranking and data is not None:
        _rank_later(lambda: _publish_ranked(public, data, started))
    logger.info("ℹ️ archive hand-off from the macro: %s", "a page shows it" if shown else "no page is open")
    return {"shown": shown}


@router.get("/archive/pending")
async def pending(request: Request, wait: float = Query(0.0, ge=0.0, le=PENDING_WAIT_MAX_S)) -> dict[str, Any]:
    """The mail the macro handed over since the last poll, once; a page polls this while it is visible.

    ``wait`` holds the poll open up to that many seconds and answers the moment a hand-off arrives, so
    the sheet opens without waiting for the next poll. A page that went away ends the wait.
    """
    _pc_only(request)
    deadline = time.monotonic() + wait
    while True:
        doc = _take_pending()
        if doc is not None:
            return {"mail": doc}
        if time.monotonic() >= deadline:
            return {"mail": None}
        await asyncio.sleep(PENDING_TICK_S)
        if await request.is_disconnected():
            return {"mail": None}


@router.post("/archive/file")
def file_mail(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """File the prepared mail in the chosen folder, with the chosen category or none.

    ``duplicate: true`` files another copy of an archived one; ``date_prefix`` is ``auto``, ``on`` or ``off``.
    """
    _pc_only(request)
    ids = PREPARED.ids(str(body.get("token", "")))
    folder = body.get("folder")
    if ids is None:
        raise HTTPException(status_code=404, detail="this mail is no longer prepared: open the sheet again")
    if not isinstance(folder, str) or not folder.strip():
        raise HTTPException(status_code=400, detail="pick a folder first")
    category = body.get("category") or ""
    # Only a literal true asks for another copy of a mail that is already archived.
    duplicate = body.get("duplicate") is True
    date_prefix = body.get("date_prefix", "auto")
    if not isinstance(date_prefix, str) or date_prefix not in DATE_PREFIX_CHOICES:
        raise HTTPException(status_code=400, detail="date_prefix must be auto, on or off")
    # One "--flag=value" argument each, so a value that starts with "--" is never read as a flag.
    args = ["file", "--entry-id=" + ids["entry_id"], "--store-id=" + ids["store_id"], "--folder=" + folder,
            "--category=" + str(category), "--date-prefix=" + date_prefix]
    if duplicate:
        args.append("--duplicate")
    token = str(body["token"])
    started = time.monotonic()
    FOLLOW_UPS.start(token)
    doc = bridge_file(args, FILE_TIMEOUT_S, FOLLOW_UP_TIMEOUT_S, lambda after: FOLLOW_UPS.finish(token, after))
    if doc.get("files"):
        PREPARED.drop(token)
        # The page asks /archive/followup with the same token for what the tagging and the indexing came to.
        doc["follow_up"] = True
    else:
        FOLLOW_UPS.drop(token)
    logger.info("⏱️ archive file: the page had its answer in %.0f ms", (time.monotonic() - started) * 1000)
    return doc


@router.get("/archive/followup")
async def followup(request: Request, token: str = Query(..., max_length=64),
                   wait: float = Query(0.0, ge=0.0, le=PENDING_WAIT_MAX_S)) -> dict[str, Any]:
    """What followed a filing (the category, the indexing): ``{"state": "running"}`` or ``{"state": "done", "result"}``.

    ``wait`` holds the poll open until it ends; the result is given once, and 404 says no filing under that token is known.
    """
    _pc_only(request)
    deadline = time.monotonic() + wait
    while True:
        state, doc = FOLLOW_UPS.take(token)
        if state == "unknown":
            raise HTTPException(status_code=404, detail="no filing is following up under this token")
        if state == "done":
            return {"state": "done", "result": doc}
        if time.monotonic() >= deadline:
            return {"state": "running"}
        await asyncio.sleep(PENDING_TICK_S)
        if await request.is_disconnected():
            return {"state": "running"}


@router.post("/archive/explorer")
def explorer(request: Request) -> dict[str, Any]:
    """The folder open in the foremost File Explorer window on this PC, when it is inside an email folder.

    Answers the sheet's document shape: ``folder_path`` and its place in the scan, or an error whose code says
    whether Windows couldn't be asked, no Explorer folder is open, or the folder is outside the email folders.
    """
    _pc_only(request)
    doc = bridge(["explorer"], EXPLORER_TIMEOUT_S)
    if doc.get("ok"):
        doc["location"] = locate_folder(resolve_roots(config.load()["roots"]), str(doc["folder_path"]))
    return doc


@router.get("/archive/folders")
def folders(request: Request, q: str = Query("", max_length=200)) -> dict[str, Any]:
    """Every folder Quick search finds for the same words, those a mail can be filed in first, for "Find a folder".

    Each folder says ``fileable`` (inside the email folders) and carries its place in the scan; ``total`` counts
    every match, of which the first rows are listed.
    """
    _pc_only(request)
    return sheet_folders(config.load(), q)


@router.get("/archive/subfolders")
def subfolders(request: Request, root: str = Query(..., max_length=200), scan_id: int = Query(...),
               id: int = Query(...)) -> dict[str, Any]:
    """The subfolders of the folder a listed row names by its place in the scan, in the same shape as ``/archive/folders``.

    Never takes a path: the folder is looked up by root, scan id and folder id, and 409 says a newer scan replaced it.
    """
    _pc_only(request)
    try:
        return sheet_subfolders(config.load(), root, scan_id, id)
    except ScanChanged as exc:
        raise HTTPException(status_code=409, detail="the index changed since this list was found: search again") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="no such folder") from exc


@router.get("/archive/macro")
def macro(request: Request) -> dict[str, Any]:
    """The VBA template to paste into Outlook, without the module header the editor's import reads.

    ``setup`` (the ``setx`` line with this PC's checkout and whether the variable is set) is
    given to a page on this PC only: it names a folder, and the variable is this PC's.
    """
    text = MACRO_FILE.read_text(encoding="utf-8")
    return {
        "text": "\n".join(line for line in text.splitlines() if not line.startswith("Attribute ")).strip() + "\n",
        "setup": macro_home.setup(PROJECT_ROOT) if is_pc_itself(request) else None,
        "prefer_window": config.load()["macro"]["prefer_window"],
    }
