"""The one constant-time comparison for secrets (the password and the token)."""

from __future__ import annotations

import hmac


def secrets_equal(presented: str, expected: str) -> bool:
    """True when both are non-empty and equal, compared in constant time."""
    return bool(presented) and bool(expected) and hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))
