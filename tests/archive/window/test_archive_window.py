"""The fallback Archive window: its model, its theme and the Tk window over an invented mail (no Outlook, no real mail)."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from typing import Any, Iterator

import pytest

from app import outlook
from src.archive_window import theme
from src.archive_window.model import (
    NO_NAME,
    WEAK_SCORE,
    ModelView,
    last_two,
    mail_date,
    mail_line,
    openable_folder,
    under_root,
)
from src.archive_window.view import ArchiveView

SAVED = {"auto": "001 - Plan.msg", "on": "2026-11-05 - 001 - Plan.msg", "off": "001 - Plan.msg"}


def _doc(root: Path, **over: Any) -> dict[str, Any]:
    for name in ("Clients/Northwind", "Internal"):
        (root / name).mkdir(parents=True, exist_ok=True)
    doc = {"ok": True, "category": "Filed", "roots": [str(root)], "already_archived": None,
           "mail": {"message_id": "m1", "subject": "Plan", "sender": "leo@example.invalid", "date_sent": "2026-11-05T09:12:00",
                    "attachment_count": 2, "where": "Inbox", "preview": "Hello team"},
           "suggestions": [
               {"folder_path": str(root / "Clients" / "Northwind"), "display_name": "x", "why": [{"label": "Same subject", "tone": "accent"}],
                "note": "This conversation is filed here", "score": 0.82, "similar": 6, "example": "Plan v2", "saved_as": SAVED},
               {"folder_path": str(root / "Internal"), "display_name": "y", "why": [{"label": "Topic", "tone": ""}], "note": "",
                "score": 0.31, "similar": 1, "example": "", "saved_as": SAVED}]}
    doc.update(over)
    return doc


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    return tmp_path / "Mail"


# ------------------------------------------------------------------ model

def test_a_suggestion_carries_its_percent_rank_tone_and_similar_line(root: Path) -> None:
    first, second = ModelView(_doc(root)).options()
    assert (first.rank, first.percent, first.weak) == (1, 82, False)
    assert (second.rank, second.percent, second.weak) == (2, 31, True) and 0.31 < WEAK_SCORE
    assert first.chips == (("Same subject", "accent"),) and first.note == "This conversation is filed here"
    assert first.similar == "6 similar emails · e.g. “Plan v2”" and second.similar == "1 similar email"
    assert first.shown == "Clients\\Northwind"


def test_a_picked_folder_leads_unranked_and_the_suggestions_keep_their_rank(root: Path) -> None:
    view = ModelView(_doc(root))
    view.pick_extra(str(root / "Internal"))
    picked, first, _ = view.options()
    assert (picked.rank, picked.percent, picked.chips) == (None, None, (("Chosen", ""),)) and view.choice == 0
    assert first.rank == 1 and view.go_label() == "Archive to Mail\\Internal"
    assert view.saved_as_text() == NO_NAME


def test_saved_as_follows_the_date_prefix_choice(root: Path) -> None:
    view = ModelView(_doc(root))
    assert view.saved_as_text() == "001 - Plan.msg"
    view.date_prefix = "on"
    assert view.saved_as_text() == "2026-11-05 - 001 - Plan.msg"


def test_an_archived_mail_needs_the_copy_choice_and_files_as_a_duplicate(root: Path) -> None:
    view = ModelView(_doc(root, already_archived=str(root / "Internal" / "001 - Plan.msg")))
    assert not view.can_archive() and view.request() is None and view.go_label().startswith("Archive a copy to ")
    view.copy = True
    assert view.request().duplicate is True  # type: ignore[union-attr]


def test_the_request_carries_folder_category_and_date_prefix(root: Path) -> None:
    view = ModelView(_doc(root))
    view.choice, view.date_prefix = 1, "off"
    req = view.request()
    assert (req.folder, req.category, req.duplicate, req.date_prefix) == (str(root / "Internal"), "Filed", False, "off")  # type: ignore[union-attr]
    view.category = ""
    assert view.request().category is None  # type: ignore[union-attr]


def test_no_suggestion_leaves_nothing_to_archive(root: Path) -> None:
    view = ModelView(_doc(root, suggestions=[]))
    assert view.options() == [] and view.request() is None and view.go_label() == "Pick a folder"


def test_each_filing_result_has_its_own_words(root: Path) -> None:
    view = ModelView(_doc(root))
    done = view.outcome({"ok": True, "files": ["a"], "folder_path": "/m/Clients/Northwind", "indexing": True})
    assert (done.filed, done.ok, done.text) == (True, True, "Archived to Clients\\Northwind. Indexing the folder now.")
    untagged = view.outcome({"ok": False, "files": ["a"], "folder_path": "/m/Internal", "error": {"message": "no tag"}})
    assert (untagged.filed, untagged.ok, untagged.text) == (True, False, "Filed to m\\Internal, but not tagged: no tag")
    refused = view.outcome({"ok": False, "error": {"message": "outside"}})
    assert (refused.filed, refused.text) == (False, "outside")


def test_paths_and_dates_read_like_the_page_writes_them() -> None:
    assert under_root("C:/Mail/Clients/Alpha", ["c:\\mail"]) == "Clients\\Alpha"
    assert under_root("D:/Else/Alpha", ["C:\\Mail"]) == "D:\\Else\\Alpha"
    assert last_two("C:/Mail/Clients/Alpha/") == "Clients\\Alpha"
    assert mail_date("2026-11-05T09:12:00") == "5 Nov 2026, 09:12" and mail_date("2026-11-05T09:12:00", False) == "5 Nov 2026"
    assert mail_date("") == "" and mail_date("garbage-text") == "garbage-text"
    assert mail_line({"sender": "", "date_sent": "", "attachment_count": 1}) == "(no sender) · 1 attachment"


def test_open_is_offered_only_for_a_folder_inside_the_email_folders(root: Path, tmp_path: Path) -> None:
    (root / "Here").mkdir(parents=True)
    outside = tmp_path / "Elsewhere"
    outside.mkdir()
    assert openable_folder(str(root / "Here"), [str(root)]) == str((root / "Here").resolve())
    assert openable_folder(str(root / "Missing"), [str(root)]) is None
    assert openable_folder(str(outside), [str(root)]) is None
    assert openable_folder(str(root / ".." / "Elsewhere"), [str(root)]) is None


# ------------------------------------------------------------------ theme

def test_the_window_uses_the_apps_white_and_blue_tokens_in_both_themes() -> None:
    light, dark = theme.palette(False), theme.palette(True)
    assert (light.bg, light.accent, light.accent_fill, light.accent_fg) == ("#ffffff", "#0969da", "#0969da", "#ffffff")
    assert (dark.bg, dark.card, dark.accent, dark.accent_fill) == ("#0d1117", "#161b22", "#2f81f7", "#1f6feb")
    assert dark.line_muted == "#21262d" and dark.attention_text == "#e3b341" and light.attention_text == "#7d4e00"


def test_the_dark_block_overrides_the_light_one_and_vars_resolve() -> None:
    css = ':root {\n  --a: #111111;\n  --b: var(--a);\n  --only-light: #222222;\n}\n[data-theme="dark"] {\n  --a: #999999;\n}\n'
    assert theme.read_tokens(css, False) == {"a": "#111111", "b": "#111111", "only-light": "#222222"}
    assert theme.read_tokens(css, True) == {"a": "#999999", "b": "#999999", "only-light": "#222222"}


def test_a_soft_tint_is_the_colour_over_the_surface() -> None:
    assert theme.blend("#0969da", "#ffffff", 16) == "#d8e7f9" and theme.blend("#000000", "#ffffff", 0) == "#ffffff"


def test_an_unreadable_windows_theme_opens_light(monkeypatch: pytest.MonkeyPatch) -> None:
    import winreg

    def refuse(*_a: object) -> None:
        raise OSError("no such key")

    monkeypatch.setattr(winreg, "OpenKey", refuse)
    assert theme.windows_dark() is False


# ------------------------------------------------------------------ the window

@pytest.fixture(scope="module")
def tk_root() -> Iterator[tk.Tk]:
    """The one Tk root the window tests share: Tk can't reliably be started again after each test, and the app opens one."""
    try:
        shared = tk.Tk()
    except tk.TclError as exc:
        pytest.skip("Tk can't open a window here: %s" % exc)
    shared.withdraw()
    yield shared
    shared.destroy()


def _build(view: ArchiveView) -> ArchiveView:
    """Build ``view`` on the shared root with closing disabled, so a pending close can't take the root down."""
    view.close = lambda: None  # type: ignore[method-assign]
    view.build()
    view.root.update()
    return view


@pytest.fixture()
def window(root: Path, tk_root: tk.Tk, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """A built window over the invented document, its launches and its search replaced."""
    opened: list[str] = []
    monkeypatch.setattr("src.archive_window.view.desktop.open_in_explorer", opened.append)
    filed: list[tuple[Any, ...]] = []
    calls: dict[str, Any] = {"opened": opened, "filed": filed}

    def on_file(*args: Any) -> dict[str, Any]:
        filed.append(args)
        return {"ok": True, "files": ["a"], "folder_path": args[0]}

    hits = [{"path": str(root / "Internal"), "name": "Internal", "location": None}]
    view = _build(ArchiveView(_doc(root), on_file, lambda q: hits if q == "int" else [], theme.palette(False), tk_root))
    calls["view"] = view
    yield calls
    view.frame.destroy()


def _texts(widget: tk.Misc) -> list[str]:
    found = [str(widget.cget("text"))] if "text" in widget.keys() else []
    for child in widget.winfo_children():
        found.extend(_texts(child))
    return found


def test_the_window_shows_what_the_sheet_shows(window: dict[str, Any]) -> None:
    shown = "\n".join(_texts(window["view"].root))
    for expected in ("Plan", "leo@example.invalid · 5 Nov 2026, 09:12 · 2 attachments · Inbox", "Preview", "Suggested folders", "82%", "31%",
                     "Clients\\Northwind", "Same subject", "This conversation is filed here", "6 similar emails · e.g. “Plan v2”",
                     "Browse…", "Outlook category", "Filed", "No category", "Date prefix", "Saved as", "001 - Plan.msg",
                     "Archive to Clients\\Northwind", "Cancel"):
        assert expected in shown, expected


def test_choosing_a_folder_and_a_date_prefix_files_with_both(window: dict[str, Any], root: Path) -> None:
    view = window["view"]
    view._choose(1)
    view._set_date("on")
    view.category.choose("")
    view._archive()
    assert window["filed"] == [(str(root / "Internal"), None, False, "on")]
    assert view.status.cget("text") == "Archived to Mail\\Internal"


def test_a_failed_filing_keeps_the_window_and_the_choice(window: dict[str, Any], root: Path) -> None:
    view = window["view"]
    view.on_file = lambda *_a: {"ok": False, "error": {"message": "Outlook isn't reachable"}}
    view._archive()
    assert view.status.cget("text") == "Outlook isn't reachable" and str(view.go.cget("state")) == "normal"


def test_open_asks_windows_for_the_folder_and_never_a_path_outside(window: dict[str, Any], root: Path) -> None:
    view = window["view"]
    view._open(view.model.options()[0])
    assert window["opened"] == [str((root / "Clients" / "Northwind").resolve())]
    view._open(view.model.options()[0].__class__(**{**view.model.options()[0].__dict__, "open_target": None}))
    assert len(window["opened"]) == 1


def test_typing_searches_and_a_hit_leads_the_list(window: dict[str, Any], root: Path) -> None:
    view = window["view"]
    view._clear_placeholder(None)
    view.query.set("int")
    view._search()
    hits = view.found.winfo_children()
    assert [b.cget("text") for b in hits] == ["Internal"]
    hits[0].invoke()
    assert view.model.extra == str(root / "Internal") and view.model.options()[0].chips == (("Chosen", ""),)
    assert not view.found.winfo_children()


def test_an_unreadable_mail_shows_its_error_and_only_closes(tk_root: tk.Tk) -> None:
    view = _build(ArchiveView({"ok": False, "error": {"code": "mail_gone", "message": "That mail is no longer in Outlook"}},
                              lambda *_a: {}, None, theme.palette(True), tk_root))
    assert "That mail is no longer in Outlook" in _texts(view.root) and "Archive" not in _texts(view.root)
    view.frame.destroy()


def test_the_entry_hands_the_window_a_search_and_a_filer_that_takes_every_option(monkeypatch: pytest.MonkeyPatch) -> None:
    got: dict[str, Any] = {}
    monkeypatch.setattr(outlook, "hand_to_page", lambda doc, cfg: False)
    monkeypatch.setattr(outlook, "prepare", lambda cfg, e, s: {"ok": True})
    monkeypatch.setattr(outlook, "file_one", lambda cfg, e, s, folder, cat, dup, dp: got.update(args=(folder, cat, dup, dp)) or {"ok": True})
    monkeypatch.setattr(outlook, "_apartment", __import__("contextlib").nullcontext)
    monkeypatch.setattr(outlook, "_guarded", lambda work: work())
    monkeypatch.setattr("src.archive_window.show_window", lambda doc, on_file, find: on_file("F", "Filed", True, "off"))
    outlook.archive({"c": 1}, "e", "s")
    assert got["args"] == ("F", "Filed", True, "off")
