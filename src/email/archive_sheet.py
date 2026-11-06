"""The Archive sheet's two steps over one live mail: the folders to suggest, and filing it where the user chose.

Pure orchestration over the Outlook client's surface (``read_mail``,
``set_category``), the suggester, the archiver and the index, so a fake client
drives both steps in a test. The sheet replaces the old archive dialog, so
filing writes the ``.msg`` and its attachments into an email folder and tags
the mail; it never moves it, which stays the batch ``apply`` verb's job.
Every failure is a ``SheetError`` with its own code and words, never a bare
exception the page cannot tell apart.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

from src.email.archive import EmailArchiver, planned_name
from src.email.archive_paths import archive_roots, is_within, resolve_under_roots
from src.email.archive_settings import get_outlook_category
from src.email.repository import EmailRepository, open_index
from src.email.suggester import EmailData, RankedSuggestion, SuggestionEngine

logger = logging.getLogger(__name__)

ERR_NO_COM = "pywin32_missing"
ERR_OUTLOOK = "outlook_unreachable"
ERR_NO_MAIL = "no_mail"
ERR_GONE = "mail_gone"
ERR_NO_ROOT = "no_archive_root"
ERR_OUTSIDE = "outside_email_folders"
ERR_ALREADY = "already_archived"
ERR_ARCHIVE = "archive_failed"
ERR_CATEGORY = "category_failed"
ERR_NOT_IN_MAILBOX = "not_in_mailbox"
ERR_TIMEOUT = "timeout"
ERR_FAILED = "failed"

# What the sheet, the fallback window and the toast say for each failure.
MESSAGES = {
    ERR_NO_COM: "Not set up here: pywin32 isn't installed (requirements-outlook.txt).",
    ERR_OUTLOOK: "Outlook isn't reachable: open Outlook (classic) and try again.",
    ERR_NO_MAIL: "No mail is open or selected in Outlook.",
    ERR_GONE: "That mail is no longer in Outlook: it was moved or deleted after the click.",
    ERR_NO_ROOT: "No archive root is mounted: none of the email folders is on this PC right now.",
    ERR_OUTSIDE: "That folder is outside the email folders, so nothing is filed there.",
    ERR_ALREADY: "This mail is already archived.",
    ERR_ARCHIVE: "The mail couldn't be saved to the folder.",
    ERR_CATEGORY: "The mail was filed, but Outlook wouldn't take the category.",
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
    """The chips and the one line that say why a folder is suggested, strongest reason first."""
    from_sender = repo.count_from_sender(s.folder_path, sender)
    chips = []
    if s.exact_subject:
        chips.append({"label": "Same subject", "tone": "accent"})
    if from_sender:
        chips.append({"label": "Sender", "tone": ""})
    if not chips:
        chips.append({"label": "Topic", "tone": ""})
    if s.exact_subject:
        note = "This conversation is filed here"
    elif from_sender:
        note = _plural(from_sender, "email") + " from " + sender
    elif s.match_count:
        note = _plural(s.match_count, "similar email")
    else:
        note = s.sample_subjects[0] if s.sample_subjects else ""
    return chips, note


def suggest(client: Any, cfg: dict[str, Any], item: Any, where: str = "") -> dict[str, Any]:
    """The sheet's opening document for one live mail: the mail, where it is already filed, and the folders to offer."""
    roots = mounted_roots(cfg)
    mail = client.read_mail(item)
    ranked = SuggestionEngine(cfg).suggest(EmailData(subject=mail.subject, sender=mail.sender,
                                                     recipients=mail.recipients, date_sent=mail.date_sent))
    conn = open_index(cfg)
    try:
        repo = EmailRepository(conn)
        filed = _filed_as(repo, mail.message_id)
        suggestions = []
        for s in ranked:
            if not any(is_within(Path(s.folder_path), r) for r in roots):
                continue
            chips, note = _why(repo, s, mail.sender)
            suggestions.append({"folder_path": s.folder_path, "display_name": s.display_name, "why": chips,
                                "note": note, "saved_as": planned_name(cfg, s.folder_path, mail.subject, mail.date_sent)})
    finally:
        conn.close()
    logger.info("ℹ️ archive sheet: %d folder(s) suggested%s", len(suggestions), ", already filed" if filed else "")
    return {
        "ok": True,
        "mail": {"message_id": mail.message_id, "subject": mail.subject, "sender": mail.sender,
                 "date_sent": mail.date_sent.isoformat() if mail.date_sent else "",
                 "attachment_count": mail.attachment_count, "where": where},
        "already_archived": filed,
        "suggestions": suggestions,
        "category": get_outlook_category(cfg),
        "roots": [str(r) for r in roots],
    }


def file_mail(client: Any, cfg: dict[str, Any], item: Any, folder_path: str, category: Optional[str],
              duplicate: bool = False) -> dict[str, Any]:
    """Save the mail and its attachments into ``folder_path`` and tag it with ``category`` (none when blank).

    A mail the index already holds is refused unless ``duplicate`` says the user asked for another copy;
    the copy still takes the folder's next sequence number, so no file is ever overwritten.
    """
    roots = mounted_roots(cfg)
    folder, refusal = resolve_under_roots(folder_path, roots)
    if folder is None:
        logger.warning("⚠️ archive sheet: refused a folder that is %s", refusal)
        raise SheetError(ERR_OUTSIDE)
    mail = client.read_mail(item)
    conn = open_index(cfg)
    try:
        filed = _filed_as(EmailRepository(conn), mail.message_id)
    finally:
        conn.close()
    if filed and not duplicate:
        raise SheetError(ERR_ALREADY, "This mail is already archived as " + os.path.basename(filed) + ".")
    try:
        archived = EmailArchiver(cfg).archive(item, str(folder), mail.subject)
    except Exception as exc:
        logger.exception("❌ archive sheet: saving the mail failed")
        raise SheetError(ERR_ARCHIVE, MESSAGES[ERR_ARCHIVE] + " " + type(exc).__name__ + ": " + str(exc)) from exc
    doc: dict[str, Any] = {"ok": True, "folder_path": str(folder), "files": [archived.email_path, *archived.attachment_paths],
                           "category": category or "", "categorized": False, "error": None}
    if category:
        try:
            client.set_category(item, category)
            doc["categorized"] = True
        except Exception as exc:
            logger.exception("⚠️ archive sheet: tagging the mail failed")
            doc["ok"] = False
            doc["error"] = {"code": ERR_CATEGORY, "message": MESSAGES[ERR_CATEGORY] + " " + type(exc).__name__}
    logger.info("✅ archive sheet: filed %s (%d attachment(s))%s", os.path.basename(archived.email_path),
                len(archived.attachment_paths), ", a copy of " + os.path.basename(filed) if filed else "")
    return doc
