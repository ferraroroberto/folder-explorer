"""What the fallback Archive window shows and does, as plain data with no Tk.

It reads the same document the Archive sheet does (``archive_sheet.suggest``) and
follows ``app/webapp/static/archive.js`` rule for rule: the same constants, the same
percent and tone, the same rank, path display, saved-as name and button text. The
three ``*_SHOWN`` / ``*_NOT_SHOWN`` tables below say where every field of that
document goes, and ``tests/archive/test_archive_window_parity.py`` fails when the
document or the page grows a field this table does not place.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from src.email.archive_paths import resolve_under_roots

# Below this blended score a suggestion is a weak guess; archive.js holds the same number and a test pins them together.
WEAK_SCORE = 0.35
# The date-prefix choices, in the order the sheet lists them: the key the filer takes, the label.
DATE_CHOICES = (("auto", "Auto"), ("on", "On"), ("off", "Off"))
RANK_NOTE = ("Each folder scores by its best match: the same subject first, then the sender and the topic. "
             "A big folder doesn't win by volume.")
NO_NAME = "Numbered after the folder's last mail"
NO_CATEGORY = "No category"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Where each field of the sheet's document is shown in the window; the value names the Option or ModelView attribute.
DOC_SHOWN = {"ok": "error block", "error": "error block", "mail": "mail card", "already_archived": "ModelView.filed",
             "suggestions": "ModelView.options", "ranking": "ModelView.ranking", "category": "ModelView.category_choices",
             "roots": "Option.shown"}
RANKING_NOTE = "Finding folders…"
NO_MATCH_NOTE = "No folder matches this mail yet. Find one below."
MAIL_SHOWN = {"subject": "mail card", "sender": "mail card", "date_sent": "mail card", "attachment_count": "mail card",
              "where": "mail card", "preview": "mail card"}
MAIL_NOT_SHOWN = {"message_id": "an identity for the index, never read by a person"}
SUGGESTION_SHOWN = {"folder_path": "Option.folder_path", "why": "Option.chips", "note": "Option.note", "score": "Option.percent",
                    "similar": "Option.similar", "example": "Option.similar", "saved_as": "Option.saved_as"}
SUGGESTION_NOT_SHOWN = {"display_name": "the sheet lists the path below the email folder (Option.shown) instead"}


def win_path(path: str) -> str:
    """``path`` with Windows separators, as every displayed path is written."""
    return str(path or "").replace("/", "\\")


def last_two(path: str) -> str:
    """The last two folders of a path, as the Archive button names its target."""
    return "\\".join([p for p in (path or "").replace("/", "\\").split("\\") if p][-2:])


def under_root(path: str, roots: list[str]) -> str:
    """``path`` below the email folder that holds it, with Windows separators; the whole path when none does."""
    shown = win_path(path)
    low = shown.lower()
    for root in roots:
        base = win_path(root).rstrip("\\").lower()
        if low.startswith(base + "\\"):
            return shown[len(base) + 1:]
    return shown


def mail_date(iso: str, long: bool = True) -> str:
    """``2026-11-05T09:12:00`` as ``5 Nov 2026, 09:12`` (the day alone when ``long`` is off)."""
    if not iso:
        return ""
    try:
        month, day = int(iso[5:7]), int(iso[8:10])
    except ValueError:
        return iso
    if not (1 <= month <= 12 and day):
        return iso
    text = "%d %s %s" % (day, MONTHS[month - 1], iso[:4])
    return text + ", " + iso[11:16] if long and len(iso) >= 16 else text


def plural(n: int, one: str) -> str:
    return "{:,} {}{}".format(n, one, "" if n == 1 else "s")


def mail_line(mail: dict[str, Any]) -> str:
    """The sender, the date, the attachments and the Outlook folder, under the subject."""
    n = int(mail.get("attachment_count") or 0)
    bits = [mail.get("sender") or "(no sender)", mail_date(mail.get("date_sent") or ""),
            plural(n, "attachment") if n else "", mail.get("where") or ""]
    return " · ".join(b for b in bits if b)


EXPLORER_CHIP = "Explorer window"


def explorer_note(path: str) -> str:
    """The Explorer folder's whole path, shown with its choice so the user sees where the mail goes before filing."""
    return "open in File Explorer: " + win_path(path)


def openable_folder(path: str, roots: list[str]) -> Optional[str]:
    """``path`` as a folder File Explorer may be asked to show: inside an email folder and on this PC; else None."""
    folder, _ = resolve_under_roots(path, [Path(r) for r in roots])
    return str(folder) if folder is not None and os.path.isdir(folder) else None


@dataclass(frozen=True)
class Option:
    """One folder offered: a suggestion, or the folder the user picked."""

    folder_path: str
    shown: str
    rank: Optional[int]
    percent: Optional[int]
    weak: bool
    chips: tuple[tuple[str, str], ...]
    note: str
    similar: str
    saved_as: Optional[dict[str, str]]
    open_target: Optional[str]


@dataclass(frozen=True)
class FileRequest:
    folder: str
    category: Optional[str]
    duplicate: bool
    date_prefix: str


@dataclass(frozen=True)
class Outcome:
    """What a filing came to: ``filed`` when the files were written, ``ok`` when the tag took too."""

    filed: bool
    ok: bool
    text: str


def _similar(s: dict[str, Any]) -> str:
    bits = []
    if s.get("similar"):
        bits.append(plural(int(s["similar"]), "similar email"))
    if s.get("example"):
        bits.append("e.g. “%s”" % s["example"])
    return " · ".join(bits)


class ModelView:
    """The window's state over one document, and every answer the widgets ask of it."""

    def __init__(self, doc: dict[str, Any]) -> None:
        self.doc = doc
        self.roots = [str(r) for r in doc.get("roots") or []]
        self.filed: Optional[str] = doc.get("already_archived") or None
        self.category_choices = [doc["category"]] if doc.get("category") else []
        self.category = doc.get("category") or ""
        self.copy = False
        self.date_prefix = "auto"
        self.choice = 0
        self.extra: Optional[str] = None
        self.extra_chip, self.extra_note = "Chosen", ""
        # True while the folders are still being ranked: the window shows the mail and says so.
        self.ranking = bool(doc.get("ranking"))
        self._options = self._suggested()

    def fill(self, suggestions: list[dict[str, Any]]) -> None:
        """The ranked folders arrived: offer them, keeping a folder the user already picked first."""
        self.doc = dict(self.doc, suggestions=suggestions, ranking=False)
        self.ranking = False
        self._options = self._suggested()

    def empty_note(self) -> str:
        """What the folder list says when it offers nothing."""
        return RANKING_NOTE if self.ranking else NO_MATCH_NOTE

    def _suggested(self) -> list[Option]:
        offered: list[Option] = []
        for i, s in enumerate(self.doc.get("suggestions") or []):
            score = s.get("score")
            has_score = isinstance(score, (int, float))
            offered.append(Option(
                folder_path=s["folder_path"], shown=under_root(s["folder_path"], self.roots),
                rank=i + 1 if has_score else None,
                percent=round(score * 100) if has_score else None, weak=has_score and score < WEAK_SCORE,
                chips=tuple((c["label"], c.get("tone") or "") for c in s.get("why") or []), note=s.get("note") or "",
                similar=_similar(s), saved_as=s.get("saved_as"), open_target=openable_folder(s["folder_path"], self.roots)))
        return offered

    def pick_extra(self, path: str, chip: str = "Chosen", note: str = "picked from the folder index") -> None:
        """Lead the list with a folder the user picked; the suggestions keep their own rank after it."""
        self.extra = path
        self.extra_chip, self.extra_note = chip, note
        self.choice = 0

    def options(self) -> list[Option]:
        if not self.extra:
            return self._options
        picked = Option(folder_path=self.extra, shown=under_root(self.extra, self.roots), rank=None, percent=None, weak=False,
                        chips=((self.extra_chip, ""),), note=self.extra_note, similar="", saved_as=None,
                        open_target=openable_folder(self.extra, self.roots))
        return [picked] + self._options

    def selected(self) -> Optional[Option]:
        opts = self.options()
        return opts[self.choice] if 0 <= self.choice < len(opts) else None

    def saved_as_text(self) -> str:
        """The name the chosen folder would give the mail under the date-prefix choice."""
        choice = self.selected()
        names = choice.saved_as if choice else None
        return names[self.date_prefix] if names and names.get(self.date_prefix) else NO_NAME

    def can_archive(self) -> bool:
        return self.selected() is not None and (not self.filed or self.copy)

    def go_label(self) -> str:
        choice = self.selected()
        if choice is None:
            return "Pick a folder"
        return ("Archive a copy to " if self.filed else "Archive to ") + last_two(choice.folder_path)

    def request(self) -> Optional[FileRequest]:
        choice = self.selected()
        if choice is None or not self.can_archive():
            return None
        return FileRequest(choice.folder_path, self.category or None, bool(self.filed and self.copy), self.date_prefix)

    def outcome(self, result: dict[str, Any]) -> Outcome:
        """The words for a filing's result: filed and tagged, filed but not tagged, or not filed."""
        error = (result.get("error") or {}).get("message") or "Not archived"
        if result.get("files"):
            where = last_two(result.get("folder_path") or "")
            if result.get("ok"):
                return Outcome(True, True, "Archived to " + where + (". Indexing the folder now." if result.get("indexing") else ""))
            return Outcome(True, False, "Filed to %s, but not tagged: %s" % (where, error))
        return Outcome(False, False, error)
