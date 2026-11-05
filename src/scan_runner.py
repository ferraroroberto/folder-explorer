"""Run scans: one root after another, under the one scan lock, recorded in the state store.

The CLI, the app's Rescan button and the in-app schedule all come through
:func:`run_scans`, so they share the lock, the history and the index swap.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable, Optional

from src import config, store
from src.email import indexer
from src.email.settings import email_part
from src.roots import Root, resolve_roots
from src.scan_lock import ScanBusy, ScanLock
from src.scanner import exclude_keys, scan_to_index

logger = logging.getLogger(__name__)

# Progress rows are rewritten at most this often, so a fast walk does not
# turn into a stream of small state-database commits.
_PROGRESS_WRITE_SECONDS = 1.0


def lock() -> ScanLock:
    return ScanLock(store.data_dir() / "scan.lock")


def _scan_root(root: Root, trigger: str, exclude: frozenset[str] = frozenset()) -> dict[str, Any]:
    scan_id = store.begin_scan(root.key, root.path, trigger)
    index_name = store.index_name(root.key, scan_id)
    last = [0.0]

    def progress(counters: dict[str, int]) -> None:
        now = time.monotonic()
        if now - last[0] >= _PROGRESS_WRITE_SECONDS:
            last[0] = now
            store.update_progress(scan_id, counters)

    logger.info("ℹ️ scan %d started: root %s", scan_id, root.key)
    t0 = time.monotonic()
    try:
        counters = scan_to_index(root.path, store.index_dir() / index_name, root_name=root.name, on_progress=progress,
                                 exclude=exclude)
    except Exception as exc:  # noqa: BLE001 -- a failed root must not stop the others
        logger.warning("⚠️ scan %d of root %s failed: %s", scan_id, root.key, exc)
        store.finish_scan(scan_id, store.STATUS_FAILED, {}, None, str(exc))
        return {"root": root.key, "status": store.STATUS_FAILED, "message": str(exc)}
    store.finish_scan(scan_id, store.STATUS_OK, counters.as_dict(), index_name)
    elapsed = time.monotonic() - t0
    logger.info(
        "✅ scan %d of root %s: %d folders, %d files, %d errors, %d links skipped, %d excluded in %.1f s",
        scan_id, root.key, counters.folders, counters.files, counters.errors, counters.skipped_links, counters.excluded, elapsed,
    )
    return {"root": root.key, "status": store.STATUS_OK, "files": counters.files, "seconds": round(elapsed, 1)}


def run_scans(root_keys: Optional[list[str]] = None, trigger: str = "cli", held: Optional[ScanLock] = None) -> list[dict[str, Any]]:
    """Scan ``root_keys`` (default: every configured root) in order.

    Raises ``ScanBusy`` when another scan holds the lock. Pass ``held`` when the
    caller already took the lock (the app takes it before answering the
    request, so a second click gets its 409 straight away).
    """
    cfg = config.load()
    roots = resolve_roots(cfg["roots"])
    exclude = exclude_keys(cfg["exclude"])
    if root_keys:
        wanted = set(root_keys)
        unknown = wanted - {r.key for r in roots}
        if unknown:
            raise KeyError("unknown root: " + ", ".join(sorted(unknown)))
        roots = [r for r in roots if r.key in wanted]
    guard = held or lock()
    if held is None:
        guard.acquire()
    try:
        settled = store.settle_interrupted()
        if settled:
            logger.info("ℹ️ marked %d scan(s) left running by a stopped process as interrupted", settled)
        for stale in store.index_dir().glob("*.part"):
            stale.unlink(missing_ok=True)
        results = [_scan_root(r, trigger, exclude) for r in roots]
        store.prune_indexes()
        return results
    finally:
        guard.release()


def index_emails_after(trigger: str) -> None:
    """Run the email stage in its own process once the disk walk let go of its lock, and wait for it."""
    cfg = config.load()
    if email_part(cfg, resolve_roots(cfg["roots"])).stage_problem is not None:
        return
    try:
        code = indexer.spawn(trigger).wait()
    except OSError as exc:
        logger.warning("⚠️ the email stage could not start: %s", exc)
        return
    logger.info("ℹ️ email stage after the %s scan exited %d", trigger, code)


def settle_if_idle() -> bool:
    """Mark dead ``running`` scans interrupted, only when no scan holds the lock.

    Returns whether a scan is running (the lock was busy).
    """
    guard = lock()
    try:
        guard.acquire()
    except ScanBusy:
        return True
    try:
        store.settle_interrupted()
    finally:
        guard.release()
    return False


def start_background(root_keys: Optional[list[str]], trigger: str, on_done: Optional[Callable[[], None]] = None) -> threading.Thread:
    """Take the lock now (raising ``ScanBusy`` if held) and scan on a worker thread."""
    guard = lock()
    guard.acquire()

    def work() -> None:
        try:
            run_scans(root_keys, trigger, held=guard)
        except Exception as exc:  # noqa: BLE001 -- the worker thread must log, never die silently
            logger.warning("⚠️ background scan failed: %s", exc, exc_info=True)
        finally:
            if guard.held:
                guard.release()
        index_emails_after(trigger)
        if on_done is not None:
            on_done()

    thread = threading.Thread(target=work, name="folder-explorer-scan", daemon=True)
    thread.start()
    return thread


def schedule_due(schedule: str, at: str, last_run: Optional[float], now: datetime) -> bool:
    """Whether the in-app schedule should start a scan at ``now``.

    ``nightly`` fires once a day at or after ``at`` (HH:MM), ``hourly`` once an
    hour; ``last_run`` is when the previous scheduled scan started.
    """
    if schedule == "hourly":
        return last_run is None or now.timestamp() - last_run >= 3600
    if schedule == "nightly":
        hh, mm = (int(x) for x in at.split(":"))
        slot = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        return now >= slot and (last_run is None or last_run < slot.timestamp())
    return False


class Scheduler:
    """The in-app schedule: checks once a minute, starts a scan when due and the lock is free."""

    CHECK_SECONDS = 60.0

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._last_run: Optional[float] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="folder-explorer-schedule", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.CHECK_SECONDS):
            self.tick(datetime.now())

    def tick(self, now: datetime) -> bool:
        cfg = config.load()
        # Any scan counts, whoever started it: a Rescan at 03:05 already did
        # the nightly scan's work.
        started = [t for t in (self._last_run, store.last_started()) if t is not None]
        if not schedule_due(cfg["schedule"], cfg["schedule_time"], max(started) if started else None, now):
            return False
        try:
            start_background(None, "schedule")
        except ScanBusy:
            logger.info("ℹ️ scheduled scan skipped: another scan is running")
            return False
        self._last_run = now.timestamp()
        return True
