"""Settings when the server's answer is missing blocks or no config.json exists: defaults, a plain sentence, and the first-run card."""

from __future__ import annotations

import json
from typing import Any, Callable

import pytest
from playwright.sync_api import Page, Route, expect

from tests.e2e._layout_lint import assert_layout_clean
from tests.e2e._settings import close_setting, open_setting
from tests.e2e.test_settings_folders import BELOW_FOLD

RAW_ERROR = "Cannot read properties"
CREATED_TOAST = "config.json created"


def _serve_config(page: Page, change: Callable[[dict[str, Any]], Any]) -> None:
    """Answer GET /api/config with the real body after *change* (returning a replacement body, or None to keep it)."""

    def handle(route: Route) -> None:
        if route.request.method != "GET":
            route.continue_()
            return
        body = route.fetch().json()
        replaced = change(body)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body if replaced is None else replaced))

    page.route("**/api/config", handle)


def _open_settings(page: Page, app_url: str) -> None:
    page.goto(app_url + "/")
    page.locator('.tab[data-tab="scans"]').click()
    page.locator("#paneScans .open-settings").click()


def _drop(*blocks: str) -> Callable[[dict[str, Any]], None]:
    def change(body: dict[str, Any]) -> None:
        for block in blocks:
            body["config"].pop(block, None)

    return change


@pytest.mark.parametrize("blocks", [("tray",), ("tray", "desktop", "outlook", "email"), ("tray", "desktop", "outlook", "email", "schedule", "exclude")])
def test_a_config_without_some_blocks_loads_settings_with_their_defaults(page: Page, app_url: str, blocks: tuple[str, ...]) -> None:
    _serve_config(page, _drop(*blocks))
    _open_settings(page, app_url)
    sheet = page.locator("#settingsDialog")
    expect(sheet).to_be_visible()
    expect(page.locator(".toast")).to_have_count(0)
    expect(sheet.locator("#setupCard")).to_be_hidden()
    tray = open_setting(page, "tray-click")
    expect(tray.locator("#trayClickSlot [role=switch]")).to_have_attribute("aria-checked", "true")
    expect(tray.locator("#trayClickNote")).to_contain_text("opens the small Quick search window")


def test_a_nightly_schedule_shows_its_time_field_inside_the_card(page: Page, app_url: str) -> None:
    def change(body: dict[str, Any]) -> None:
        body["config"]["schedule"] = "nightly"
        body["config"]["schedule_time"] = "03:30"

    _serve_config(page, change)
    _open_settings(page, app_url)
    expect(open_setting(page, "schedule").locator("#scheduleTime")).to_be_visible()
    assert_layout_clean(page, allow=BELOW_FOLD)


def test_a_server_without_the_remote_and_archive_blocks_still_loads_settings(page: Page, app_url: str) -> None:
    def change(body: dict[str, Any]) -> None:
        for key in ("remote", "archive", "index_bytes"):
            body.pop(key, None)

    _serve_config(page, change)
    _open_settings(page, app_url)
    expect(page.locator("#settingsDialog")).to_be_visible()
    expect(page.locator(".toast")).to_have_count(0)


@pytest.mark.parametrize("answer", [{"config": None}, {}, []])
def test_a_body_that_is_not_a_config_says_what_failed_and_never_shows_the_script_error(page: Page, app_url: str, answer: Any) -> None:
    _serve_config(page, lambda body: answer)
    _open_settings(page, app_url)
    toast = page.locator(".toast")
    expect(toast).to_be_visible()
    expect(toast).to_contain_text("Settings couldn't be read from the server")
    expect(toast).to_contain_text("Reload")
    assert RAW_ERROR not in toast.inner_text() and "undefined" not in toast.inner_text()
    expect(page.locator("#settingsDialog")).not_to_be_visible()


def test_a_server_that_does_not_answer_says_what_failed_in_plain_words(page: Page, app_url: str) -> None:
    page.route("**/api/config", lambda route: route.abort() if route.request.method == "GET" else route.continue_())
    _open_settings(page, app_url)
    toast = page.locator(".toast")
    expect(toast).to_contain_text("Settings couldn't be read from the server")
    assert "Failed to fetch" not in toast.inner_text() and "NetworkError" not in toast.inner_text()


def test_a_save_that_the_network_drops_shows_no_script_error(page: Page, app_url: str) -> None:
    _open_settings(page, app_url)
    page.route("**/api/config", lambda route: route.abort() if route.request.method == "POST" else route.continue_())
    open_setting(page, "explorer-window").locator("#reuseExplorerSlot [role=switch]").click()
    toast = page.locator(".toast")
    expect(toast).to_contain_text("Could not save")
    assert "Failed to fetch" not in toast.inner_text() and "NetworkError" not in toast.inner_text() and "Load failed" not in toast.inner_text()


def test_an_existing_config_file_shows_no_setup_card(page: Page, app_url: str) -> None:
    _open_settings(page, app_url)
    expect(page.locator("#settingsDialog")).to_be_visible()
    expect(page.locator("#setupCard")).to_be_hidden()


def _no_file(body: dict[str, Any]) -> None:
    body["config_file"] = {"exists": False}


def test_with_no_config_file_the_card_creates_it_in_one_tap(page: Page, app_url: str) -> None:
    created: list[str] = []
    state = {"exists": False}

    def change(body: dict[str, Any]) -> None:
        body["config_file"] = {"exists": state["exists"]}

    def create(route: Route) -> None:
        created.append(route.request.method)
        state["exists"] = True
        real = route.fetch(method="GET", url=route.request.url.replace("/config/create", "/config")).json()
        real["config_file"] = {"exists": True}
        real["created"] = True
        route.fulfill(status=200, content_type="application/json", body=json.dumps(real))

    _serve_config(page, change)
    page.route("**/api/config/create", create)
    _open_settings(page, app_url)
    card = page.locator("#setupCard")
    expect(card).to_be_visible()
    expect(card).to_contain_text("Set up folder-explorer")
    card.get_by_role("button", name="Create config.json").click()
    expect(page.locator(".toast")).to_contain_text(CREATED_TOAST)
    expect(card).to_be_hidden()
    assert created == ["POST"]
    expect(open_setting(page, "tray-click").locator("#trayClickSlot [role=switch]")).to_be_visible()


def test_a_refused_create_shows_the_servers_sentence(page: Page, app_url: str) -> None:
    _serve_config(page, _no_file)
    page.route("**/api/config/create", lambda route: route.fulfill(status=500, content_type="application/json",
                                                                    body=json.dumps({"detail": "config.example.json is missing"})))
    _open_settings(page, app_url)
    page.locator("#setupCard").get_by_role("button", name="Create config.json").click()
    expect(page.locator(".toast")).to_contain_text("config.example.json is missing")
    expect(page.locator("#setupCard")).to_be_visible()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_setup_card_fits_the_sheet_in_light_and_dark(page: Page, app_url: str, theme: str) -> None:
    page.emulate_media(color_scheme=theme)  # type: ignore[arg-type]
    _serve_config(page, _no_file)
    _open_settings(page, app_url)
    expect(page.locator("#setupCard")).to_be_visible()
    assert_layout_clean(page, allow=BELOW_FOLD)


def test_the_markdown_association_row_shows_its_state_on_this_pc(page: Page, app_url: str) -> None:
    # The e2e instance never touches the registry, so it reports the state as unknown and offers no switch.
    _open_settings(page, app_url)
    sheet = page.locator("#settingsDialog")
    expect(sheet.locator("#mdAssocRow")).to_be_visible()
    expect(sheet.locator("#valMdAssoc")).to_have_text("Unknown")
    detail = open_setting(page, "md-assoc")
    expect(detail.locator("#mdAssocNote")).to_contain_text("unknown")
    expect(detail.locator("#mdAssocSlot [role=switch]")).to_be_disabled()

    asked: list[Any] = []
    _serve_config(page, lambda body: body.update(md_assoc={"state": "off"}))

    def assoc(route: Route) -> None:
        asked.append(route.request.post_data_json)
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"state": "on"}))

    page.route("**/api/md-assoc", assoc)
    _open_settings(page, app_url)
    detail = open_setting(page, "md-assoc")
    switch = detail.locator("#mdAssocSlot [role=switch]")
    expect(switch).to_have_attribute("aria-checked", "false")
    switch.click()
    expect(switch).to_have_attribute("aria-checked", "true")
    expect(detail.locator("#mdAssocNote")).to_contain_text("Windows may ask once")
    expect(sheet.locator("#valMdAssoc")).to_have_text("On")
    assert asked == [{"on": True}]


def test_another_device_gets_no_markdown_association_row(page: Page, app_url: str) -> None:
    _serve_config(page, lambda body: body.update(md_assoc={"state": "pc_only"}))
    _open_settings(page, app_url)
    expect(page.locator("#settingsDialog")).to_be_visible()
    expect(page.locator("#mdAssocRow")).to_be_hidden()


ROW_AUDIT = """() => {
  const problems = [];
  document.querySelectorAll('#settingsDialog .settings-row, #settingDialog [data-setting]:not([hidden]) .settings-row').forEach((row) => {
    if (row.offsetParent === null) return;
    const label = row.querySelector('.settings-row-label');
    const glyph = row.querySelector('.settings-row-icon');
    const name = label ? label.textContent.trim() : '(no label)';
    if (!label || label.getBoundingClientRect().height > 30) problems.push(name + ': label wraps');
    if (!glyph || Math.round(glyph.getBoundingClientRect().width) !== 18) problems.push(name + ': no 18px glyph');
    if (row.matches('button.settings-row[data-open]') && !(row.querySelector('.settings-row-value') && row.querySelector('.settings-row-chevron'))) problems.push(name + ': no value and chevron');
    const value = row.querySelector('.settings-row-value');
    if (value && value.scrollWidth > value.clientWidth + 1) problems.push(name + ': value is cut off');
    if (row.scrollWidth > row.clientWidth + 1) problems.push(name + ': row overflows');
  });
  document.querySelectorAll('#settingsDialog select, #settingDialog select').forEach((select) => {
    if (!select.classList.contains('settings-row-select') || parseFloat(getComputedStyle(select).borderTopWidth) > 0) problems.push((select.id || 'select') + ': drawn as a pill');
  });
  return problems;
}"""


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_every_settings_row_is_one_line_with_a_glyph_on_a_phone_in_light_and_dark(page: Page, app_url: str, theme: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    _open_settings(page, app_url)
    page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
    expect(page.locator("#settingsDialog")).to_be_visible()
    assert page.evaluate(ROW_AUDIT) == []
    for name in ("schedule", "budget", "outlook", "screenshot", "remote"):
        open_setting(page, name)
        assert page.evaluate(ROW_AUDIT) == [], name
        close_setting(page)
