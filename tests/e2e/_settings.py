"""Open and close one setting's sheet over the Settings sheet."""

from __future__ import annotations

from playwright.sync_api import Locator, Page, expect


def open_setting(page: Page, name: str) -> Locator:
    """Tap the Settings row that opens *name*'s sheet and return that sheet."""
    page.locator('#settingsDialog [data-open="%s"]' % name).click()
    sheet = page.locator("#settingDialog")
    expect(sheet).to_be_visible()
    return sheet


def close_setting(page: Page) -> None:
    """Close the open setting sheet, back to the Settings sheet beneath it."""
    page.locator("#settingDialog .detail-close").click()
    expect(page.locator("#settingDialog")).to_be_hidden()
