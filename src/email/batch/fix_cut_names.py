"""
The ``fix-cut-names`` verb: rename archived mails whose file name lost its first letters.

Before the reply-prefix rule required its colon, "Recibo de pago" was filed as
``cibo de pago``. ``email subjects`` lists those files; this verb renames them
to the full subject (``subject_audit`` decides which, reading each mail's
subject through the one reader under the email stage's budget and lock), keeps
the date and sequence prefix, and follows the rename in the index.

- **Dry run by default.** ``apply=False`` reports the renames and touches nothing.
- **Nothing is overwritten.** A name already taken is a clash: that file is left
  as it is and counted.
- **Only inside ``email.roots``.** Both the old and the new path are checked; a
  file outside is skipped, never renamed.
- **The undo log is written first.** ``cut-names-undo-<time>.json`` in the
  runtime-data folder lists every planned rename (old → new) before the first
  one runs; ``revert_cut_names`` restores from it under the same guards.
- **The lock is held for the whole run**, so no index run starts between the
  read and the rename. The disk walk's own index follows at the next scan.

The verb touches neither Outlook nor COM.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from src import store
from src.atomic_write import atomic_write_text
from src.email import indexer, subject_audit
from src.email.archive_paths import archive_roots as _archive_roots
from src.email.archive_paths import resolve_under_roots
from src.email.batch.common import header
from src.email.repository import EmailRepository, open_index

logger = logging.getLogger(__name__)

UNDO_PREFIX = "cut-names-undo-"
UNDO_VERSION = 1

OUTCOME_PLANNED = "planned"
OUTCOME_RENAMED = "renamed"
OUTCOME_CLASH = "clash"                    # the target name is taken; nothing was renamed
OUTCOME_MISSING = "missing"                # the file is gone (or, on revert, was never renamed)
OUTCOME_OUTSIDE_ROOTS = "outside_roots"
OUTCOME_FAILED = "failed"                  # the OS refused the rename


class UndoLogError(ValueError):
    """The undo log is missing, unreadable or not one this verb wrote."""


def _inside(roots: list[Path], *paths: str) -> bool:
    return all(resolve_under_roots(p, roots)[0] is not None for p in paths)


def _move(repo: EmailRepository, roots: list[Path], old: str, new: str) -> tuple[str, int]:
    """Rename ``old`` to ``new`` and point the index row at it; returns ``(outcome, rows updated)``."""
    if not _inside(roots, old, new):
        return OUTCOME_OUTSIDE_ROOTS, 0
    if not os.path.lexists(old):
        return OUTCOME_MISSING, 0
    if os.path.lexists(new):
        return OUTCOME_CLASH, 0
    try:
        os.rename(old, new)
    except FileNotFoundError:
        return OUTCOME_MISSING, 0
    except OSError as exc:
        logger.warning("⚠️ fix-cut-names: %s not renamed: %s", os.path.basename(old), exc)
        return OUTCOME_FAILED, 0
    # Nothing was on disk at `new`, so a row there is stale; `file_path` is UNIQUE and would refuse the update.
    try:
        repo.delete_by_path(new)
        updated = repo.update_path(old, new)
        repo.commit()
    except sqlite3.Error as exc:
        logger.error("❌ fix-cut-names: renamed %s but the index did not follow: %s", os.path.basename(new), exc)
        return OUTCOME_RENAMED, 0
    return OUTCOME_RENAMED, updated


def _write_undo_log(renames: list[dict[str, str]]) -> Optional[Path]:
    """The undo log, written before the first rename; ``None`` when there is nothing to undo."""
    if not renames:
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = store.data_dir() / f"{UNDO_PREFIX}{stamp}.json"
    body = {"version": UNDO_VERSION, "created": datetime.now().astimezone().isoformat(timespec="seconds"),
            "renames": renames}
    atomic_write_text(path, json.dumps(body, ensure_ascii=False, indent=2) + "\n")
    return path


def _counts(items: list[dict[str, str]], updated: int) -> dict[str, int]:
    def n(outcome: str) -> int:
        return sum(1 for i in items if i["outcome"] == outcome)
    return {"renamed": n(OUTCOME_RENAMED), "clashes": n(OUTCOME_CLASH),
            "skipped": n(OUTCOME_MISSING) + n(OUTCOME_OUTSIDE_ROOTS), "failed": n(OUTCOME_FAILED),
            "index_rows_updated": updated}


def fix_cut_names(
    cfg: dict[str, Any], *, apply: bool = False, scope: Optional[str] = None, wait_s: float = 0.0,
    read: Optional[Callable[[str, int], dict[str, Any]]] = None,
) -> dict[str, Any]:
    """List (or, with ``apply``, rename) the archived mails whose name lost its first letters.

    Raises ``indexer.NotSetUp`` / ``indexer.EmailBusy`` before anything is read.
    """
    roots = _archive_roots(cfg)
    items: list[dict[str, str]] = []
    updated = 0
    undo: Optional[Path] = None
    with subject_audit.audited(scope, wait_s, read, cfg) as result:
        plan = [{"old_path": c.path, "new_path": os.path.join(os.path.dirname(c.path), c.should_be)}
                for c in result.cut]
        if not apply:
            items = [{**p, "outcome": OUTCOME_PLANNED} for p in plan]
        else:
            undo = _write_undo_log([{"old": p["old_path"], "new": p["new_path"]} for p in plan])
            conn = open_index(cfg)
            try:
                repo = EmailRepository(conn)
                for p in plan:
                    outcome, rows = _move(repo, roots, p["old_path"], p["new_path"])
                    items.append({**p, "outcome": outcome})
                    updated += rows
            finally:
                conn.close()
        audit_counts = result.counts()
    counts = {**audit_counts, **_counts(items, updated)}
    logger.info("ℹ️ fix-cut-names (%s): %s", "apply" if apply else "dry run", counts)
    return {**header("fix-cut-names"), "dry_run": not apply, "counts": counts, "renames": items,
            "undo_log": str(undo) if undo is not None else None}


def _read_undo_log(path: str) -> list[dict[str, str]]:
    try:
        body = json.loads(Path(path).read_text(encoding="utf-8"))
        renames = body["renames"]
        if body.get("version") != UNDO_VERSION or not isinstance(renames, list):
            raise ValueError("not an undo log of this verb")
        return [{"old": str(r["old"]), "new": str(r["new"])} for r in renames]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise UndoLogError(f"{path}: {exc}") from exc


def revert_cut_names(cfg: dict[str, Any], log_path: str, wait_s: float = 0.0) -> dict[str, Any]:
    """Put each file of an undo log back under its old name, under the same guards as the rename.

    A file that is not at its new name (the rename never ran, or it moved since)
    is ``missing`` and a taken old name is a ``clash``; neither is touched.
    Raises ``UndoLogError`` before anything is renamed when the log is unusable,
    and ``indexer.EmailBusy`` when an index run holds the email lock.
    """
    renames = _read_undo_log(log_path)
    roots = _archive_roots(cfg)
    items: list[dict[str, str]] = []
    updated = 0
    guard = indexer.lock()
    indexer.take_lock(guard, wait_s)
    try:
        conn = open_index(cfg)
        try:
            repo = EmailRepository(conn)
            for r in renames:
                outcome, rows = _move(repo, roots, r["new"], r["old"])
                items.append({"old_path": r["new"], "new_path": r["old"], "outcome": outcome})
                updated += rows
        finally:
            conn.close()
    finally:
        guard.release()
    counts = _counts(items, updated)
    logger.info("ℹ️ fix-cut-names revert: %s", counts)
    return {**header("fix-cut-names"), "dry_run": False, "revert": True, "counts": counts, "renames": items,
            "undo_log": log_path}


__all__ = ["UndoLogError", "fix_cut_names", "revert_cut_names"]
