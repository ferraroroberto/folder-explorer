"""The Scans tab's email card while a run is going: a bar and counts that move, and the budget stat only when a cap is set."""

from __future__ import annotations

import json
from typing import Any

from playwright.sync_api import Page, Route, expect

from tests.e2e._layout_lint import assert_layout_clean
from tests.e2e._shot import settle


def _running(base: dict[str, Any], counts: dict[str, int]) -> dict[str, Any]:
    run = {"id": 99, "trigger": "app", "scope": "all", "status": "running", "started_at": 1_700_000_000, "finished_at": None,
           "counts": counts, "message": None}
    return dict(base, running=True, runs=[run])


def test_a_running_index_shows_a_bar_and_counts_that_move(page: Page, app_url: str) -> None:
    base = page.request.get(app_url + "/api/email/status").json()
    base["budget_mb"] = None
    steps = [
        {},
        {"to_index": 10, "done": 3, "downloaded": 2, "downloaded_bytes": 5 * 1024 * 1024, "failed": 1, "unchanged": 40},
        {"to_index": 10, "done": 7, "downloaded": 5, "downloaded_bytes": 9 * 1024 * 1024, "failed": 1, "unchanged": 40},
    ]
    calls = {"n": 0}

    def status(route: Route) -> None:
        body = _running(base, steps[min(calls["n"], len(steps) - 1)])
        calls["n"] += 1
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/api/email/status", status)
    page.goto(app_url + "/")
    page.locator('.tab[data-tab="scans"]').click()
    row = page.locator("#emailRuns .run-row").first
    counts = row.locator(".run-counts")
    expect(counts).to_have_text("3 of 10 indexed (30%) · 2 downloaded · 5 MB · 1 failed · 40 unchanged", timeout=10_000)
    assert row.locator(".share-bar > span").get_attribute("style") == "width:30.00%"
    expect(counts).to_contain_text("7 of 10 indexed (70%)", timeout=10_000)
    assert row.locator(".share-bar > span").get_attribute("style") == "width:70.00%"
    # No cap is set, so there is no "Waiting for budget" figure to show.
    expect(page.locator("#emailStats")).not_to_contain_text("Waiting for budget")
    settle(page)
    assert_layout_clean(page)
    page.unroute("**/api/email/status")
