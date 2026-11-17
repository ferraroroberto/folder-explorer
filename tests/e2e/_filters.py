"""Drive the Filters & sort sheet of Explore, Largest and Types: its controls live in a dialog, so a test opens it, acts, and presses Done."""

from __future__ import annotations

from playwright.sync_api import Page, expect

SHEET = "#filtersDialog"


def open_filters(page: Page) -> None:
    page.locator("#filtersOpen").click()
    expect(page.locator(SHEET)).to_be_visible()


def close_filters(page: Page) -> None:
    page.locator(SHEET + ' [data-track="filters-done"]').click()
    expect(page.locator(SHEET)).to_be_hidden()


def choose_root(page: Page, name_prefix: str) -> None:
    """Pick the root whose option text starts with ``name_prefix`` and close the sheet."""
    open_filters(page)
    expect(page.locator("#rootSelect option", has_text=name_prefix)).to_have_count(1)
    value = page.eval_on_selector("#rootSelect", "(s, p) => [...s.options].find(o => o.text.startsWith(p)).value", name_prefix)
    page.locator("#rootSelect").select_option(value)
    close_filters(page)
