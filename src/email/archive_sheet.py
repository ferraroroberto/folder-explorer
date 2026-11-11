"""The Archive sheet's two steps over one live mail: the folders to suggest, and filing it where the user chose.

Pure orchestration over the Outlook client's surface (``read_mail``,
``set_category``), the suggester, the archiver and the index, so a fake client
drives both steps in a test. The sheet replaces the old archive dialog, so
filing writes the ``.msg`` and its attachments into an email folder
(``save_mail``, all the user waits on), then tags the mail and queues its
folder for indexing (``follow_up``, after the window closes); it never moves
it, which stays the batch ``apply`` verb's job.
Every failure is a ``SheetError`` with its own code and words, never a bare
exception the page cannot tell apart.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from src.email.archive import DATE_PREFIX_CHOICES, EmailArchiver, planned_names
from src.email.archive_paths import archive_roots, is_within, resolve_under_roots
from src.email.archive_settings import get_outlook_add_category, get_outlook_category
from src.email.repository import EmailRepository, open_index, record_filed_mail
from src.email.suggester import EmailData, RankedSuggestion, SuggestionEngine
from src.explorer_window import ExplorerUnavailableError, get_current_explorer_folder
from src.stage_timer import StageTimer

logger = logging.getLogger(__name__)

ERR_NO_COM = "pywin32_missing"
ERR_OUTLOOK = "outlook_unreachable"
ERR_NO_MAIL = "no_mail"
ERR_GONE = "mail_gone"
ERR_NO_ROOT = "no_archive_root"
ERR_OUTSIDE = "outside_email_folders"
ERR_EXPLORER_UNAVAILABLE = "explorer_unavailable"
ERR_NO_EXPLORER = "no_explorer_folder"
ERR_ALREADY = "already_archived"
ERR_ARCHIVE = "archive_failed"
ERR_CATEGORY = "category_failed"
ERR_INDEX = "index_failed"
ERR_NOT_IN_MAILBOX = "not_in_mailbox"
ERR_TIMEOUT = "timeout"
ERR_FAILED = "failed"

# The live mail's readable text the sheet's preview shows: the index's own preview length.
PREVIEW_CHARS = 500

# What the sheet, the fallback window and the toast say for each failure.
MESSAGES = {
    ERR_NO_COM: "Not set up here: pywin32 isn't installed (requirements-outlook.txt).",
    ERR_OUTLOOK: "Outlook isn't reachable: open Outlook (classic) and try again.",
    ERR_NO_MAIL: "No mail is open or selected in Outlook.",
    ERR_GONE: "That mail is no longer in Outlook: it was moved or deleted after the click.",
    ERR_NO_ROOT: "No archive root is mounted: none of the email folders is on this PC right now.",
    ERR_OUTSIDE: "That folder is outside the email folders, so nothing is filed there.",
    ERR_EXPLORER_UNAVAILABLE: "Cannot read Explorer windows: Windows didn't say which folders are open. Pick a folder from the list instead.",
    ERR_NO_EXPLORER: "No Explorer folder is open: open the destination folder in File Explorer and try again, or pick one from the list.",
    ERR_ALREADY: "This mail is already archived.",
    ERR_ARCHIVE: "The mail couldn't be saved to the folder.",
    ERR_CATEGORY: "The mail was filed, but Outlook wouldn't take the category.",
    ERR_INDEX: "The mail was filed, but its folder wasn't queued for indexing: search finds it after the next scan.",
    ERR_NOT_IN_MAILBOX: "The live copy isn't in the Inbox, the archive folder or Sent Items any more.",
    ERR_TIMEOUT: "Outlook didn't answer in time. A dialog may be waiting in Outlook.",
    ERR_FAILED: "Something went wrong talking to Outlook.",
}


class SheetError(Exception):
    """A step that could not run: ``code`` names the case, the message says it in words."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or MESSAGES.get(code, MESSAGES[ERR_FAILED]))
        self.code = code


def error_document(code: str, detail: str = "") -> dict[str, Any]:
    """The one document a failed step prints: ``{"ok": false, "error": {"code", "message"}}``."""
    return {"ok": False, "error": {"code": code, "message": detail or MESSAGES.get(code, MESSAGES[ERR_FAILED])}}


def mounted_roots(cfg: dict[str, Any]) -> list[Path]:
    """The email folders that are on this PC right now; raises ``no_archive_root`` when none is."""
    mounted = [r for r in archive_roots(cfg) if r.is_dir()]
    if not mounted:
        raise SheetError(ERR_NO_ROOT)
    return mounted


def _filed_as(repo: EmailRepository, message_id: str) -> Optional[str]:
    """The ``.msg`` this mail is already filed as, when the index says so and the file is still there."""
    path = repo.find_path_by_message_id(message_id) if message_id else None
    return path if path and os.path.isfile(path) else None


def _plural(n: int, word: str) -> str:
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def _why(repo: EmailRepository, s: RankedSuggestion, sender: str) -> tuple[list[dict[str, str]], str]:
    """The chips and the one line that say why a folder is suggested, strongest reason first.

    A folder suggested on topic alone gets no line: its similar count and example say it.
    """
    from_sender = repo.count_from_sender(s.folder_path, sender)
    chips = []
    if s.same_email:
        chips.append({"label": "Same email", "tone": "accent"})
    if s.exact_subject:
        chips.append({"label": "Same subject", "tone": "accent"})
    if from_sender:
        chips.append({"label": "Sender", "tone": ""})
    if not chips:
        chips.append({"label": "Topic", "tone": ""})
    if s.same_email:
        note = "This email is already filed here"
    elif s.exact_subject:
        note = "This conversation is filed here"
    elif from_sender:
        note = _plural(from_sender, "email") + " from " + sender
    else:
        note = ""
    return chips, note


def explorer_folder(cfg: dict[str, Any], current: Optional[Callable[[], Optional[str]]] = None) -> dict[str, Any]:
    """The folder open in the foremost File Explorer window, as a destination the sheet and the fallback window offer.

    Each way it can fail has its own code: the shell can't be asked, no Explorer folder is open, or the open
    folder is outside the email folders (filing refuses those, so the user hears it before choosing).
    """
    roots = mounted_roots(cfg)
    try:
        opened = (current or get_current_explorer_folder)()
    except ExplorerUnavailableError as exc:
        logger.warning("⚠️ archive: Explorer windows can't be read: %s", exc)
        raise SheetError(ERR_EXPLORER_UNAVAILABLE) from exc
    if not opened:
        raise SheetError(ERR_NO_EXPLORER)
    folder, refusal = resolve_under_roots(opened, roots)
    if folder is None:
        logger.warning("⚠️ archive: the Explorer folder is %s", refusal)
        raise SheetError(ERR_OUTSIDE)
    return {"ok": True, "folder_path": str(folder)}


def opening(client: Any, cfg: dict[str, Any], item: Any, where: str = "",
            timer: Optional[StageTimer] = None) -> tuple[dict[str, Any], EmailData]:
    """The sheet's document before ranking (``ranking`` true, no suggestions yet) and what ranking reads.

    It needs one COM read and one index lookup, so the sheet or the window can show the mail at
    once and fill the folders in when ``rank`` answers.
    """
    roots = mounted_roots(cfg)
    mail = client.read_mail(item, PREVIEW_CHARS)
    if timer:
        timer.mark("read_mail")
    conn = open_index(cfg)
    try:
        filed = _filed_as(EmailRepository(conn), mail.message_id)
    finally:
        conn.close()
    if timer:
        timer.mark("filed_lookup")
    data = EmailData(subject=mail.subject, sender=mail.sender, recipients=mail.recipients,
                     date_sent=mail.date_sent, message_id=mail.message_id)
    add_category = get_outlook_add_category(cfg)
    doc = {
        "ok": True,
        "mail": {"message_id": mail.message_id, "subject": mail.subject, "sender": mail.sender,
                 "date_sent": mail.date_sent.isoformat() if mail.date_sent else "",
                 "attachment_count": mail.attachment_count, "where": where, "preview": mail.body_preview},
        "already_archived": filed,
        "suggestions": [],
        "ranking": True,
        "add_category": add_category,
        "category": get_outlook_category(cfg) if add_category else "",
        "roots": [str(r) for r in roots],
    }
    return doc, data


def rank(cfg: dict[str, Any], data: EmailData, roots: list[str]) -> list[dict[str, Any]]:
    """The folders to offer for one mail, best first, each with why it is offered and the name it would get.

    Logs one timing line (milliseconds and counts only) for the ranking and the folder details.
    """
    timer = StageTimer("archive ranking")
    root_paths = [Path(r) for r in roots]
    conn = open_index(cfg)
    try:
        repo = EmailRepository(conn)
        found = SuggestionEngine(cfg).suggest(data, repo, timer)
        suggestions = []
        for s in found:
            if not any(is_within(Path(s.folder_path), r) for r in root_paths):
                continue
            chips, note = _why(repo, s, data.sender)
            suggestions.append({"folder_path": s.folder_path, "display_name": s.display_name.replace("/", "\\"),
                                "why": chips, "note": note, "score": round(s.score, 3), "similar": s.match_count,
                                "example": s.sample_subjects[0] if s.sample_subjects else "",
                                "saved_as": planned_names(cfg, s.folder_path, data.subject, data.date_sent)})
    finally:
        conn.close()
    timer.mark("details")
    timer.count("suggested", len(suggestions))
    timer.log(logger)
    return suggestions


def ranked(cfg: dict[str, Any], doc: dict[str, Any], data: EmailData) -> dict[str, Any]:
    """``doc`` from ``opening`` with its folders filled in."""
    suggestions = rank(cfg, data, doc["roots"])
    logger.info("ℹ️ archive sheet: %d folder(s) suggested%s", len(suggestions), ", already filed" if doc["already_archived"] else "")
    return dict(doc, suggestions=suggestions, ranking=False)


def suggest(client: Any, cfg: dict[str, Any], item: Any, where: str = "") -> dict[str, Any]:
    """The sheet's whole document for one live mail: the mail, where it is already filed, and the folders to offer."""
    doc, data = opening(client, cfg, item, where)
    return ranked(cfg, doc, data)


def rank_input(data: EmailData) -> dict[str, str]:
    """What ``rank`` reads, as JSON the bridge hands the server with an opening document."""
    return {"subject": data.subject, "sender": data.sender, "recipients": data.recipients,
            "date_sent": data.date_sent.isoformat() if data.date_sent else "", "message_id": data.message_id}


def rank_data(raw: Any) -> Optional[EmailData]:
    """``rank_input``'s JSON back as ``EmailData``; None when it isn't that shape."""
    if not isinstance(raw, dict):
        return None
    try:
        sent = datetime.fromisoformat(raw["date_sent"]) if raw.get("date_sent") else None
        return EmailData(subject=str(raw.get("subject") or ""), sender=str(raw.get("sender") or ""),
                         recipients=str(raw.get("recipients") or ""), date_sent=sent,
                         message_id=str(raw.get("message_id") or ""))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Saved:
    """A mail confirmed on disk, and what the steps after the save need of it."""

    item: Any
    folder_path: str
    email_path: str
    category: Optional[str]


def save_mail(client: Any, cfg: dict[str, Any], item: Any, folder_path: str, category: Optional[str],
              duplicate: bool = False, date_prefix: str = "auto",
              timer: Optional[StageTimer] = None) -> tuple[dict[str, Any], Saved]:
    """Save the mail and its attachments into ``folder_path``: everything the user waits on, and nothing else.

    A mail the index already holds is refused unless ``duplicate`` says the user asked for another copy;
    the copy still takes the folder's next sequence number, so no file is ever overwritten.
    ``date_prefix`` is one of ``DATE_PREFIX_CHOICES``: ``auto`` keeps the configured mode for the folder.
    The ``.msg`` is confirmed on disk before this returns. Tagging with ``category`` and indexing the
    folder are ``follow_up``'s, run once the window has closed or the page has its answer.
    """
    if date_prefix not in DATE_PREFIX_CHOICES:
        raise ValueError("unknown date prefix choice: " + str(date_prefix))
    if category and not get_outlook_add_category(cfg):
        # The server owns the switch, so an older window or page that still sends one stamps nothing.
        logger.info("ℹ️ archive sheet: outlook.add_category is off, so the requested category is not stamped")
        category = None
    roots = mounted_roots(cfg)
    folder, refusal = resolve_under_roots(folder_path, roots)
    if folder is None:
        logger.warning("⚠️ archive sheet: refused a folder that is %s", refusal)
        raise SheetError(ERR_OUTSIDE)
    mail = client.read_mail(item)
    if timer:
        timer.mark("read_mail")
    conn = open_index(cfg)
    try:
        filed = _filed_as(EmailRepository(conn), mail.message_id)
    finally:
        conn.close()
    if filed and not duplicate:
        raise SheetError(ERR_ALREADY, "This mail is already archived as " + os.path.basename(filed) + ".")
    if timer:
        timer.mark("duplicate_check")
    try:
        archived = EmailArchiver(cfg, date_prefix=DATE_PREFIX_CHOICES[date_prefix]).archive(item, str(folder), mail.subject,
                                                                                             timer)
    except Exception as exc:
        logger.exception("❌ archive sheet: saving the mail failed")
        raise SheetError(ERR_ARCHIVE, MESSAGES[ERR_ARCHIVE] + " " + type(exc).__name__ + ": " + str(exc)) from exc
    # The window closes on this answer, so the file has to be there, not just reported saved by Outlook.
    if not os.path.isfile(archived.email_path) or os.path.getsize(archived.email_path) == 0:
        logger.error("❌ archive sheet: Outlook reported the save, but %s is missing or empty",
                     os.path.basename(archived.email_path))
        raise SheetError(ERR_ARCHIVE, MESSAGES[ERR_ARCHIVE] + " Outlook reported it saved, but the file isn't there.")
    conn = open_index(cfg)
    try:
        record_filed_mail(EmailRepository(conn), mail, archived.email_path)
    finally:
        conn.close()
    if timer:
        timer.mark("record")
    logger.info("✅ archive sheet: saved %s (%d attachment(s))%s", os.path.basename(archived.email_path),
                len(archived.attachment_paths), ", a copy of " + os.path.basename(filed) if filed else "")
    doc: dict[str, Any] = {"ok": True, "folder_path": str(folder), "files": [archived.email_path, *archived.attachment_paths],
                           "category": category or "", "error": None}
    return doc, Saved(item, str(folder), archived.email_path, category)


def _stamp_category(client: Any, saved: Saved) -> bool:
    if not saved.category:
        return False
    client.set_category(saved.item, saved.category)
    return True


def _queue_index(client: Any, saved: Saved) -> bool:
    from src.email import indexer  # noqa: PLC0415 - only a filing starts the indexer; the macro's entry starts faster without it

    indexer.spawn("archive", saved.folder_path)
    return True


@dataclass(frozen=True)
class FollowUpStep:
    """One step after the save: ``run`` answers whether it did anything, and raises when it failed."""

    name: str
    key: str
    code: str
    run: Callable[[Any, Saved], bool]


# What runs after the save, in order, off the user's wait. Another step (moving the mail in Outlook) joins this tuple.
FOLLOW_UP_STEPS = (
    FollowUpStep("category", "categorized", ERR_CATEGORY, _stamp_category),
    FollowUpStep("index", "indexing", ERR_INDEX, _queue_index),
)


def follow_up(client: Any, saved: Saved, steps: tuple[FollowUpStep, ...] = FOLLOW_UP_STEPS,
              timer: Optional[StageTimer] = None) -> dict[str, Any]:
    """Run every step after the save; one failing never stops the next, and each failure is reported.

    Answers ``{"ok", <each step's key>: bool, "error": None | {"code", "message"}}``; the error carries the
    first failure's code and every failure's words, so the user hears all of them in one notice.
    """
    doc: dict[str, Any] = {"ok": True, "error": None}
    failed: list[tuple[str, str]] = []
    for step in steps:
        try:
            doc[step.key] = step.run(client, saved)
        except Exception as exc:
            logger.exception("⚠️ archive sheet: the %s step after the save failed", step.name)
            doc[step.key] = False
            failed.append((step.code, MESSAGES[step.code] + " " + type(exc).__name__))
        if timer:
            timer.mark(step.name)
    if failed:
        doc["ok"] = False
        doc["error"] = {"code": failed[0][0], "message": " ".join(message for _, message in failed)}
    return doc
