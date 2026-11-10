"""The Archive window's "Find a folder": search every folder as Quick search does, step into one, say which can take the mail.

Plain data, no Tk. The search is Quick search's own (``search_window.model.fetch`` and
``folder_row``) over a source in ``GET /api/search/folders``'s shape, so the same words
find the same folders in both windows. A folder outside the email folders is listed too,
marked: it can be browsed and shown in File Explorer, never filed in (the filer refuses it).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from src.email.settings import under_any
from src.search_window.model import FOLDERS, HINTS, Answer, FolderSource, Row, Status, fetch, folder_row, plain

logger = logging.getLogger(__name__)

# How many folders the window lists at once; more words narrow the list.
ROWS_SHOWN = 8
OUTSIDE = "Outside the email folders"
NO_SUBFOLDERS = "No subfolders."


class FinderSource(FolderSource, Protocol):
    """The folder search, plus the direct subfolders of a scanned folder in the search hits' shape."""

    def children(self, path: str) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class Found:
    """One folder listed: Quick search's row, and whether the mail may be filed in it."""

    row: Row
    fileable: bool

    @property
    def name(self) -> str:
        return plain(self.row.title)


@dataclass(frozen=True)
class Level:
    """A folder stepped into: its path and name, and its subfolders."""

    path: str
    name: str
    found: tuple[Found, ...]


class Finder:
    """The search box's state: the last search, the folders stepped into from it, and the selected row."""

    def __init__(self, source: FinderSource, roots: list[str]) -> None:
        self.source, self.roots = source, roots
        self.query = ""
        self.answer: Optional[Answer] = None
        self.trail: list[Level] = []
        self.selected = 0
        # A failure of the last step in, said in place of the list until the next search or step.
        self.problem = ""

    def _found(self, rows: tuple[Row, ...]) -> tuple[Found, ...]:
        """The rows with the folders a mail can go in first, each group in the search's own order."""
        marked = [Found(r, under_any(r.path, self.roots)) for r in rows]
        return tuple(sorted(marked, key=lambda f: not f.fileable))

    def search(self, query: str) -> None:
        """Search every folder for ``query``; a new search leaves any folder stepped into."""
        self.query, self.trail, self.selected, self.problem = query, [], 0, ""
        try:
            self.answer = fetch(self.source, FOLDERS, query)
        except Exception as exc:  # noqa: BLE001 - a failed search is said in the window, never fatal to it
            logger.exception("❌ archive window: the folder search failed")
            self.answer = Answer(FOLDERS, query, Status.ERROR, message="The search couldn't run: %s" % exc)

    @property
    def browsing(self) -> bool:
        return bool(self.trail)

    def found(self) -> tuple[Found, ...]:
        if self.trail:
            return self.trail[-1].found
        return self._found(self.answer.rows) if self.answer else ()

    def shown(self) -> tuple[Found, ...]:
        return self.found()[:ROWS_SHOWN]

    def message(self) -> str:
        """What the list says in place of rows: a hint, no match, no subfolders, or a failure in its own words."""
        if self.problem:
            return self.problem
        if self.trail:
            return "" if self.found() else NO_SUBFOLDERS
        if self.answer is None:
            return HINTS[FOLDERS]
        return "" if self.answer.rows else self.answer.message

    def failed(self) -> bool:
        return bool(self.problem) or (not self.trail and self.answer is not None
                                      and self.answer.status in (Status.ERROR, Status.UNREACHABLE))

    def count_line(self) -> str:
        """``12 folders, first 8 shown``; empty when there is nothing to count."""
        total = len(self.found())
        if not total:
            return ""
        text = "{:,} folder{}".format(total, "" if total == 1 else "s")
        return text + (", first {:,} shown".format(ROWS_SHOWN) if total > ROWS_SHOWN else "")

    def crumbs(self) -> list[str]:
        return [level.name for level in self.trail]

    def move(self, delta: int) -> None:
        shown = self.shown()
        if shown:
            self.selected = max(0, min(len(shown) - 1, self.selected + delta))

    def current(self, index: Optional[int] = None) -> Optional[Found]:
        if index is not None:
            self.selected = index
        shown = self.shown()
        return shown[self.selected] if 0 <= self.selected < len(shown) else None

    def enter(self, index: Optional[int] = None) -> bool:
        """Step into a row's subfolders (the selected one by default); False when there is no row."""
        here = self.current(index)
        if here is None:
            return False
        try:
            kids = self.source.children(here.row.path)
        except Exception as exc:  # noqa: BLE001 - a failed step is said in the window, never fatal to it
            logger.exception("❌ archive window: listing the subfolders failed")
            self.problem = "Couldn't list the subfolders: %s" % exc
            return False
        self.problem = ""
        self.trail.append(Level(here.row.path, here.name, self._found(tuple(folder_row(k, []) for k in kids))))
        self.selected = 0
        return True

    def back(self) -> bool:
        """Step back up one level, to the search's own list at the top; False when already there."""
        if not self.trail:
            return False
        self.trail.pop()
        self.selected, self.problem = 0, ""
        return True
