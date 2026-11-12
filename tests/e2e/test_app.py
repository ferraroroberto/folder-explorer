"""The rendered app: each tab, the folder tree and sheet, the type filter and scans."""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Page, expect

from tests.e2e._layout_lint import assert_layout_clean
from tests.e2e._shot import settle

TABS = ("search", "explore", "largest", "types", "scans")
TITLES = {"search": "Search", "explore": "Explore", "largest": "Largest", "types": "Types", "scans": "Scans"}
# The installed iPhone app's bottom safe-area inset (the home indicator) in portrait. Headless
# WebKit reports every env(safe-area-inset-*) as 0, so the pill test applies it from the page head.
HOME_INDICATOR_INSET = 34
# The vendored pill's outer geometry, the same in every fleet app: height, and the margin to
# the viewport's bottom, left and right edges.
PILL_HEIGHT = 61
PILL_MARGIN = 21


def _assert_pill_clears_safe_area(page: Page) -> None:
    """The phone pill keeps the fleet geometry and sits above the installed app's safe-area line.

    Under `viewport-fit=cover` the installed app's web view runs under the home indicator, while
    the pill's bottom offset is a fixed margin, so the device's inset applies; under
    `viewport-fit=auto` the web view ends above the indicator and the inset is 0."""
    viewport = page.locator('meta[name="viewport"]').get_attribute("content") or ""
    inset = HOME_INDICATOR_INSET if "viewport-fit=cover" in viewport else 0
    box = page.evaluate(
        """() => {
          const r = document.querySelector('nav.tabs').getBoundingClientRect();
          const labels = [...document.querySelectorAll('nav.tabs .tab-label')].map(l => {
            const b = l.getBoundingClientRect();
            return {top: b.top, bottom: b.bottom, cut: l.scrollWidth > l.clientWidth + 1};
          });
          return {w: innerWidth, h: innerHeight, left: r.left, right: r.right, top: r.top, bottom: r.bottom, labels};
        }"""
    )
    safe_line = box["h"] - inset
    assert box["bottom"] <= safe_line, f"pill bottom {box['bottom']} below the safe-area line {safe_line} ({viewport})"
    assert box["bottom"] - box["top"] == PILL_HEIGHT, box
    assert box["h"] - box["bottom"] == PILL_MARGIN and box["left"] == PILL_MARGIN, box
    assert box["w"] - box["right"] == PILL_MARGIN, box
    for label in box["labels"]:
        assert box["top"] <= label["top"] and label["bottom"] <= min(box["bottom"], safe_line), (label, box)
        assert not label["cut"], label
    expect(page.locator('meta[name="apple-mobile-web-app-status-bar-style"]')).to_have_attribute(
        "content", "black-translucent"
    )


def _open(page: Page, app_url: str) -> list[str]:
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(app_url + "/")
    page.locator('.tab[data-tab="explore"]').click()
    expect(page.locator("#tree .tree-row").first).to_be_visible()
    return errors


def _tab(page: Page, tab: str) -> Any:
    page.locator('.tab[data-tab="%s"]' % tab).click()
    pane = page.locator("#pane" + TITLES[tab])
    expect(pane).to_be_visible()
    return pane


def test_every_tab_leads_with_its_own_header_and_lays_out_clean(page: Page, app_url: str, browser_name: str) -> None:
    errors = _open(page, app_url)
    if browser_name == "webkit":
        _assert_pill_clears_safe_area(page)
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


def test_tree_selecting_a_folder_shows_its_contents_and_the_basis_switches_totals(page: Page, app_url: str, browser_name: str) -> None:
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
    _tab(page, "explore")
    expect(page.locator("#summary .stat-label").first).to_have_text("Total size")
    page.locator('#basisSeg [data-value="disk"]').click()
    expect(page.locator("#summary .stat-label").first).to_have_text("On disk")


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


def test_scans_lists_each_root_once_and_rescans(page: Page, app_url: str, roots_count: int) -> None:
    _open(page, app_url)
    _tab(page, "scans")
    rows = page.locator("#rootsList .root-row")
    expect(rows).to_have_count(roots_count)
    names = [t.strip() for t in rows.locator(".action-row-title").all_inner_texts()]
    assert len(set(names)) == len(names)
    expect(page.locator("#toolbar")).to_be_hidden()
    expect(page.locator("#emailCard")).to_be_visible()
    boxes = [page.locator(sel).bounding_box() for sel in ("#rescanAll", "#indexEmails")]
    assert all(boxes)
    left, right = boxes[0], boxes[1]
    assert abs(left["x"] - right["x"]) <= 1 and abs(left["width"] - right["width"]) <= 1
    assert abs(left["height"] - right["height"]) <= 1
    page.locator("#rescanAll").click()
    expect(page.locator(".toast")).to_be_visible()
    expect(page.locator("#rescanAll")).to_be_enabled(timeout=20_000)
    # A fixture rescan can end inside the nav pill's half-second spring to the Scans tab, whose
    # overshoot reaches past the bar; the lint measures the layout once that motion has ended.
    settle(page)
    assert_layout_clean(page)
