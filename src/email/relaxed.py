"""Email search below the ``exact`` tier: mails holding some of the words, near spellings of them, or the closest words.

The tiers and their scores are ``src.search_tiers``, which folder search
shares. Only the free words are relaxed: quoted phrases, operators and
``-`` exclusions are required in every tier (``Compiled.loose`` and
``Compiled.strict``). An ``OR`` pair counts as one word.

Everything is read from the email index: each word's mails are one FTS5
lookup, and near and closest words come from the index's own vocabulary
(``fts5vocab``), so there is no second index to keep in step and no pass
over the mails' text. The caps below keep a query interactive on a slow PC.
"""

from __future__ import annotations

import bisect
import json
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Optional

from src import search_tiers as tiers
from src.email.query import FTS_BM25, Compiled, any_of, fts_term
from src.folder_match import edits, fold, fuzzy
from src.index import MAX_NEAR

# Free words counted towards a tier; a longer query is ranked by its first ones.
MAX_WORDS = 30
# Words looked up by near spelling or closest word; each costs a pass over part of the vocabulary.
MAX_FUZZY_WORDS = 8
# Partial and similar mails listed after the exact ones, as many as folder search lists in all.
LOWER_LIMIT = 200
# Mails one closest word brings, the last indexed first: a word thousands hold tells none of them apart.
CLOSEST_MAILS = 200

_IDS = "(SELECT value FROM json_each(?))"

# A mail below the exact tier as ranked: its tier, how many words it holds exactly, and its score.
Hit = tuple[str, int, float]


class Vocabulary:
    """Every word the email index holds, as its tokenizer keeps them (lower case, no accents)."""

    def __init__(self, words: Iterable[str]) -> None:
        self.words = sorted(words)
        self.by_length: dict[int, list[str]] = {}
        for word in self.words:
            self.by_length.setdefault(len(word), []).append(word)

    def _one_edit(self, word: str) -> list[str]:
        """Words one edit from ``word``, or starting with such a spelling, the shortest first, as folder search takes them."""
        found: set[str] = set()
        for spelling in edits(word):
            at = bisect.bisect_left(self.words, spelling)
            while at < len(self.words) and self.words[at].startswith(spelling):
                found.add(self.words[at])
                at += 1
        # A word holding the query's own spelling is an exact (prefix) match or no typo of it.
        return sorted((w for w in found if word not in w), key=lambda w: (len(w), w))[:MAX_NEAR]

    def near(self, word: str) -> list[tuple[float, list[str]]]:
        """``(score, words)`` for the words one and two edits from the folded ``word``; none for a short or punctuated one."""
        if not fuzzy(word):
            return []
        one = self._one_edit(word)
        seen = set(one)
        two = [w for w in tiers.two_edit_words(word, self.by_length) if not w.startswith(word) and w not in seen]
        return [(tiers.edit_score(word, n), words) for n, words in ((1, one), (2, two)) if words]


@dataclass
class Lower:
    """The mails below the exact tier, best first, with what to mark in them."""

    ids: list[int] = field(default_factory=list)
    # Each listed mail's tier and score.
    hits: dict[int, tuple[str, float]] = field(default_factory=dict)
    # An FTS5 MATCH every listed mail satisfies, holding the words that found them.
    match: str = ""
    truncated: bool = False


def _word_expr(word: list[str]) -> str:
    return any_of([fts_term(v) for v in word])


def _exact_terms(words: Iterable[str]) -> list[str]:
    """Vocabulary words as FTS5 terms: whole words, never prefixes."""
    return [fts_term(w, prefix=False) for w in words]


def _match(q: Compiled, exprs: list[str]) -> str:
    """Any of ``exprs`` together with everything the query requires."""
    return " AND ".join(q.strict + [any_of(exprs)])


def _allowed(conn: sqlite3.Connection, q: Compiled) -> Optional[set[int]]:
    """The mails passing the operators, exclusions and phrases; ``None`` when the query has none."""
    clauses, args = list(q.where), list(q.args)
    if q.strict:
        clauses.append("e.id IN (SELECT rowid FROM emails_fts WHERE emails_fts MATCH ?)")
        args.append(" AND ".join(q.strict))
    if not clauses:
        return None
    return {r[0] for r in conn.execute("SELECT e.id FROM emails e WHERE " + " AND ".join(clauses), args)}


class _Index:
    """The lookups one query makes, each answering only mails it may list."""

    def __init__(self, conn: sqlite3.Connection, allowed: Optional[set[int]], exact: set[int]) -> None:
        self.conn = conn
        self.allowed = allowed
        self.exact = exact

    def holding(self, expr: str) -> set[int]:
        found = {r[0] for r in self.conn.execute("SELECT rowid FROM emails_fts WHERE emails_fts MATCH ?", (expr,))}
        if self.allowed is not None:
            found &= self.allowed
        return found - self.exact

    def ordered(self, found: dict[int, Hit], match: str, newest: bool) -> list[int]:
        """Tier by tier; within one, more words then the higher score then relevance, or the newest mail first."""
        ids = json.dumps(list(found))
        dates = {r[0]: r[1] or "" for r in self.conn.execute("SELECT id, date_sent FROM emails WHERE id IN " + _IDS, (ids,))}
        by_date = sorted((i for i in found if i in dates), key=lambda i: (dates[i], i), reverse=True)
        if newest:
            return sorted(by_date, key=lambda i: tiers.rank(found[i][0]))
        bm25 = dict(self.conn.execute(
            "SELECT rowid, " + FTS_BM25 + " FROM emails_fts WHERE emails_fts MATCH ?", (match,)).fetchall())
        return sorted(by_date, key=lambda i: (*tiers.sort_key(*found[i]), bm25.get(i, 0.0)))


def _near_hits(index: _Index, word: list[str], vocabulary: Vocabulary) -> tuple[dict[int, float], list[str]]:
    """The mails holding a near spelling of ``word`` with the best such score, and the near words looked up."""
    best: dict[int, float] = {}
    used: list[str] = []
    for value in word:
        for score, words in vocabulary.near(fold(value)):
            used += words
            for mail in index.holding(any_of(_exact_terms(words))):
                best[mail] = max(score, best.get(mail, 0.0))
    return best, used


def _tiered(index: _Index, words: list[list[str]], vocabulary: Vocabulary,
            room: int) -> tuple[dict[int, Hit], list[str]]:
    """The partial mails, and the similar ones while exact and partial leave ``room``; plus the near words used."""
    held = [index.holding(_word_expr(w)) for w in words]
    near: list[dict[int, float]] = [{} for _ in words]
    used: list[str] = []
    for i, word in enumerate(words[:MAX_FUZZY_WORDS]):
        near[i], spellings = _near_hits(index, word, vocabulary)
        used += spellings
    n = len(words)
    partial: dict[int, Hit] = {}
    similar: dict[int, Hit] = {}
    for mail in set().union(*held, *near):
        matched = sum(1 for h in held if mail in h)
        nearly = [near[i][mail] for i in range(n) if mail not in held[i] and mail in near[i]]
        tier = tiers.tier_of(matched, len(nearly), n)
        if tier is None:
            continue
        if tier == tiers.EXACT:
            # Every counted word, yet not an exact hit: the query holds more words than MAX_WORDS.
            tier = tiers.PARTIAL
        hit = (tier, matched, round((matched + sum(nearly)) / n, 3))
        (partial if tier == tiers.PARTIAL else similar)[mail] = hit
    if len(index.exact) + len(partial) < room:
        partial.update(similar)
    return partial, used


def _closest(index: _Index, words: list[list[str]], vocabulary: Vocabulary) -> tuple[dict[int, Hit], list[str]]:
    """The mails whose words look most like the query's, scored by that likeness; plus the words they were found by."""
    per_word: list[dict[int, float]] = []
    used: list[str] = []
    for word in words[:MAX_FUZZY_WORDS]:
        best: dict[int, float] = {}
        for value in word:
            for near, likeness in tiers.closest_words(fold(value), vocabulary.by_length):
                mails = sorted(index.holding(fts_term(near, prefix=False)), reverse=True)[:CLOSEST_MAILS]
                if mails:
                    used.append(near)
                for mail in mails:
                    best[mail] = max(likeness, best.get(mail, 0.0))
        per_word.append(best)
    found = {mail: (tiers.CLOSEST, 0, round(sum(b.get(mail, 0.0) for b in per_word) / len(words), 3))
             for mail in set().union(*per_word)}
    return found, used


def lower_tiers(conn: sqlite3.Connection, q: Compiled, exact: list[int], room: int, newest: bool,
                vocabulary: Callable[[], Vocabulary]) -> Lower:
    """What to list after the ``exact`` mails of a query that has fewer than ``room`` of them.

    ``partial``, then ``similar`` while room is left; ``closest`` only when
    nothing at all was found. A query with no free word has nothing to relax.
    """
    words = q.loose[:MAX_WORDS]
    if not words:
        return Lower()
    allowed = _allowed(conn, q)
    if allowed is not None and not allowed:
        return Lower()
    index = _Index(conn, allowed, set(exact))
    vocab = vocabulary()
    found, used = _tiered(index, words, vocab, room)
    closest = not found and not exact
    if closest:
        found, used = _closest(index, words, vocab)
    if not found:
        return Lower()
    typed = [] if closest else [_word_expr(w) for w in words]
    match = _match(q, typed + _exact_terms(dict.fromkeys(used)))
    limit = tiers.CLOSEST_LIMIT if closest else LOWER_LIMIT
    ids = index.ordered(found, match, newest)
    shown = ids[:limit]
    return Lower(shown, {i: (found[i][0], found[i][2]) for i in shown}, match, not closest and len(ids) > limit)


def markings(q: Compiled, vocabulary: Callable[[], Vocabulary]) -> Iterator[tuple[str, list[str]]]:
    """``(match, extra words)`` to try, in order, to mark a mail that does not hold every word.

    First the typed words and their near spellings, then the closest words,
    the same words the lists are found by.
    """
    words = q.loose[:MAX_FUZZY_WORDS]
    if not words:
        return
    vocab = vocabulary()
    near = list(dict.fromkeys(w for word in words for v in word for _, found in vocab.near(fold(v)) for w in found))
    yield _match(q, [_word_expr(w) for w in q.loose[:MAX_WORDS]] + _exact_terms(near)), near
    close = list(dict.fromkeys(w for word in words for v in word for w, _ in tiers.closest_words(fold(v), vocab.by_length)))
    if close:
        yield _match(q, _exact_terms(close)), close
