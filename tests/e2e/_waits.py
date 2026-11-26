"""Wait for something a test counts itself: requests seen, answers handed out."""

from __future__ import annotations

from typing import Callable

import pytest
from playwright.sync_api import Page


def wait_until(page: Page, enough: Callable[[], bool], what: str) -> None:
    """Wait up to 8 s for ``enough()`` to hold; fail naming ``what``."""
    for _ in range(80):
        if enough():
            return
        page.wait_for_timeout(100)
    pytest.fail("never happened: " + what)
