"""The rendered app: each tab, the folder tree and sheet, the filters, scans and settings."""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e._layout_lint import assert_layout_clean

TABS = ("explore", "largest", "types", "scans")
TITLES = {"explore": "Explore", "largest": "Largest", "types": "Types", "scans": "Scans"}


def _open(page: Page, app_url: str) -> list[str]:
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(app_url + "/")
    expect(page.locator("#tree .tree-row").first).to_be_visible()
    return errors


def _tab(page: Page, tab: str) -> Any:
    page.locator('.tab[data-tab="%s"]' % tab).click()
    pane = page.locator("#pane" + TITLES[tab])
    expect(pane).to_be_visible()
    return pane


def test_every_tab_leads_with_its_own_header_and_lays_out_clean(page: Page, app_url: str) -> None:
    errors = _open(page, app_url)
    for theme in ("light", "dark"):
        page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
        for tab in TABS:
            pane = _tab(page, tab)
            first = pane.locator("> *").first
            expect(first).to_have_class("card home-head")
            expect(first.locator(".home-title")).to_have_text(TITLES[tab])
            page.wait_for_timeout(150)
            assert_layout_clean(page)
    assert errors == []


def test_tree_expands_and_selecting_a_folder_shows_its_contents(page: Page, app_url: str, browser_name: str) -> None:
    _open(page, app_url)
    videos = page.locator(".tree-main", has_text="Videos")
    videos.click()
    if browser_name == "webkit":
        sheet = page.locator("#contentsDialog")
        expect(sheet).to_be_visible()
        expect(sheet.locator(".list-row", has_text="drone-footage")).to_be_visible()
        assert_layout_clean(page)
        sheet.locator("[data-close]").click()
    else:
        expect(page.locator("#contentsDock .card-title")).to_have_text("Videos")
        expect(page.locator("#contentsDock .list-row", has_text="drone-footage")).to_be_visible()
    expect(page.locator("#scopePill")).to_contain_text("Videos")
    _tab(page, "largest")
    expect(page.locator("#topList .list-row").first).to_contain_text("river-01.mp4")


def test_type_filter_narrows_every_view(page: Page, app_url: str) -> None:
    _open(page, app_url)
    _tab(page, "types")
    page.locator('#typesList .list-row[data-ext="mp4"]').click()
    expect(page.locator("#typesFilterLine")).to_be_visible()
    _tab(page, "explore")
    names = page.locator("#tree .tree-label")
    expect(names).to_have_text(["Cloud drive", "Videos", "drone-footage"])
    _tab(page, "types")
    page.locator("#clearFilter").click()
    expect(page.locator("#typesFilterLine")).to_be_hidden()


def test_on_disk_basis_switches_the_totals(page: Page, app_url: str) -> None:
    _open(page, app_url)
    expect(page.locator("#summary .stat-label").first).to_have_text("Total size")
    page.locator('#basisSeg [data-value="disk"]').click()
    expect(page.locator("#summary .stat-label").first).to_have_text("On disk")


def test_scans_lists_each_root_once_and_rescans(page: Page, app_url: str, roots_count: int) -> None:
    _open(page, app_url)
    _tab(page, "scans")
    rows = page.locator("#rootsList .root-row")
    expect(rows).to_have_count(roots_count)
    names = [t.strip() for t in rows.locator(".action-row-title").all_inner_texts()]
    assert len(set(names)) == len(names)
    expect(page.locator("#toolbar")).to_be_hidden()
    page.locator("#rescanAll").click()
    expect(page.locator(".toast")).to_be_visible()
    expect(page.locator("#rescanAll")).to_be_enabled(timeout=20_000)
    assert_layout_clean(page)


@pytest.mark.desktop_only
def test_settings_show_remote_access_and_the_schedule(page: Page, app_url: str) -> None:
    _open(page, app_url)
    page.locator(".open-settings").first.click()
    sheet = page.locator("#settingsDialog")
    expect(sheet).to_be_visible()
    expect(sheet.locator("#remoteSwitchSlot [role=switch]")).to_have_attribute("aria-checked", "false")
    expect(sheet.locator("#remoteNote")).to_contain_text("this PC only")
    expect(sheet.locator("#scheduleSelect")).to_have_value("off")
    assert_layout_clean(page)
