"""The four result tiers folder and email search rank by, so a wrong or misspelled word still finds something.

A query's words are matched one by one, and each hit lands in one tier;
tiers never interleave, best first:

1. ``exact``: every word matches.
2. ``partial``: at least half the words match; more matched words rank first.
3. ``similar``: at least half the words match, counting a near word (one
   edit off, two for a word of ``TWO_EDIT_MIN`` letters or more).
4. ``closest``: asked only when the tiers above found nothing; the
   ``CLOSEST_LIMIT`` hits whose words look most like the query's.

A hit's score, 0 to 1, is the mean of its words' scores: 1 for an exact
word, ``1 - edits / letters`` for a near one, 0 for a missing one. Each
caller reads its own index; this module holds the rules and the word
similarity, and reads nothing.
"""

from __future__ import annotations

from typing import Optional

from rapidfuzz import fuzz, process
from rapidfuzz.distance import OSA

EXACT, PARTIAL, SIMILAR, CLOSEST = "exact", "partial", "similar", "closest"
TIERS = (EXACT, PARTIAL, SIMILAR, CLOSEST)
# Hits the closest tier lists: enough to recognise the one meant, few enough to read.
CLOSEST_LIMIT = 20
# Words this long may be two edits off; shorter ones one, so "notes" never finds "nodes" twice removed.
TWO_EDIT_MIN = 8
# Two-edit words kept per query word, the nearest first; each widens what a near match reads.
MAX_TWO_EDIT = 40
# Vocabulary words a closest lookup keeps per query word.
CLOSEST_WORDS = 20
# How much longer or shorter than the query word a vocabulary word may be and still count as closest.
CLOSEST_LENGTH_SLACK = 3


def needed(words: int) -> int:
    """Words a hit must hold for ``partial`` or ``similar``: at least half of them."""
    return (words + 1) // 2


def tier_of(matched: int, near: int, words: int) -> Optional[str]:
    """The tier of a hit holding ``matched`` words exactly and ``near`` more through a near word; ``None`` below them all."""
    if matched >= words:
        return EXACT
    if matched >= needed(words):
        return PARTIAL
    if matched + near >= needed(words):
        return SIMILAR
    return None


def rank(tier: str) -> int:
    """The tier's place, 0 for ``exact``: lower sorts first."""
    return TIERS.index(tier)


def edit_score(word: str, edits: int) -> float:
    """What a near word scores for ``word``: its edits in its length."""
    return round(max(0.0, 1 - edits / len(word)), 3)


def _lengths(by_length: dict[int, list[str]], word: str, slack: int) -> list[str]:
    return [w for n in range(max(1, len(word) - slack), len(word) + slack + 1) for w in by_length.get(n, ())]


def two_edit_words(word: str, by_length: dict[int, list[str]]) -> list[str]:
    """Vocabulary words exactly two edits from ``word`` (a swap of two letters is one), nearest length first; only for a word of ``TWO_EDIT_MIN`` letters or more."""
    if len(word) < TWO_EDIT_MIN:
        return []
    found = process.extract(word, _lengths(by_length, word, 2), scorer=OSA.distance,
                            score_cutoff=2, limit=None)
    near = sorted((w for w, d, _ in found if d == 2), key=lambda w: (abs(len(w) - len(word)), w))
    return near[:MAX_TWO_EDIT]


def closest_words(word: str, by_length: dict[int, list[str]], limit: int = CLOSEST_WORDS) -> list[tuple[str, float]]:
    """The ``limit`` vocabulary words most like ``word`` with their similarity, 0 to 1, best first."""
    found = process.extract(word, _lengths(by_length, word, CLOSEST_LENGTH_SLACK), scorer=fuzz.ratio, limit=limit)
    return [(w, round(score / 100, 3)) for w, score, _ in found if score > 0]


def sort_key(tier: str, matched: int, score: float) -> tuple[int, int, float]:
    """How hits order across tiers: tier first, then more matched words, then the higher score."""
    return (rank(tier), -matched, -score)

