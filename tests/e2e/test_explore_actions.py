"""Explore's hidden-items switch, double-click to open, and the row menu, with the open route faked."""

from __future__ import annotations

import json
from typing import Any

import pytest
from playwright.sync_api import Page, Route, expect

from tests.e2e._layout_lint import assert_layout_clean
from tests.e2e.conftest import LIVE_PORT
from tests.fixture_tree import HIDDEN_FILE, HIDDEN_FOLDER


def _wait_for(page: Page, asked: list[dict[str, Any]], n: int) -> dict[str, Any]:
    for _ in range(50):
        if len(asked) >= n:
            return asked[n - 1]
        page.wait_for_timeout(100)
    pytest.fail("the page never asked to open item %d" % n)


@pytest.mark.desktop_only
def test_hidden_items_switch_and_opening_items_on_the_pc(page: Page, app_url: str) -> None:
    if app_url.endswith(":%d" % LIVE_PORT):
        pytest.skip("the switch writes the settings: never on the live app")
    asked: list[dict[str, Any]] = []

    def fake_open(route: Route) -> None:
        asked.append(route.request.post_data_json)
        route.fulfill(status=200, content_type="application/json", body='{"status": "opened"}')

    page.route("**/api/open", fake_open)
    page.goto(app_url + "/")
    page.locator('.tab[data-tab="explore"]').click()
    dock = page.locator("#contentsDock")
    switch = dock.locator('[data-act="hidden"]')
    expect(switch).to_have_attribute("aria-checked", "false")
    expect(dock.locator(".contents-note")).to_have_text("2 hidden items not listed. Sizes include hidden items.")
    expect(page.locator("#tree .tree-label", has_text=HIDDEN_FOLDER)).to_have_count(0)

    switch.click()
    expect(page.locator("#tree .tree-label", has_text=HIDDEN_FOLDER)).to_be_visible()
    expect(dock.locator(".row-item.is-hidden")).to_have_count(2)
    expect(dock.locator(".row-item.is-hidden .hidden-mark").first).to_be_visible()
    assert_layout_clean(page)
    page.reload()
    expect(page.locator("#contentsDock [data-act=hidden]")).to_have_attribute("aria-checked", "true")
    page.locator("#contentsDock [data-act=hidden]").click()
    expect(page.locator("#contentsDock .list-row", has_text=HIDDEN_FILE)).to_have_count(0)

    notes = dock.locator('.list-row[data-open="file"]', has_text="notes.txt")
    file_id = int(notes.get_attribute("data-id") or "-1")
    notes.dblclick()
    first = _wait_for(page, asked, 1)
    assert (first["kind"], first["id"], first["verb"]) == ("file", file_id, "open")

    dock.locator(".row-item", has_text="notes.txt").locator(".row-kebab").click()
    menu = page.locator(".row-menu")
    expect(menu.locator(".row-menu-label")).to_have_text(["Open", "Edit", "Show in File Explorer", "Copy path"])
    menu.locator(".row-menu-item", has_text="Edit").click()
    assert _wait_for(page, asked, 2)["verb"] == "edit"

    videos = page.locator(".tree-main", has_text="Videos")
    folder_id = int(videos.get_attribute("data-id") or "-1")
    videos.dblclick()
    third = _wait_for(page, asked, 3)
    assert (third["kind"], third["id"], third["verb"]) == ("folder", folder_id, "explore")
    assert all(a["scan_id"] == asked[0]["scan_id"] and a["root"] for a in asked)


@pytest.mark.desktop_only
def test_off_the_pc_the_row_menu_says_opening_works_on_the_pc(page: Page, app_url: str) -> None:
    def as_remote(route: Route) -> None:
        body = route.fetch().json()
        body["can_open"] = False
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route("**/api/folder?*", as_remote)
    page.goto(app_url + "/")
    page.locator('.tab[data-tab="explore"]').click()
    dock = page.locator("#contentsDock")
    dock.locator(".row-item", has_text="notes.txt").locator(".row-kebab").click()
    menu = page.locator(".row-menu")
    expect(menu.locator(".row-menu-head")).to_have_text("Opening files and folders works on the PC")
    expect(menu.locator(".row-menu-label")).to_have_text(["Copy path"])
