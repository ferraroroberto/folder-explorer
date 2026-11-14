"""The Settings sheet: remote access, the schedule and the download budget, then the folder lists (add and remove a scan root and an email folder, a refusal), Scans without settings, and the tray-click switch."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from playwright.sync_api import Locator, Page, Response, expect

from tests.e2e._layout_lint import assert_layout_clean
from tests.e2e.conftest import LIVE_PORT

# The sheet scrolls, so its controls above or below the visible part sit outside
# the viewport; the rows' and lists' own overflow is still linted.
BELOW_FOLD = {"#settingsDialog " + tag: "outside the visible part of the scrolling sheet"
              for tag in ("button", "input", "select", "[role=switch]")}


def _add(section: Locator, path: str) -> None:
    section.locator(".folder-add input").fill(path)
    section.locator(".folder-add button[type=submit]").click()


def _remove(page: Page, section: Locator, path: str) -> None:
    section.locator(".action-row", has_text=path).locator(".action-row-kebab").click()
    page.get_by_role("menuitem", name="Remove").click()
    confirm = page.locator("#folderRemoveDialog")
    expect(confirm).to_be_visible()
    expect(confirm.locator("[data-remove-path]")).to_have_text(path)
    confirm.locator("[data-remove-ok]").click()
    expect(confirm).to_be_hidden()


@pytest.fixture()
def saved_lists(page: Page, app_url: str) -> Iterator[None]:
    """Put the session app's folder lists back however the test ends, so later tests see the fixture roots."""
    if app_url.endswith(":%d" % LIVE_PORT):
        pytest.skip("the lists write the settings: never on the live app")
    cfg = page.request.get(app_url + "/api/config").json()["config"]
    yield
    page.request.post(app_url + "/api/config", data={"email": cfg["email"]})
    page.request.post(app_url + "/api/config", data={"roots": cfg["roots"]})


@pytest.mark.usefixtures("saved_lists")
def test_settings_show_remote_access_and_the_schedule_and_edit_the_folder_lists(page: Page, app_url: str, tmp_path: Path) -> None:
    extra = tmp_path / "Invented archive"
    inbox = extra / "Inbox"
    inbox.mkdir(parents=True)
    page.goto(app_url + "/")
    page.locator('.tab[data-tab="scans"]').click()
    scans = page.locator("#paneScans")
    expect(scans).to_be_visible()
    assert scans.locator("textarea, input[type=text], #emailBudget").count() == 0

    scans.locator(".open-settings").click()
    sheet = page.locator("#settingsDialog")
    expect(sheet).to_be_visible()
    expect(sheet.locator("#remoteSwitchSlot [role=switch]")).to_have_attribute("aria-checked", "false")
    expect(sheet.locator("#remoteNote")).to_contain_text("this PC only")
    expect(sheet.locator("#scheduleSelect")).to_have_value("off")
    # A config that never set a download budget has no limit, and the menu offers that first.
    expect(sheet.locator("#emailBudget")).to_have_value("")
    expect(sheet.locator("#emailBudget option").first).to_have_text("No limit (recommended)")
    roots = sheet.locator("#rootsEditor")
    emails = sheet.locator("#emailRootsEditor")
    expect(roots).to_be_visible()
    field, add = (roots.locator(".folder-add " + sel).bounding_box() for sel in ("input", "button"))
    # A fine pointer's 44px field box holds a 36px visible control (transparent hit inset); a coarse pointer's field is a real 44px.
    visible = 44 if page.evaluate("matchMedia('(pointer: coarse)').matches") else 36
    assert field and add and abs(add["height"] - visible) <= 1, "Add matches the field's visible height, not the 48px primary"
    assert abs((field["y"] + field["height"] / 2) - (add["y"] + add["height"] / 2)) <= 1
    before = roots.locator(".action-row").count()

    _add(roots, str(tmp_path / "Not there"))
    expect(roots.locator(".folder-error")).to_contain_text("Folder not found")
    _add(roots, str(extra))
    expect(roots.locator(".action-row")).to_have_count(before + 1)
    expect(roots.locator(".folder-error")).to_be_empty()

    _add(emails, str(inbox))
    expect(emails.locator(".action-row", has_text=str(inbox))).to_be_visible()
    assert_layout_clean(page, allow=BELOW_FOLD)

    _remove(page, roots, str(extra))
    expect(roots.locator(".folder-error")).to_contain_text("Remove " + str(inbox) + " from email folders first")
    expect(roots.locator(".action-row")).to_have_count(before + 1)

    _remove(page, emails, str(inbox))
    expect(emails.locator(".action-row", has_text=str(inbox))).to_have_count(0)
    _remove(page, roots, str(extra))
    expect(roots.locator(".action-row")).to_have_count(before)
    expect(roots.locator(".folder-error")).to_be_empty()


def test_the_tray_click_switch_saves_with_a_toast_and_says_what_a_click_opens(page: Page, app_url: str) -> None:
    if app_url.endswith(":%d" % LIVE_PORT):
        pytest.skip("the switch writes the settings: never on the live app")
    page.goto(app_url + "/")
    page.locator("#paneSearch .home-settings").click()
    sheet = page.locator("#settingsDialog")
    expect(sheet).to_be_visible()
    tray = sheet.locator("#trayClickSlot [role=switch]")
    expect(tray).to_have_attribute("aria-checked", "true")
    expect(sheet.locator("#trayClickNote")).to_contain_text("opens the small Quick search window")
    try:
        tray.click()
        expect(tray).to_have_attribute("aria-checked", "false")
        expect(page.locator(".toast")).to_contain_text("Tray click opens the app")
        expect(sheet.locator("#trayClickNote")).to_contain_text("Quick search stays in the tray menu")
        assert page.request.get(app_url + "/api/config").json()["config"]["tray"] == {"left_click": "open_app"}
        tray.click()
        expect(tray).to_have_attribute("aria-checked", "true")
        expect(page.locator(".toast")).to_contain_text("Tray click opens Quick search")
    finally:
        page.request.post(app_url + "/api/config", data={"tray": {"left_click": "quick_search"}})


def test_the_hold_to_capture_switch_is_off_by_default_and_saves(page: Page, app_url: str) -> None:
    if app_url.endswith(":%d" % LIVE_PORT):
        pytest.skip("the switch writes the settings: never on the live app")
    page.goto(app_url + "/")
    page.locator("#paneSearch .home-settings").click()
    sheet = page.locator("#settingsDialog")
    expect(sheet).to_be_visible()
    hold = sheet.locator("#snapHoldSlot [role=switch]")
    expect(hold).to_have_attribute("aria-checked", "false")
    expect(sheet.locator("#snapHoldNote")).to_contain_text("a click to start")
    try:
        hold.click()
        expect(hold).to_have_attribute("aria-checked", "true")
        expect(sheet.locator("#snapHoldNote")).to_contain_text("pressing, dragging and releasing")
        assert page.request.get(app_url + "/api/config").json()["config"]["snap"]["hold_to_capture"] is True
    finally:
        page.request.post(app_url + "/api/config", data={"snap": {"hold_to_capture": False}})


def _saved(response: Response) -> bool:
    return response.url.endswith("/api/config") and response.request.method == "POST"


def test_every_screenshot_setting_is_in_settings_and_a_refusal_puts_the_saved_value_back(page: Page, app_url: str) -> None:
    if app_url.endswith(":%d" % LIVE_PORT):
        pytest.skip("the rows write the settings: never on the live app")
    page.goto(app_url + "/")
    page.locator("#paneSearch .home-settings").click()
    sheet = page.locator("#settingsDialog")
    expect(sheet).to_be_visible()
    clipboard = sheet.locator("#snapClipboardSlot [role=switch]")
    expect(clipboard).to_have_attribute("aria-checked", "true")
    expect(sheet.locator("#snapLoupeSlot [role=switch]")).to_have_attribute("aria-checked", "true")
    expect(sheet.locator("#snapQuality")).to_have_value("90")
    expect(sheet.locator("#snapPrefix")).to_have_value("snap")
    expect(sheet.locator("#snapHotkey")).to_have_value("ctrl+shift+a")
    expect(sheet.locator("#snapFolderPath")).not_to_be_empty()
    expect(sheet.locator("#snapHotkeyNote")).to_contain_text("next time the tray starts")
    try:
        with page.expect_response(_saved):
            sheet.locator("#snapQuality").select_option("95")
        assert page.request.get(app_url + "/api/config").json()["config"]["snap"]["jpeg_quality"] == 95
        with page.expect_response(_saved):
            clipboard.click()
        expect(clipboard).to_have_attribute("aria-checked", "false")
        expect(sheet.locator("#snapClipboardNote")).to_contain_text("only saved as a file")
        assert page.request.get(app_url + "/api/config").json()["config"]["snap"]["copy_to_clipboard"] is False
        sheet.locator("#snapPrefix").fill("shot")
        with page.expect_response(_saved):
            sheet.locator("#snapPrefix").press("Enter")
        expect(sheet.locator("#snapPrefixNote")).to_contain_text("shot_date_time.jpg")
        sheet.locator("#snapPrefix").fill("bad:name")
        with page.expect_response(_saved):
            sheet.locator("#snapPrefix").press("Enter")
        expect(page.locator(".toast").last).to_contain_text("prefix")
        expect(sheet.locator("#snapPrefix")).to_have_value("shot")
        sheet.locator("#snapHotkey").fill("ctrl+shift")
        with page.expect_response(_saved):
            sheet.locator("#snapHotkey").press("Enter")
        expect(sheet.locator("#snapHotkey")).to_have_value("ctrl+shift+a")
        assert page.request.get(app_url + "/api/config").json()["config"]["snap"]["file_prefix"] == "shot"
        sheet.locator("#snapOpenFolder").click()
        expect(page.locator(".toast").last).to_contain_text("Could not open the folder")
    finally:
        page.request.post(app_url + "/api/config", data={"snap": {"jpeg_quality": 90, "copy_to_clipboard": True, "file_prefix": "snap"}})


def test_the_archive_category_switch_is_off_by_default_and_saves(page: Page, app_url: str) -> None:
    if app_url.endswith(":%d" % LIVE_PORT):
        pytest.skip("the switch writes the settings: never on the live app")
    page.goto(app_url + "/")
    page.locator("#paneSearch .home-settings").click()
    sheet = page.locator("#settingsDialog")
    expect(sheet).to_be_visible()
    category = sheet.locator("#archiveCategorySlot [role=switch]")
    expect(category).to_have_attribute("aria-label", "Add an Outlook category when archiving")
    expect(category).to_have_attribute("aria-checked", "false")
    expect(sheet.locator("#archiveCategoryNote")).to_contain_text("show no category row")
    try:
        category.click()
        expect(category).to_have_attribute("aria-checked", "true")
        expect(sheet.locator("#archiveCategoryNote")).to_contain_text("stamp it on the mail")
        assert page.request.get(app_url + "/api/config").json()["config"]["outlook"]["add_category"] is True
    finally:
        page.request.post(app_url + "/api/config", data={"outlook": {"add_category": False}})


def test_the_move_to_archive_switch_is_off_by_default_and_saves(page: Page, app_url: str) -> None:
    if app_url.endswith(":%d" % LIVE_PORT):
        pytest.skip("the switch writes the settings: never on the live app")
    page.goto(app_url + "/")
    page.locator("#paneSearch .home-settings").click()
    sheet = page.locator("#settingsDialog")
    expect(sheet).to_be_visible()
    move = sheet.locator("#archiveMoveSlot [role=switch]")
    expect(move).to_have_attribute("aria-label", "Move to the Outlook archive folder when archiving")
    expect(move).to_have_attribute("aria-checked", "false")
    expect(sheet.locator("#archiveMoveNote")).to_contain_text("leaves it where it is in Outlook")
    try:
        move.click()
        expect(move).to_have_attribute("aria-checked", "true")
        expect(sheet.locator("#archiveMoveNote")).to_contain_text("also move it to the Outlook folder")
        assert page.request.get(app_url + "/api/config").json()["config"]["outlook"]["move_to_archive"] is True
    finally:
        page.request.post(app_url + "/api/config", data={"outlook": {"move_to_archive": False}})
