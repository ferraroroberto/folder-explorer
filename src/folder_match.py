"""How a folder-search word matches a path: accents and case folded away, or one typo off a folder-name word.

A scan stores each folder's path folded (``fold``: case-folded, accents
dropped) and, per word of the folder names, the ids of the folders whose own
name holds it (``folder_words``, see ``src/scanner.py``). A query word is
folded the same way, so ``comparacion`` and ``comparación`` find each other.
A word of ``FUZZY_MIN`` letters or more also finds its near words: the
folder words one edit away from it (a letter dropped, added, changed or two
swapped), or that start with such a spelling, so ``presupesto`` still finds
``presupuestos``. A near word scores ``1 - 1 / letters``, always below an
exact hit's 1.

This holds no state and reads nothing: ``src/index.py`` asks the index.
"""

from __future__ import annotations

import re
import string
import unicodedata
from array import array

# A folder-name word: a run of letters and digits.
WORD_RE = re.compile(r"[^\W_]+")
# The combining marks an accented letter splits into (NFKD); one regex pass beats a per-letter
# ``unicodedata.combining`` test on every name a scan writes.
MARKS_RE = re.compile(r"[̀-ͯ᪰-᫿᷀-᷿⃐-⃿︠-︯]")
# Shorter words match only exactly or accent-folded: one edit would let "notes" find "nodes".
FUZZY_MIN = 6
# The letters an edit may add or change to, besides the word's own.
ALPHABET = string.ascii_lowercase + string.digits
# The folder-id lists in ``folder_words``: unsigned 32-bit, in id order.
IDS_TYPE = "I"


def fold(text: str) -> str:
    """``text`` case-folded and without accents: ``Comparación`` reads ``comparacion``."""
    low = text.casefold()
    return low if low.isascii() else MARKS_RE.sub("", unicodedata.normalize("NFKD", low)).casefold()


def name_words(folded_name: str) -> set[str]:
    """The words of an already folded folder name."""
    return set(WORD_RE.findall(folded_name))


def fuzzy(folded_word: str) -> bool:
    """Whether a query word is long enough, and one word, to look for its near words."""
    return len(folded_word) >= FUZZY_MIN and folded_word.isalnum()


def near_score(folded_word: str) -> float:
    """What a near word scores for this query word: one edit in its length."""
    return round(1 - 1 / len(folded_word), 3)


def edits(folded_word: str) -> set[str]:
    """Every spelling one edit from ``folded_word``: a letter dropped, swapped with the next, changed or added."""
    w = folded_word
    letters = sorted(set(ALPHABET) | set(w))
    splits = [(w[:i], w[i:]) for i in range(len(w) + 1)]
    out = {a + b[1:] for a, b in splits if b}
    out |= {a + b[1] + b[0] + b[2:] for a, b in splits if len(b) > 1}
    out |= {a + c + b[1:] for a, b in splits if b for c in letters}
    out |= {a + c + b for a, b in splits for c in letters}
    out.discard(w)
    return out


def unpack_ids(blob: bytes) -> array:
    ids = array(IDS_TYPE)
    ids.frombytes(blob)
    return ids
