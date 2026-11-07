"""Settings' folder lists: add and remove a scan root and an email folder, a refusal, and Scans without settings."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from playwright.sync_api import Locator, Page, expect

from tests.e2e.conftest import LIVE_PORT
from tests.e2e._layout_lint import assert_layout_clean

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
def test_folder_lists_add_remove_and_refuse(page: Page, app_url: str, tmp_path: Path) -> None:
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
