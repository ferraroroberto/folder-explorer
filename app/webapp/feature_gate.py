"""A router-level guard for a feature block: its API answers one plain sentence while the block is switched off."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException

from src import config, features


def require(key: str) -> Callable[[], None]:
    """A FastAPI dependency that refuses with 404 while block ``key`` is off.

    404, not 403: the block is not part of this machine's app, so a caller sees the route as absent, and the page's own
    handlers already treat a 404 as "nothing there". The setting is read on every request, so a switch applies at once.
    """

    def guard() -> None:
        if not features.enabled(config.load(), key):
            raise HTTPException(status_code=404, detail=features.OFF_SENTENCE)

    return guard
