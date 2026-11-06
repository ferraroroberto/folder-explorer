"""The Archive card, the Archive sheet, the macro's hand-off, Find in mailbox and the macro sheet, in a browser.

The disposable instance runs with the Outlook bridge off, and every Outlook
route is answered here with invented data, so no test reaches this PC's
Outlook. The macro sheet's text is the real template from the repo.
"""

from __future__ import annotations

import json
from typing import Any

from playwright.sync_api import Page, Route, expect

from tests.e2e._layout_lint import assert_layout_clean

ROOT = "/archive/Mail"
MAIL = {"subject": "Kickoff agenda", "sender": "leo@example.org", "date_sent": "2026-11-05T09:12:00",
        "attachment_count": 1, "where": "Inbox", "message_id": "kick@example.invalid",
        "preview": "Hi team, the kickoff agenda is attached. See you on Monday."}


# The sheet caps to the viewport and scrolls its card, so on a phone these sit below its fold by design.
_FOLD = "below the fold of the scrolling sheet on a phone"
BELOW_FOLD = {sel: _FOLD for sel in ("#archiveDialog #archMacro", "#archiveDialog .arch-row > button", "#archiveDialog #archSearch",
                                     "#archiveDialog #archFound button", "#archiveDialog #archCat", "#archiveDialog #archDate > button")}


def _suggestion(rel: str, label: str, tone: str, note: str, seq: str, score: float, folder_id: int) -> dict[str, Any]:
    return {"folder_path": ROOT + "/" + rel, "display_name": "\\".join(rel.split("/")[-2:]),
            "why": [{"label": label, "tone": tone}], "note": note, "score": score, "similar": folder_id,
            "example": "Agenda for week %d" % folder_id,
            "saved_as": {"auto": seq + " - Kickoff agenda.msg", "on": "2026-11-05 - " + seq + " - Kickoff agenda.msg",
                         "off": seq + " - Kickoff agenda.msg"},
            "location": {"root": "invented-root", "scan_id": 7, "id": folder_id}}


PREPARED = {
    "ok": True, "token": "invented-token", "mail": MAIL, "already_archived": None, "category": "Archived", "roots": [ROOT],
    "suggestions": [
        _suggestion("Clients/Project Alpha/2026", "Same subject", "accent", "This conversation is filed here", "004", 0.81, 21),
        _suggestion("Clients/Project Alpha/2025", "Sender", "", "12 emails from leo@example.org", "031", 0.52, 22),
        _suggestion("Internal/Meetings", "Topic", "", "", "118", 0.3, 23),
    ],
}


def _json(route: Route, body: Any) -> None:
    route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


def _archive_ready(page: Page) -> None:
    """The status the PC with Outlook (classic) and pywin32 reports; the instance itself has the bridge off."""
    def status(route: Route) -> None:
        body = route.fetch().json()
        body["archive"] = {"ready": True, "reason": None}
        _json(route, body)
    page.route("**/api/search/status", status)


def _lint_both_themes(page: Page, **kwargs: Any) -> None:
    for theme in ("light", "dark"):
        page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
        assert_layout_clean(page, **kwargs)


def _errors(page: Page) -> list[str]:
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    return errors


def test_the_card_opens_the_sheet_which_files_to_the_chosen_folder(page: Page, app_url: str, browser_name: str) -> None:
    errors = _errors(page)
    filed: list[dict[str, Any]] = []
    _archive_ready(page)
    page.route("**/api/archive/pending", lambda r: _json(r, {"mail": None}))
    page.route("**/api/archive/current", lambda r: _json(r, PREPARED))
    page.route("**/api/archive/folders?*", lambda r: _json(r, {"folders": [{"path": ROOT + "/Clients/Northwind/2026", "name": "2026",
                                                                           "location": {"root": "invented-root", "scan_id": 7, "id": 40}}]}))
    opened: list[dict[str, Any]] = []
    page.route("**/api/open", lambda r: opened.append(r.request.post_data_json) or _json(r, {"status": "opened"}))

    def file_mail(route: Route) -> None:
        filed.append(route.request.post_data_json)
        _json(route, {"ok": True, "folder_path": filed[-1]["folder"], "files": [filed[-1]["folder"] + "/005 - Kickoff agenda.msg"],
                      "categorized": True, "indexing": True, "error": None})
    page.route("**/api/archive/file", file_mail)

    page.goto(app_url + "/")
    card = page.locator("#outlookCard")
    expect(card).to_be_visible()
    expect(page.locator("#outlookMeta")).to_have_text("The mail open or selected in Outlook (classic)")
    _lint_both_themes(page)
    page.locator("#openArchive").click()
    sheet = page.locator("#archiveDialog")
    expect(sheet).to_be_visible()
    options = sheet.locator(".arch-opt")
    expect(options).to_have_count(3)
    expect(options.first).to_have_attribute("aria-checked", "true")
    expect(options.first.locator(".ao-path")).to_have_text("Clients\\Project Alpha\\2026")
    expect(sheet.locator(".ao-line .rank")).to_have_text(["1", "2", "3"])
    expect(sheet.locator(".ao-score .chip")).to_have_text(["81%", "52%", "30%"])
    expect(sheet.locator(".ao-score .chip.tone-attention")).to_have_count(1)
    expect(options.nth(2).locator(".ao-similar")).to_have_text("23 similar emails · e.g. “Agenda for week 23”")
    expect(sheet.locator("#archiveGo")).to_have_text("Archive to Project Alpha\\2026")
    preview = sheet.locator(".arch-preview")
    expect(preview.locator("p")).to_be_hidden()
    preview.locator("summary").click()
    expect(preview.locator("p")).to_have_text(MAIL["preview"])
    options.nth(1).click()
    expect(options.nth(1)).to_have_attribute("aria-checked", "true")
    expect(preview.locator("p")).to_be_visible()
    saved = sheet.locator(".row-value.mono")
    expect(saved).to_have_text("031 - Kickoff agenda.msg")
    sheet.locator('[data-date="on"]').click()
    assert sheet.locator('[data-date="on"]').bounding_box()["width"] >= 44
    expect(sheet.locator('[data-date="on"]')).to_have_attribute("aria-pressed", "true")
    expect(saved).to_have_text("2026-11-05 - 031 - Kickoff agenda.msg")
    sheet.locator(".ao-open").nth(1).click()
    expect(page.locator(".toast")).to_contain_text("Opened in File Explorer")
    assert opened == [{"root": "invented-root", "scan_id": 7, "kind": "folder", "id": 22, "verb": "explore"}]
    # The clicks scrolled the sheet; the lint reads it from the top, as it opens.
    sheet.evaluate("d => { [d, ...d.querySelectorAll('*')].forEach((e) => { e.scrollTop = 0; }); window.scrollTo(0, 0); }")
    _lint_both_themes(page, allow=BELOW_FOLD)
    sheet.locator("#archSearch").fill("northwind")
    sheet.locator("#archFound button").click()
    expect(options.first.locator(".chip")).to_have_text("Chosen")
    sheet.locator("#archiveGo").click()
    expect(sheet).to_be_hidden()
    assert filed == [{"token": "invented-token", "folder": ROOT + "/Clients/Northwind/2026", "category": "Archived", "date_prefix": "on"}]
    expect(page.locator(".toast")).to_contain_text("Archived to Northwind\\2026")
    expect(page.locator("#outlookMeta")).to_have_text("Last filed to Northwind\\2026")
    page.locator("#paneSearch .home-settings").click()
    page.locator("#showMacro").click()
    macro = page.locator("#macroDialog")
    expect(macro).to_be_visible()
    expect(macro.locator("#setxText")).to_contain_text("setx FOLDER_EXPLORER_HOME")
    expect(macro.locator("#homeState")).not_to_be_empty()
    expect(macro.locator(".setup-step-title")).to_have_text(["Set the folder", "Import the macro", "Add it to the ribbon", "Restart Outlook"])
    expect(macro.locator("#macroText")).to_contain_text("Sub ArchiveCurrentItem()")
    # The vendored code-listing: Copy is an unpainted icon button that puts the listing on the clipboard and says so.
    if browser_name == "chromium":
        page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    macro.locator("#copyMacro").click()
    expect(macro.locator(".code-listing-status").nth(1)).to_have_text("Copied")
    if browser_name == "chromium":
        assert "Sub ArchiveCurrentItem()" in page.evaluate("() => navigator.clipboard.readText()")
    # The tap scrolled the sheet to the Copy; the lint below reads the sheet from its top.
    macro.evaluate("d => { [d, ...d.querySelectorAll('*')].forEach((e) => { e.scrollTop = 0; }); window.scrollTo(0, 0); }")
    # The sheet is capped to the viewport and scrolls its card, so the VBA's Copy sits below a phone's fold by design.
    _lint_both_themes(page, allow={"#macroDialog #copyMacro": "below the fold of the scrolling sheet"})
    assert errors == []


def test_an_archived_mail_is_filed_again_only_through_the_copy_switch(page: Page, app_url: str) -> None:
    errors = _errors(page)
    filed: list[dict[str, Any]] = []
    _archive_ready(page)
    page.route("**/api/archive/pending", lambda r: _json(r, {"mail": None}))
    page.route("**/api/archive/current", lambda r: _json(r, dict(PREPARED, already_archived=ROOT + "/Clients/Project Alpha/2026/003 - Kickoff agenda.msg")))

    def file_mail(route: Route) -> None:
        filed.append(route.request.post_data_json)
        _json(route, {"ok": True, "folder_path": filed[-1]["folder"], "files": [filed[-1]["folder"] + "/004 - Kickoff agenda.msg"],
                      "categorized": True, "indexing": True, "error": None})
    page.route("**/api/archive/file", file_mail)

    page.goto(app_url + "/")
    page.locator("#openArchive").click()
    sheet = page.locator("#archiveDialog")
    expect(sheet.locator(".arch-filed")).to_have_text("Already archived as Clients\\Project Alpha\\2026\\003 - Kickoff agenda.msg")
    go, copy = sheet.locator("#archiveGo"), sheet.locator("#archCopy")
    expect(copy).to_have_attribute("aria-checked", "false")
    expect(go).to_be_disabled()
    _lint_both_themes(page, allow=BELOW_FOLD)
    copy.click()
    expect(copy).to_have_attribute("aria-checked", "true")
    expect(go).to_have_text("Archive a copy to Project Alpha\\2026")
    _lint_both_themes(page, allow=BELOW_FOLD)
    go.click()
    expect(sheet).to_be_hidden()
    assert filed == [{"token": "invented-token", "folder": ROOT + "/Clients/Project Alpha/2026", "category": "Archived",
                      "date_prefix": "auto", "duplicate": True}]
    assert errors == []


def test_the_macro_hand_off_opens_the_sheet_and_a_failure_says_why(page: Page, app_url: str, browser_name: str) -> None:
    errors = _errors(page)
    handed = [{"mail": dict(PREPARED, from_macro=True)}]
    _archive_ready(page)
    page.route("**/api/archive/pending", lambda r: _json(r, handed.pop() if handed else {"mail": None}))
    page.route("**/api/archive/current", lambda r: _json(r, {"ok": False, "error": {
        "code": "outlook_unreachable", "message": "Outlook isn't reachable: open Outlook (classic) and try again."}}))
    page.route("**/api/emails/*/find", lambda r: _json(r, {"ok": True, "folder": "Archive", "mailbox": "default"}))
    page.goto(app_url + "/")
    sheet = page.locator("#archiveDialog")
    expect(sheet.locator(".arch-source")).to_have_text("Opened from the Archive button in Outlook")
    expect(sheet.locator(".arch-mail strong")).to_have_text("Kickoff agenda")
    sheet.locator("[data-close]").click()
    page.locator("#openArchive").click()
    expect(sheet.locator(".pv-error")).to_contain_text("Outlook isn't reachable")
    expect(sheet.locator("#archRetry")).to_be_visible()
    _lint_both_themes(page)
    sheet.locator("[data-close]").click()
    page.locator("#q").fill("budget")
    page.locator("#results .result-row").first.click()
    preview = page.locator("#previewSheetBody" if browser_name == "webkit" else "#previewDock")
    preview.locator('[data-act="mailbox"]').click()
    expect(page.locator(".toast")).to_contain_text("Opened in Outlook from Archive")
    assert errors == []
