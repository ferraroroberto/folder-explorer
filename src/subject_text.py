"""Strip reply/forward prefixes from an email or message subject.

One definition of the prefix pattern, so a repo that archives, names or
threads messages by subject never re-derives it. The colon is required: an
optional colon makes any subject that merely begins with "Re", "Rv" or "Fw"
lose its first letters ("Recibo de pago" -> "cibo de pago", "Revisión anual"
-> "visión anual"). Both the ASCII ``:`` and the full-width ``：`` count, the
match is case-insensitive, and a chain such as ``RE: RV: Fwd:`` goes in one
pass.

Only ``Re``, ``Rv``, ``Fw`` and ``Fwd`` are recognised. A localized prefix
(``AW``, ``WG``, ``SV``, ``TR``, ``ENC``) is one more alternative in
``RE_REPLY_PREFIX`` once a caller needs it; the colon requirement keeps such an
addition from touching ordinary words.

Stdlib-only, no logging and no repo-specific imports, so the file is copied
byte-for-byte into adopters (manifest key ``subject_text``).
"""

from __future__ import annotations

import re

#: A run of reply/forward prefixes at the start of a subject, with the trailing
#: whitespace. The colon (ASCII or full-width) is mandatory.
RE_REPLY_PREFIX = re.compile(r"^\s*(?:(?:re|rv|fwd?)\s*[:：]\s*)+", re.IGNORECASE)


def clean_subject(raw: str | None) -> str:
    """Return ``raw`` without its leading reply/forward prefixes, trimmed.

    ``None`` and the empty string give ``""``, never ``None``, so a caller can
    use the result as a name without a guard.
    """
    if not raw:
        return ""
    return RE_REPLY_PREFIX.sub("", raw.strip()).strip()


__all__ = ["RE_REPLY_PREFIX", "clean_subject"]
