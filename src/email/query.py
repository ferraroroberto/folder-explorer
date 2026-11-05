"""Gmail-style email search operators, parsed into one FTS5 MATCH plus SQL filters.

    budget "draft plan" -old from:ana subject:invoice to:leo cc:mia with:ana
    after:2025-06 before:2025-12-31 newer_than:30d in:"Project Alpha"
    has:attachment filename:pdf larger:5M is:flagged is:done id:<message id>

Free words and quoted phrases search every indexed text column; a word is a
prefix match, so the box answers while it is being typed. ``OR`` between two
words takes either; ``-`` excludes a word or an operator. An operator the
index cannot answer yet (a column it does not have) is reported in ``notes``
and left out, never silently turned into "no results".

The page's ``static/query.js`` holds the same tokenizer, so a chip and the box
always agree; ``tests/test_email_search.py`` pins both to one table of cases.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional

# Every operator the box understands; anything else before a colon is a word.
OPERATORS = ("from", "to", "cc", "with", "subject", "filename", "has", "larger", "after", "before",
             "newer_than", "in", "is", "id")

# The FTS5 column order of emails_fts, fixed by every consumer's bm25 weights.
FTS_SUBJECT, FTS_SENDER, FTS_RECIPIENTS, FTS_PREVIEW = 0, 1, 2, 3

# MAPI PidTagFlagStatus values.
FLAG_FLAGGED, FLAG_DONE = 2, 1

_TOKEN_RE = re.compile(r'(-?)([a-z_]+):"([^"]*)"|(-?)([a-z_]+):(\S+)|(-?)"([^"]*)"|(\S+)', re.IGNORECASE)
_SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)([kmg]?)b?$", re.IGNORECASE)
_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?$")
_NEWER_RE = re.compile(r"^(\d+)([dmy])$", re.IGNORECASE)
_SIZE_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}


@dataclass(frozen=True)
class Token:
    """One piece of the query: an operator (``key`` + ``value``) or a word (``key`` empty)."""

    value: str
    key: str = ""
    neg: bool = False
    # A word that the user typed as "a phrase".
    phrase: bool = False
    # A word joined to the previous one by OR.
    or_prev: bool = False


def tokenize(text: str) -> list[Token]:
    """Split the box into words and operators, in the order typed."""
    out: list[Token] = []
    pending_or = False
    for m in _TOKEN_RE.finditer(text or ""):
        if m.group(2) or m.group(5):
            neg, key, value = (m.group(1), m.group(2), m.group(3)) if m.group(2) else (m.group(4), m.group(5), m.group(6))
            key = key.lower()
            if key in OPERATORS:
                out.append(Token(value, key, bool(neg)))
                pending_or = False
                continue
            out.append(Token(key + ":" + value, neg=bool(neg), or_prev=pending_or))
        elif m.group(8) is not None:
            out.append(Token(m.group(8), neg=bool(m.group(7)), phrase=True, or_prev=pending_or))
        else:
            raw = m.group(9)
            if raw.upper() == "OR":
                pending_or = bool(out) and not out[-1].key
                continue
            neg = raw.startswith("-") and len(raw) > 1
            out.append(Token(raw[1:] if neg else raw, neg=neg, or_prev=pending_or))
        pending_or = False
    return out


def words_of(tokens: list[Token]) -> list[str]:
    """The positive free words and phrases, the ones a highlight marks."""
    return [t.value for t in tokens if not t.key and not t.neg and t.value]


def fts_term(value: str, prefix: bool = True) -> str:
    """One FTS5 string, quoted so any character is literal; a prefix match unless a phrase."""
    return '"' + value.replace('"', '""') + '"' + ("*" if prefix else "")


def size_bytes(value: str) -> Optional[int]:
    m = _SIZE_RE.match(value or "")
    return int(float(m.group(1)) * _SIZE_UNITS[m.group(2).lower()]) if m else None


def date_bounds(value: str) -> Optional[tuple[date, date]]:
    """The first and last day ``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD`` covers."""
    m = _DATE_RE.match(value or "")
    if not m:
        return None
    y, mo, d = int(m.group(1)), m.group(2), m.group(3)
    try:
        if d:
            day = date(y, int(mo), int(d))
            return day, day
        if mo:
            last = calendar.monthrange(y, int(mo))[1]
            return date(y, int(mo), 1), date(y, int(mo), last)
        return date(y, 1, 1), date(y, 12, 31)
    except ValueError:
        return None


def newer_than(value: str, today: date) -> Optional[date]:
    m = _NEWER_RE.match(value or "")
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    if unit == "d":
        return today - timedelta(days=n)
    months = n if unit == "m" else n * 12
    y, mo = divmod(today.year * 12 + today.month - 1 - months, 12)
    return date(y, mo + 1, min(today.day, calendar.monthrange(y, mo + 1)[1]))


@dataclass
class Compiled:
    """A query as SQL: one FTS5 MATCH (or none) and filters over ``emails e``."""

    match: Optional[str] = None
    where: list[str] = field(default_factory=list)
    args: list[Any] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    words: list[str] = field(default_factory=list)

    def add(self, clause: str, *args: Any, neg: bool = False) -> None:
        self.where.append(("NOT " if neg else "") + "(" + clause + ")")
        self.args.extend(args)


_IN_FTS = "e.id IN (SELECT rowid FROM emails_fts WHERE emails_fts MATCH ?)"
_CONTAINS = "instr(lower(COALESCE(e.{col}, '')), ?) > 0"


def searchable(value: str) -> bool:
    """A word the FTS tokenizer keeps: one with a letter or digit in it."""
    return bool(re.search(r"\w", value))


def _word_groups(tokens: list[Token]) -> list[list[Token]]:
    """Positive words grouped by OR: ``a OR b c`` is ``[[a, b], [c]]``."""
    groups: list[list[Token]] = []
    for t in tokens:
        if t.key or t.neg or not searchable(t.value):
            continue
        if t.or_prev and groups:
            groups[-1].append(t)
        else:
            groups.append([t])
    return groups


def compile_query(text: str, columns: set[str], today: date) -> Compiled:
    """The SQL for ``text`` over an index whose ``emails`` table has ``columns``."""
    tokens = tokenize(text)
    out = Compiled(words=words_of(tokens))
    positive: list[str] = []
    for group in _word_groups(tokens):
        alts = [fts_term(t.value, prefix=not t.phrase) for t in group]
        positive.append(alts[0] if len(alts) == 1 else "(" + " OR ".join(alts) + ")")
    for t in tokens:
        if not t.key and t.neg and searchable(t.value):
            out.add(_IN_FTS, fts_term(t.value, prefix=not t.phrase), neg=True)
        elif t.key:
            _operator(t, columns, today, out, positive)
    out.match = " AND ".join(positive) if positive else None
    return out


def _fts_column(t: Token, column: str, out: Compiled, positive: list[str]) -> None:
    if not searchable(t.value):
        return
    expr = column + " : " + fts_term(t.value)
    if t.neg:
        out.add(_IN_FTS, expr, neg=True)
    else:
        positive.append(expr)


def _people(t: Token, columns: set[str], out: Compiled, positive: list[str]) -> None:
    """``to:``, ``cc:`` and ``with:``: the split address columns, or the older combined one."""
    v = t.value.lower()
    if t.key == "with":
        cols = [c for c in ("sender", "recipients", "to_addrs", "cc_addrs") if c in columns]
        out.add(" OR ".join(_CONTAINS.format(col=c) for c in cols), *([v] * len(cols)), neg=t.neg)
        return
    col = "to_addrs" if t.key == "to" else "cc_addrs"
    if col in columns:
        out.add(_CONTAINS.format(col=col), v, neg=t.neg)
    elif t.key == "to":
        _fts_column(t, "recipients", out, positive)
    else:
        out.notes.append("Cc isn't in this email index yet, so cc: was left out")


def _needs(t: Token, column: str, columns: set[str], out: Compiled) -> bool:
    if column in columns:
        return True
    out.notes.append(t.key + ": needs the attachment and size fields, which this email index doesn't have yet")
    return False


def _dated(t: Token, today: date, out: Compiled) -> None:
    if t.key == "newer_than":
        since = newer_than(t.value, today)
        if since is None:
            out.notes.append("newer_than: takes a number and d, m or y, like 30d")
            return
        out.add("substr(e.date_sent, 1, 10) >= ?", since.isoformat(), neg=t.neg)
        return
    bounds = date_bounds(t.value)
    if bounds is None:
        out.notes.append(t.key + ": takes a date like 2025, 2025-06 or 2025-06-30")
    elif t.key == "after":
        out.add("substr(e.date_sent, 1, 10) >= ?", bounds[0].isoformat(), neg=t.neg)
    else:
        out.add("e.date_sent <> '' AND substr(e.date_sent, 1, 10) <= ?", bounds[1].isoformat(), neg=t.neg)


def _operator(t: Token, columns: set[str], today: date, out: Compiled, positive: list[str]) -> None:
    v = t.value.strip()
    if not v:
        return
    if t.key == "from":
        _fts_column(t, "sender", out, positive)
    elif t.key == "subject":
        _fts_column(t, "subject", out, positive)
    elif t.key in ("to", "cc", "with"):
        _people(t, columns, out, positive)
    elif t.key in ("after", "before", "newer_than"):
        _dated(t, today, out)
    elif t.key == "in":
        out.add(_CONTAINS.format(col="folder_path"), v.lower(), neg=t.neg)
    elif t.key == "is":
        flag = {"flagged": FLAG_FLAGGED, "done": FLAG_DONE}.get(v.lower())
        if flag is None:
            out.notes.append("is: takes flagged or done")
        else:
            out.add("e.flag_status = ?", flag, neg=t.neg)
    elif t.key == "id":
        out.add("e.message_id = ?", v.strip("<>"), neg=t.neg)
    elif t.key == "has":
        if v.lower() != "attachment":
            out.notes.append("has: takes attachment")
        elif _needs(t, "has_attachments", columns, out):
            out.add("e.has_attachments = 1", neg=t.neg)
    elif t.key == "filename":
        if _needs(t, "attachment_names", columns, out):
            out.add(_CONTAINS.format(col="attachment_names"), v.lower(), neg=t.neg)
    elif t.key == "larger":
        n = size_bytes(v)
        if n is None:
            out.notes.append("larger: takes a size like 500K, 5M or 1G")
        elif _needs(t, "size", columns, out):
            out.add("e.size > ?", n, neg=t.neg)
