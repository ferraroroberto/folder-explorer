"""The main page keeps its request budget: what it reads in a visible minute, nothing while hidden, one catch-up read per path on return."""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page

from tests.e2e.budget._request_budget import (
    assert_request_budget,
    measure_request_budget,
    visible_budget,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# The one request a hidden page may make: the usage log's own flush on the hide, sent once so
# the tab's last taps are not lost when the tab is closed. It is a write, not a poll, and it
# never repeats while the page stays hidden.
HIDDEN_FLUSH = "/api/usage/events"


@pytest.mark.desktop_only
def test_main_page_keeps_its_request_budget(page: Page, app_url: str) -> None:
    # The suite's context answers the Archive hand-off long poll at once, so the page re-asks
    # every PENDING_MS: the visible count is the fastest cadence a server could drive.
    reading = measure_request_budget(page, f"{app_url}/")
    flushes = reading.hidden.pop(HIDDEN_FLUSH, 0)
    assert flushes <= 1, f"{flushes} usage flushes while hidden, one on the hide only ({reading.summary()})"
    assert_request_budget(reading, visible_budget(REPO_ROOT))
