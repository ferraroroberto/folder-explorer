"""What the Quick search window shows and does, as plain data with no Tk.

One query box over two tabs, Folders and Emails, each answered by the same API call the
Search tab makes (``client.py``). The rows follow ``app/webapp/static/search.js``: a
folder shows its name and its depth-trimmed path (``display``, cut by the server's
``folder_search.skip_levels``) with every query word marked, a folder of people its
count; an email its sender, date, subject and snippet with the index's own marks.

Every state the list can be in is its own ``Status``, so an unreachable app or a
failed search never reads as "no matches".
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Optional

from src.archive_window.model import mail_date, plural, win_path
from src.search_window.client import ApiError, SearchClient, Unreachable

FOLDERS, EMAILS = "folders", "emails"
TABS = ((FOLDERS, "Folders"), (EMAILS, "Emails"))
# The private-use characters the email index wraps a match in (``src.email.search.MARK_START`` / ``MARK_END``).
MARK_START, MARK_END = chr(0xE000), chr(0xE001)

HINTS = {FOLDERS: "Type part of a folder name. Every word must match.",
         EMAILS: "Type words from a mail: subject, sender or text."}
EMPTY = {FOLDERS: "No folder matches every word.", EMAILS: "No emails match. Use fewer words."}

Segments = tuple[tuple[str, bool], ...]


class Status(enum.Enum):
    HINT = "hint"
    RESULTS = "results"
    EMPTY = "empty"
    UNREACHABLE = "unreachable"
    ERROR = "error"


def words(query: str) -> list[str]:
    """The query's words, split on spaces and ``;`` as folder search splits them."""
    return [w for w in (query or "").replace(";", " ").split() if w]


def highlight(text: str, marks: list[str]) -> Segments:
    """``text`` cut into (piece, marked) runs, every case-insensitive occurrence of a word of two letters or more marked."""
    low = (text or "").lower()
    on = [False] * len(low)
    for w in (m.lower() for m in marks if len(m) > 1):
        start = low.find(w)
        while start >= 0:
            on[start:start + len(w)] = [True] * len(w)
            start = low.find(w, start + 1)
    return _runs(text or "", on)


def marked(text: str) -> Segments:
    """``text`` with the index's ``MARK_START``/``MARK_END`` pairs turned into marked runs."""
    out: list[tuple[str, bool]] = []
    inside = False
    piece = ""
    for ch in text or "":
        if ch in (MARK_START, MARK_END):
            if piece:
                out.append((piece, inside))
            piece, inside = "", ch == MARK_START
        else:
            piece += ch
    if piece:
        out.append((piece, inside))
    return tuple(out)


def plain(segments: Segments) -> str:
    return "".join(t for t, _ in segments)


def _runs(text: str, on: list[bool]) -> Segments:
    out: list[tuple[str, bool]] = []
    for ch, m in zip(text, on):
        if out and out[-1][1] == m:
            out[-1] = (out[-1][0] + ch, m)
        else:
            out.append((ch, m))
    return tuple(out)


def short_folder(path: str) -> str:
    """The last folder of a mail's folder path, as the page's chip names it."""
    parts = [p for p in win_path(path).split("\\") if p]
    return parts[-1] if parts else ""


@dataclass(frozen=True)
class Row:
    """One result line: a bold title, a detail line, a muted meta line, and what Open and Copy path act on."""

    kind: str
    title: Segments
    aside: str
    detail: Segments
    meta: str
    path: str
    target: dict[str, Any]


def folder_row(hit: dict[str, Any], marks: list[str]) -> Row:
    meta = [plural(int(hit.get("files") or 0), "file")]
    if hit.get("people"):
        meta.append("{:,} {}".format(hit["people"], "person" if hit["people"] == 1 else "people"))
    if hit.get("kind") == "synced":
        meta.append("OneDrive")
    return Row(FOLDERS, highlight(hit.get("name") or "", marks), _size(int(hit.get("size") or 0)),
               highlight(win_path(hit.get("display") or hit.get("path") or ""), marks), " · ".join(meta),
               win_path(hit.get("path") or ""), {"root": hit.get("root"), "id": hit.get("id")})


def email_row(hit: dict[str, Any]) -> Row:
    title = marked(hit.get("subject") or "") or (("(no subject)", False),)
    meta = [short_folder(hit.get("folder") or "")]
    if hit.get("attachments"):
        meta.append(plural(int(hit["attachments"]), "attachment"))
    if hit.get("flagged"):
        meta.append("Flagged")
    who = hit.get("from_name") or hit.get("from_addr") or "(no sender)"
    return Row(EMAILS, title, mail_date(hit.get("date") or "", long=False), marked(hit.get("snippet") or ""),
               " · ".join([who] + [m for m in meta if m]), win_path(hit.get("path") or ""), {"id": hit.get("id")})


_UNITS = ("KB", "MB", "GB", "TB", "PB")


def _size(n: int) -> str:
    """Bytes as the page writes them (``fmt.js`` ``fmtBytes``): B under 1 KB, then 2, 1 or 0 decimals by magnitude."""
    if not n:
        return "0 B"
    if n < 1024:
        return "%d B" % n
    value, unit = n / 1024, 0
    while value >= 1024 and unit < len(_UNITS) - 1:
        value, unit = value / 1024, unit + 1
    return ("%.0f" if value >= 100 else "%.1f" if value >= 10 else "%.2f") % value + " " + _UNITS[unit]


@dataclass(frozen=True)
class Answer:
    """What one search came to, for the tab and query it was asked for."""

    tab: str
    query: str
    status: Status
    rows: tuple[Row, ...] = ()
    total: int = 0
    message: str = ""
    notes: tuple[str, ...] = ()


def fetch(client: SearchClient, tab: str, query: str) -> Answer:
    """Ask the API for ``query`` on ``tab``. Safe on a worker thread: it touches no window state."""
    if not words(query):
        return Answer(tab, query, Status.HINT, message=HINTS[tab])
    try:
        if tab == FOLDERS:
            body = client.folders(query)
            rows = tuple(folder_row(h, words(query)) for h in body.get("results", []))
            notes: tuple[str, ...] = ()
        else:
            body = client.emails(query)
            rows = tuple(email_row(h) for h in body.get("hits", []))
            notes = tuple(body.get("notes") or ())
    except Unreachable as exc:
        return Answer(tab, query, Status.UNREACHABLE, message=str(exc))
    except ApiError as exc:
        return Answer(tab, query, Status.ERROR, message="The search couldn't run: %s" % exc)
    status = Status.RESULTS if rows else Status.EMPTY
    return Answer(tab, query, status, rows, int(body.get("total") or len(rows)), "" if rows else EMPTY[tab], notes)


@dataclass
class QuickSearchModel:
    """The window's state: the tab, the query, and the last answer for them. It outlives the window being hidden."""

    client: SearchClient
    tab: str = FOLDERS
    query: str = ""
    answer: Optional[Answer] = None
    selected: int = 0

    @property
    def status(self) -> Status:
        return self.answer.status if self.answer else Status.HINT

    @property
    def rows(self) -> tuple[Row, ...]:
        return self.answer.rows if self.answer else ()

    @property
    def message(self) -> str:
        return self.answer.message if self.answer else HINTS[self.tab]

    def is_current(self) -> bool:
        """The shown answer is for the tab and query on screen now."""
        return self.answer is not None and (self.answer.tab, self.answer.query) == (self.tab, self.query)

    def apply(self, answer: Answer) -> bool:
        """Show ``answer`` if it is for the tab and query on screen now; a late answer to an older query is dropped."""
        if (answer.tab, answer.query) != (self.tab, self.query):
            return False
        self.answer, self.selected = answer, 0
        return True

    def run(self) -> None:
        """Search the current tab for the current query and show the answer."""
        self.apply(fetch(self.client, self.tab, self.query))

    def count_line(self) -> str:
        """``12 folders`` or ``1,234 emails, first 50 shown``; empty when there is no answer to count."""
        if self.answer is None or self.status not in (Status.RESULTS, Status.EMPTY):
            return ""
        total = self.answer.total
        text = plural(total, "folder" if self.tab == FOLDERS else "email")
        return text + (", first {:,} shown".format(len(self.rows)) if len(self.rows) < total else "")

    def move(self, delta: int) -> None:
        if self.rows:
            self.selected = max(0, min(len(self.rows) - 1, self.selected + delta))

    def current(self) -> Optional[Row]:
        return self.rows[self.selected] if 0 <= self.selected < len(self.rows) else None

    def open(self, index: Optional[int] = None) -> tuple[bool, str]:
        """Open a row (the selected one by default) on this PC: (opened, what to say)."""
        if index is not None:
            self.selected = index
        row = self.current()
        if row is None:
            return False, ""
        try:
            if row.kind == FOLDERS:
                self.client.open_folder(row.target["root"], row.target["id"])
                return True, "Opened in File Explorer"
            self.client.open_email(row.target["id"])
            return True, "Opening in Outlook"
        except Unreachable as exc:
            return False, str(exc)
        except ApiError as exc:
            return False, "Couldn't open it: %s" % exc
