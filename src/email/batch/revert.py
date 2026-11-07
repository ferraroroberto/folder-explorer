"""
The ``revert`` verb: undo an ``apply`` -- delete its files and put each mail back in the Inbox.

Only the listed files are touched, and only those inside the email folders;
anything else is refused per file with its reason. Deleting a ``.msg`` drops
its index row in the same step, so ``plan`` stops reporting the mail as
archived before the next scan.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from src.email.archive_paths import archive_roots as _archive_roots
from src.email.archive_paths import resolve_under_roots as _resolve_under_roots
from src.email.archive_settings import get_mailbox_archive_folder, get_outlook_category
from src.email.batch.common import (
    ERROR_BAD_DECISION,
    envelope,
    renumber_folders,
    unique_folders,
)
from src.email.batch.move import ERROR_MOVE_FAILED
from src.email.mailboxes import Mailbox
from src.email.naming import normalize_message_id
from src.email.repository import EmailRepository, open_index

logger = logging.getLogger(__name__)

ERROR_NOT_IN_ARCHIVE = "not_in_archive_folder"  # the mail cannot be found back in the archive folder


def _blank_revert_result(message_id: str) -> dict[str, Any]:
    return {
        "message_id": message_id,
        "ok": False,
        "deleted": [],
        "missing": [],
        "refused": [],
        "file_errors": [],
        "moved_back": False,
        "category_removed": False,
        "entry_id": "",
        "index_rows_removed": 0,
        "error": None,
    }


def _delete_files(paths: list[Any], roots: list[Any], repo: EmailRepository, result: dict[str, Any]) -> None:
    """Delete one item's files into the result's four distinct buckets.

    ``deleted`` (gone now), ``missing`` (already gone: a revert run twice is
    not an error), ``refused`` (outside the email folders, left alone) and
    ``file_errors`` (the OS said no). A deleted ``.msg`` drops its index row
    in the same step, or ``plan`` would report the mail archived at a path
    that no longer exists until the next scan; a failed delete keeps its row,
    since the file is still there.
    """
    for raw_path in paths:
        resolved, refusal = _resolve_under_roots(str(raw_path), roots)
        if resolved is None:
            result["refused"].append({"path": str(raw_path), "reason": refusal})
            continue
        try:
            resolved.unlink()
            result["deleted"].append(str(resolved))
        except FileNotFoundError:
            result["missing"].append(str(resolved))
        except OSError as exc:
            result["file_errors"].append({"path": str(resolved), "message": f"{type(exc).__name__}: {exc}"})
            continue
        if resolved.suffix.lower() == ".msg":
            result["index_rows_removed"] += repo.delete_by_path(str(resolved))


def revert(
    client: Any,
    cfg: dict[str, Any],
    items: list[dict[str, Any]],
    *,
    renumber: bool = False,
    category: str | None = None,
    mailbox: Mailbox | None = None,
) -> dict[str, Any]:
    """Delete the listed archive files and put each mail back in the Inbox.

    ``items`` is ``[{message_id, files}]``, normally an ``apply`` result;
    ``category`` is the one to remove (the one the ``apply`` used). With
    ``renumber``, every folder a file was deleted from is renumbered once
    and the document carries the old -> new map under ``renumbered``; off,
    neither key appears.
    """
    archive_folder = get_mailbox_archive_folder(cfg, mailbox)
    category = category or get_outlook_category(cfg)
    roots = _archive_roots(cfg)
    results: list[dict[str, Any]] = []

    conn = open_index(cfg)
    repo = EmailRepository(conn)
    try:
        for raw in items:
            message_id = normalize_message_id(raw.get("message_id"))
            result = _blank_revert_result(message_id)

            _delete_files(raw.get("files") or [], roots, repo, result)
            conn.commit()

            results.append(_finish_revert_item(client, archive_folder, category, result, message_id))
        doc = envelope("revert", cfg, category, mailbox)
        if renumber:
            # The source folders: a gap only exists where a file really went.
            doc["renumbered"], doc["renumber_refused"] = renumber_folders(
                cfg, repo,
                unique_folders([
                    os.path.dirname(path)
                    for r in results for path in r["deleted"]
                ]),
            )
    finally:
        conn.close()

    reverted = sum(1 for r in results if r["ok"])
    doc["counts"] = {
        "requested": len(results),
        "reverted": reverted,
        "failed": len(results) - reverted,
    }
    doc["results"] = results
    logger.info("Revert: %d of %d mail(s) restored.", reverted, len(results))
    return doc


def _finish_revert_item(
    client: Any,
    archive_folder: str,
    category: str,
    result: dict[str, Any],
    message_id: str,
) -> dict[str, Any]:
    """The Outlook side of one ``revert`` item, after its files are handled."""
    if not message_id:
        result["error"] = {
            "code": ERROR_BAD_DECISION,
            "message": "an item needs a message_id to move the mail back",
        }
        return result

    try:
        item = client.find_by_message_id(message_id, archive_folder)
    except Exception as exc:
        item = None
        logger.warning("Archive-folder lookup for %s failed: %s", message_id, exc)

    if item is None:
        result["error"] = {
            "code": ERROR_NOT_IN_ARCHIVE,
            "message": (
                f"no mail with this Message-ID is in {archive_folder!r}; "
                "the files listed above were still processed"
            ),
        }
        return result

    try:
        client.clear_category(item, category)
        result["category_removed"] = True
        moved = client.move_to(item, None)
        result["moved_back"] = True
        result["entry_id"] = client.entry_id(moved)
    except Exception as exc:
        logger.exception("Moving %s back to the Inbox failed", message_id)
        result["error"] = {
            "code": ERROR_MOVE_FAILED,
            "message": f"{type(exc).__name__}: {exc}",
        }
        return result

    result["ok"] = not result["refused"] and not result["file_errors"]
    return result
