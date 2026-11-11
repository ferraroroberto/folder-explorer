"""Run the email stage: under its own lock, over the email folders or one of them, recorded in the history.

The disk walk and the email index each have a lock, so a scoped run (one
folder, after a mail was filed there) takes seconds and never waits behind a
multi-hour disk walk. A full run reads each email folder's manifest from its
root's current disk index (the walk already listed it) and lists a folder
itself only when no index covers it. Every run writes one row to the history
in ``state.sqlite3`` with its counts.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

from src import config, store
from src.email.manifest import Manifest, from_index, walk, without
from src.email.reader import read_facts
from src.email.schema import open_writer
from src.email.settings import EmailPart, email_part, is_under
from src.email.stage import Reader, Stage
from src.index import Index, open_index
from src.no_window import NO_WINDOW
from src.roots import Root, resolve_roots
from src.run_state import Derived, derive_run_state
from src.scan_lock import ScanBusy, ScanLock, probe_lock
from src.scanner import exclude_keys

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# How often a run waiting for the lock tries again.
_RETRY_S = 0.5
MB = 1024 * 1024


class EmailBusy(RuntimeError):
    """Another email run held the lock past the caller's wait."""


class NotSetUp(RuntimeError):
    """The email stage cannot run here; the message says why."""


class UnknownFolder(KeyError):
    """A scoped run named a folder outside every email folder."""


def lock() -> ScanLock:
    return ScanLock(store.data_dir() / "email.lock")


def take_lock(guard: ScanLock, wait_s: float) -> None:
    deadline = time.monotonic() + max(0.0, wait_s)
    while True:
        try:
            guard.acquire()
            return
        except ScanBusy as exc:
            if time.monotonic() >= deadline:
                raise EmailBusy("another email run holds the lock") from exc
            time.sleep(_RETRY_S)


def _from_scan(folder: str, roots: list[Root]) -> Optional[Manifest]:
    """The manifest of ``folder`` from the current index of the deepest root holding it."""
    for root in sorted(roots, key=lambda r: len(r.path), reverse=True):
        if not is_under(folder, root.path):
            continue
        scan = store.current_index(root.key)
        if scan is None:
            return None
        ix = Index(open_index(store.index_dir() / scan["index_file"]))
        try:
            return from_index(ix, root.path, folder)
        finally:
            ix.conn.close()
    return None


def manifests(part: EmailPart, scope: Optional[str], roots: list[Root], exclude: frozenset[str],
              fresh: bool = False) -> list[Manifest]:
    """Each folder's manifest: a listing of ``scope``, else the scan index per email folder.

    ``fresh`` lists every email folder now instead: a mail filed since the last
    scan is not in that scan's index.
    """
    if scope is not None:
        return [walk(scope, exclude=exclude)]
    out = []
    for folder in part.roots:
        m = None if fresh else _from_scan(folder, roots)
        out.append(without(m, exclude) if m is not None else walk(folder, exclude=exclude))
    return out


def exclude_for(cfg: dict[str, Any]) -> frozenset[str]:
    """The folders the email index leaves out: the global skip list plus ``email.exclude``."""
    return exclude_keys(list(cfg["exclude"]) + list(cfg["email"]["exclude"]))


def summary(counts: dict[str, int]) -> str:
    """The one-line outcome the history, the log and the Scans tab share."""
    text = "%d new, %d changed, %d re-read" % (counts.get("new", 0), counts.get("changed", 0), counts.get("reread", 0))
    if counts.get("downloaded"):
        text += " (%d downloaded, %d MB)" % (counts["downloaded"], round(counts["downloaded_bytes"] / MB))
    if counts.get("waiting"):
        text += ", %d waiting for budget" % counts["waiting"]
    if counts.get("failed"):
        text += ", %d failed" % counts["failed"]
    if counts.get("purged"):
        text += ", %d removed" % counts["purged"]
    if counts.get("cleaned"):
        text += ", %d previews cleaned" % counts["cleaned"]
    return text


def email_state(held: Optional[bool]) -> Derived:
    """Whether an email run is running, from the newest run row and the lock observation ``held``."""
    [newest] = store.email_runs(1) or [None]
    return derive_run_state(newest["status"] if newest else None, newest["message"] if newest else None,
                            held, any_run=True)


def lock_held() -> Optional[bool]:
    """Whether an email run holds the lock now; ``None`` when the lock could not be read."""
    return probe_lock(lock().path)


def activity() -> Derived:
    """The email run state now: the newest run row read against the email lock."""
    return email_state(lock_held())


def history(limit: int, held: Optional[bool]) -> list[dict[str, Any]]:
    """The newest runs first, each with the ``state`` and ``reason`` its row and the lock ``held`` give.

    ``live`` and ``attention`` are the same state as two flags, so the Scans tab marks a run without naming states.
    """
    out = []
    for r in store.email_runs(limit):
        d = derive_run_state(r["status"], r["message"], held)
        out.append({**r, **d.as_dict(), "live": d.running, "attention": d.attention})
    return out


def first_build(own: bool) -> Optional[Derived]:
    """The run state while this app's own index has been started but no run has finished ``ok``; else ``None``.

    The index file exists from the first run's start, so its presence says nothing about whether it is
    complete. An index no run built (imported) or one owned by another app is taken as it is.
    """
    if not own:
        return None
    recorded, finished_ok = store.email_history()
    return activity() if recorded and not finished_ok else None


def spawn(trigger: str, scope: Optional[str] = None, fresh: bool = False) -> "subprocess.Popen[bytes]":
    """Start ``python -m app.cli email index`` in its own windowless process, logging to ``email.log``.

    The web server never reads a mail itself: a download can stall for minutes.
    ``fresh`` lists every email folder now rather than reading the last scan's index.
    """
    cmd = [sys.executable, "-m", "app.cli", "email", "index", "--trigger", trigger]
    if scope:
        cmd += ["--root", scope]
    if fresh:
        cmd.append("--fresh")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    log = open(store.data_dir() / "email.log", "ab")
    try:
        return subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    finally:
        log.close()


def _publish(run_id: int, counts: dict[str, int]) -> None:
    """Store the run's counts so far, for the Scans tab; a failed write never stops the run."""
    try:
        store.update_email_run_counts(run_id, counts)
    except Exception as exc:  # noqa: BLE001 -- progress is only for display
        logger.warning("⚠️ email run %d: progress not stored: %s", run_id, exc)


def run(scope: Optional[str] = None, trigger: str = "cli", wait_s: float = 0.0, read: Optional[Reader] = None,
        fresh: bool = False) -> dict[str, Any]:
    """One email run; raises ``NotSetUp``, ``UnknownFolder`` or ``EmailBusy`` before touching anything."""
    cfg = config.load()
    roots = resolve_roots(cfg["roots"])
    part = email_part(cfg, roots)
    if part.stage_problem is not None or part.index_path is None:
        raise NotSetUp(part.stage_problem or "no email index")
    if scope is not None and not any(is_under(scope, f) for f in part.roots):
        raise UnknownFolder("not inside an email folder: " + scope)
    guard = lock()
    take_lock(guard, wait_s)
    try:
        settled = store.settle_email_runs()
        if settled:
            logger.info("ℹ️ marked %d email run(s) left running by a stopped process as interrupted", settled)
        run_id = store.begin_email_run(trigger, scope or "all")
        t0 = time.monotonic()
        try:
            lists = manifests(part, scope, roots, exclude_for(cfg), fresh)
            conn = open_writer(part.index_path)
            try:
                counts = Stage(conn, part.budget_bytes, read or read_facts,
                               lambda c: _publish(run_id, c)).run(lists).as_dict()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 -- a failed run is recorded and reported, never left "running"
            store.finish_email_run(run_id, store.STATUS_FAILED, {}, str(exc))
            logger.warning("⚠️ email run %d failed: %s", run_id, exc)
            return {"id": run_id, "status": store.STATUS_FAILED, "message": str(exc), "counts": {}}
        text = summary(counts)
        store.finish_email_run(run_id, store.STATUS_OK, counts, text)
        logger.info("✅ email run %d (%s): %s of %d files in %.1f s", run_id, scope or "all folders", text,
                    counts["files"], time.monotonic() - t0)
        return {"id": run_id, "status": store.STATUS_OK, "message": text, "counts": counts}
    finally:
        guard.release()
