"""The Scans tab's email card while a run is going: a bar and counts that move, and the budget stat only when a cap is set."""

from __future__ import annotations

import json
from typing import Any

from playwright.sync_api import Page, Route, expect

from tests.e2e._layout_lint import assert_layout_clean
from tests.e2e._shot import settle
from tests.e2e._surface import surface
from tests.e2e._waits import wait_until

#: The card's pause between two reads while a run is going (POLL_MS in email-scans.js).
EMAIL_POLL_MS = 2000

# Lets a case hide and show the page, firing the `visibilitychange` a tab switch or a screen lock would.
_HIDEABLE = """(() => {
  let hidden = false;
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
  window.__setHidden = (value) => { hidden = value; document.dispatchEvent(new Event('visibilitychange')); };
})();"""


def _running(base: dict[str, Any], counts: dict[str, int]) -> dict[str, Any]:
    run = {"id": 99, "trigger": "app", "scope": "all", "status": "running", "state": "running", "reason": None, "live": True, "attention": False, "started_at": 1_700_000_000, "finished_at": None,
           "counts": counts, "message": None}
    return dict(base, running=True, state="running", reason=None, runs=[run])


def test_a_running_index_shows_a_bar_and_counts_that_move(page: Page, app_url: str) -> None:
    base = page.request.get(app_url + "/api/email/status").json()
    base["budget_mb"] = None
    base["failed"] = 1
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
    page.route("**/api/email/failures", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"failures": [{"path": "C:/Invented/Mail/broken-01.msg", "reason": "not a mail file"}], "total": 1})))
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
    page.locator('#emailStats [data-email-act="failures"]').click()
    failures = page.locator("#failuresDialog")
    expect(failures.locator(".fail-row")).to_have_count(1)
    surface(page, "scans.failures")
    failures.locator("[data-close]").first.click()
    expect(failures).to_be_hidden()
    page.unroute("**/api/email/status")


def test_a_running_index_is_polled_only_while_its_card_is_on_screen(clock_page: Page, app_url: str) -> None:
    page = clock_page
    body = json.dumps(_running(page.request.get(app_url + "/api/email/status").json(), {}))
    asked: list[str] = []
    page.on("request", lambda r: asked.append(r.url) if "/api/email/status" in r.url else None)
    page.route("**/api/email/status", lambda route: route.fulfill(status=200, content_type="application/json", body=body))
    page.add_init_script(_HIDEABLE)

    def next_tick_reads(what: str) -> None:
        page.wait_for_timeout(300)  # the read in flight settles and arms the next tick
        before = len(asked)
        page.clock.run_for(EMAIL_POLL_MS + 600)
        wait_until(page, lambda: len(asked) > before, what)

    def stays_quiet(what: str) -> None:
        page.wait_for_timeout(300)
        before = len(asked)
        page.clock.run_for(3 * EMAIL_POLL_MS)
        page.evaluate("() => fetch('/healthz').then((r) => r.ok)")  # a request the jump made is reported before this answer
        assert len(asked) == before, what

    page.goto(app_url + "/")
    page.locator('.tab[data-tab="scans"]').click()
    expect(page.locator("#emailRuns .run-row")).to_have_count(1)
    next_tick_reads("the poll while the card is on screen")
    page.evaluate("() => window.__setHidden(true)")
    stays_quiet("no email status call while the page is hidden")
    before = len(asked)
    page.evaluate("() => window.__setHidden(false)")
    wait_until(page, lambda: len(asked) > before, "a read on the return to the page")
    next_tick_reads("the poll again after the return")
    page.locator('.tab[data-tab="search"]').click()
    stays_quiet("no email status call off the Scans tab")
    before = len(asked)
    page.locator('.tab[data-tab="scans"]').click()
    wait_until(page, lambda: len(asked) > before, "a read on the return to the Scans tab")
    next_tick_reads("the poll again on the Scans tab")
