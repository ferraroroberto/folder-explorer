"""The Archive sheet and the Outlook macro's hand-off: PC-only, every Outlook step in a child.

The server makes no COM call. Each Outlook step runs ``python -m app.outlook``
as a windowless child with a timeout, so a COM dialog hangs the child, never
the server. What filing a prepared mail needs (its EntryID and StoreID) stays
here under a token; the page only ever sends back that token, a folder and a
category, and the child checks the folder against the email folders again.
Each offered folder carries its place in the scan (root, scan id, folder id),
so the sheet's Open asks ``/api/open`` by id, never by a path.

The macro's entry hands its prepared mail to ``/archive/handoff``. A page on
this PC polls ``/archive/pending`` while it is visible, so the server knows
whether one is open to show the mail; when none is, the entry opens its own
window instead.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from contextlib import ExitStack
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request

from src import config, folder_search, macro_home, store
from src.config import PROJECT_ROOT
from src.desktop import outlook_exe
from src.email import body as child
from src.email.archive import DATE_PREFIX_CHOICES
from src.email.archive_sheet import ERR_FAILED, ERR_TIMEOUT, error_document
from src.email.settings import under_any
from src.outlook.bridge import pywin32_installed
from src.roots import resolve_roots
from app.webapp.auth import is_pc_itself
from app.webapp.state import POOL, locate_folder

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

MACRO_FILE = PROJECT_ROOT / "assets" / "ArchiveMacro.bas"

# One COM read of the mail plus a folder listing per suggestion.
PREPARE_TIMEOUT_S = 45.0
# Saving the .msg and its attachments into a OneDrive folder can take a while.
FILE_TIMEOUT_S = 90.0
SHOW_TIMEOUT_S = 30.0
# A page that polled within this many seconds is open to show a mail from the macro
# (it polls every 3 s while it is visible).
PAGE_LIVE_S = 8.0
# How long a prepared mail can still be filed, and how many are kept at once.
PREPARED_TTL_S = 30 * 60.0
PREPARED_MAX = 20
# A hand-off no page picked up within this long is dropped; the macro opened its own window.
HANDOFF_TTL_S = 60.0
# Folders offered when the sheet's "Another email folder" box is searched.
FOLDER_HITS = 8
# ``off`` keeps this process from ever starting the Outlook bridge: the e2e suite's
# disposable instance sets it, so no test can reach this PC's Outlook.
OUTLOOK_ENV = "FOLDER_EXPLORER_OUTLOOK"
OFF_REASON = "Outlook actions are off for this instance"


def bridge_off() -> bool:
    return os.environ.get(OUTLOOK_ENV, "").strip().lower() == "off"


class _Prepared:
    """Prepared mails by token, the macro's pending hand-off, and when a page last polled."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.items: dict[str, tuple[float, dict[str, str], dict[str, Any]]] = {}
        self.pending: Optional[tuple[float, dict[str, Any]]] = None
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


@router.post("/archive/current")
def current(request: Request) -> dict[str, Any]:
    """The sheet's document for the mail open or selected in Outlook."""
    _pc_only(request)
    return PREPARED.keep(with_locations(bridge(["prepare", "--current"], PREPARE_TIMEOUT_S)))


@router.post("/archive/handoff")
def handoff(request: Request, doc: dict[str, Any] = Body(...)) -> dict[str, bool]:
    """The macro's entry gives its prepared mail; ``shown`` says whether a page is open to show it."""
    _pc_only(request)
    public = PREPARED.keep(with_locations(doc))
    now = time.monotonic()
    with PREPARED.lock:
        shown = now - PREPARED.last_poll <= PAGE_LIVE_S
        if shown:
            PREPARED.pending = (now, public)
    logger.info("ℹ️ archive hand-off from the macro: %s", "a page shows it" if shown else "no page is open")
    return {"shown": shown}


@router.get("/archive/pending")
def pending(request: Request) -> dict[str, Any]:
    """The mail the macro handed over since the last poll, once; a page polls this while it is visible."""
    _pc_only(request)
    now = time.monotonic()
    with PREPARED.lock:
        PREPARED.last_poll = now
        item, PREPARED.pending = PREPARED.pending, None
    if item is None or now - item[0] > HANDOFF_TTL_S:
        return {"mail": None}
    return {"mail": item[1]}


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
    doc = bridge(args, FILE_TIMEOUT_S)
    if doc.get("files"):
        PREPARED.drop(str(body["token"]))
    return doc


@router.get("/archive/folders")
def folders(request: Request, q: str = Query("", max_length=200)) -> dict[str, Any]:
    """Folders inside the email folders whose path holds every word, for "Another email folder"."""
    _pc_only(request)
    cfg = config.load()
    email_roots = list((cfg.get("email") or {}).get("roots") or [])
    roots = resolve_roots(cfg["roots"])
    entries = []
    scan_ids = {}
    for root in roots:
        scan = store.current_index(root.key)
        if scan is not None:
            entries.append((root, POOL.get(store.index_dir() / scan["index_file"])))
            scan_ids[root.key] = scan["id"]
    with ExitStack() as stack:
        for _, (_, lock) in entries:
            stack.enter_context(lock)
        found = folder_search.search([(root, ix) for root, (ix, _) in entries], q, False, 0, limit=200)
    hits = [{"path": r["path"], "name": r["name"],
             "location": {"root": r["root"], "scan_id": scan_ids[r["root"]], "id": r["id"]}}
            for r in found["results"] if under_any(r["path"], email_roots)]
    return {"folders": hits[:FOLDER_HITS]}


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
    }
