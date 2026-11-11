"""The Tk widgets of the fallback Archive window: one painting of ``ModelView``, in the app's tokens.

Filing goes through the caller's ``on_file``, which runs in this same process and COM
apartment. A failure is shown in its own words and the window stays open, so the user
can pick another folder or close it.
"""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from tkinter import filedialog
from typing import Any, Callable, Optional

from src import desktop
from src.archive_window.finder import OUTSIDE, Finder, FinderSource, Found
from src.archive_window.model import (
    DATE_CHOICES,
    EXPLORER_CHIP,
    NO_CATEGORY,
    RANK_NOTE,
    ModelView,
    Option,
    explorer_note,
    mail_line,
    under_root,
)
from src.archive_window.theme import (
    FONT,
    MONO,
    SIZE_BODY,
    SIZE_CAPTION,
    SIZE_SMALL,
    SIZE_TITLE,
    SPACE_MD,
    SPACE_SM,
    Palette,
)
from src.archive_window.work_area import Rect
from src.archive_window.work_area import work_area as monitor_work_area

logger = logging.getLogger(__name__)

# How long typing in "Find a folder" pauses before it searches.
DEBOUNCE_MS = 200
WIDTH = 640
_WRAP = WIDTH - 2 * SPACE_MD - 24
PLACEHOLDER = "Find a folder"
EXPLORER_BUTTON = "Current Explorer folder"
FOUND_CHIP, FOUND_NOTE = "Found", "found by the folder search"
ARCHIVE_HERE, OPEN_HERE, STEP_IN, BACK = "Archive here", "Open in Explorer", "Subfolders ›", "← Back"

# How often Tk's thread looks for the worker's ranking; short, as the ranking itself takes tens of ms.
RANK_POLL_MS = 15
RANK_FAILED = "Couldn't rank the folders. Find one below."

# What the window manager adds around the window (title bar and borders), which a work-area fit has to leave room for.
CHROME = 56
# The least the found folders' list shows when the screen is short: two rows.
LIST_MIN = 100
# Pixels one wheel notch moves the found folders' list (the canvas scrolls in single pixels, so a row can be shown whole).
WHEEL_STEP = 24

OnFile = Callable[[str, Optional[str], bool, str], dict[str, Any]]
Rank = Callable[[], list[dict[str, Any]]]
CurrentExplorer = Callable[[], dict[str, Any]]


def _font(size: int = SIZE_BODY, bold: bool = False, mono: bool = False) -> tuple[str, int, str]:
    return (MONO if mono else FONT, size, "bold" if bold else "normal")


class Segmented(tk.Frame):
    """A row of mutually exclusive choices, the window's version of the page's segmented control."""

    def __init__(self, parent: tk.Misc, pal: Palette, items: list[tuple[str, str]], value: str, command: Callable[[str], None]) -> None:
        super().__init__(parent, bg=pal.line, padx=1, pady=1)
        self.pal, self.command, self.labels = pal, command, {}
        for key, text in items:
            label = tk.Label(self, text=text, font=_font(SIZE_SMALL), padx=SPACE_MD - 2, pady=4, cursor="hand2")
            label.pack(side="left", padx=(0, 1))
            label.bind("<Button-1>", lambda _e, k=key: self.choose(k))
            self.labels[key] = label
        self.paint(value)

    def paint(self, value: str) -> None:
        for key, label in self.labels.items():
            on = key == value
            label.configure(bg=self.pal.accent_fill if on else self.pal.card_off, fg=self.pal.accent_fg if on else self.pal.ink)

    def choose(self, key: str) -> None:
        self.paint(key)
        self.command(key)


class ArchiveView:
    def __init__(self, doc: dict[str, Any], on_file: OnFile, folders: Optional[FinderSource], pal: Palette,
                 root: Optional[tk.Tk] = None, current_explorer: Optional[CurrentExplorer] = None,
                 work_area: Optional[Callable[[], Rect]] = None) -> None:
        self.doc, self.on_file, self.pal = doc, on_file, pal
        self.current_explorer = current_explorer
        self.model = ModelView(doc) if doc.get("ok") else None
        self.finder = Finder(folders, self.model.roots) if folders is not None and self.model is not None else None
        # A caller that opens many windows in one process (the tests) passes the one root it owns.
        self.root = root or tk.Tk()
        self.work_area = work_area or (lambda: monitor_work_area(self.root))
        self.root.title("Archive mail")
        self.root.configure(bg=pal.card)
        self.root.attributes("-topmost", True)
        self.root.minsize(WIDTH, 0)
        self.frame = tk.Frame(self.root, bg=pal.card, padx=SPACE_MD, pady=SPACE_MD)
        self.frame.pack(fill="both", expand=True)
        self.built = False
        # Set while the window is held to a height smaller than its content; the found folders' list then scrolls.
        self.clamped = False
        self.list_canvas: Optional[tk.Canvas] = None
        self.list_top = 0
        self.preview_open = False
        self.search_job: Optional[str] = None
        self.busy = False

    def close(self) -> None:
        self.root.destroy()

    # ------------------------------------------------------------ pieces

    def _label(self, parent: tk.Misc, text: str, size: int = SIZE_BODY, bold: bool = False, fg: Optional[str] = None,
               mono: bool = False, **pack: Any) -> tk.Label:
        label = tk.Label(parent, text=text, bg=parent.cget("bg"), fg=fg or self.pal.ink, font=_font(size, bold, mono),
                         wraplength=_WRAP, justify="left", anchor="w")
        label.pack(**{"fill": "x", **pack})
        return label

    def _button(self, parent: tk.Misc, text: str, command: Callable[[], None], primary: bool = False) -> tk.Button:
        pal = self.pal
        return tk.Button(parent, text=text, command=command, font=_font(SIZE_BODY, bold=primary), relief="flat", cursor="hand2",
                         bg=pal.accent_fill if primary else pal.card_off, fg=pal.accent_fg if primary else pal.ink,
                         activebackground=pal.accent if primary else pal.neutral_soft, activeforeground=pal.accent_fg if primary else pal.ink,
                         disabledforeground=pal.muted, padx=SPACE_MD, pady=SPACE_SM, bd=0, highlightthickness=0 if primary else 1,
                         highlightbackground=pal.line)

    def _chip(self, parent: tk.Misc, text: str, tone: str = "", weak: bool = False) -> tk.Label:
        pal = self.pal
        bg, fg = (pal.attention_soft, pal.attention_text) if weak else (pal.accent_soft, pal.accent_text) if tone == "accent" else (pal.neutral_soft, pal.ink)
        return tk.Label(parent, text=text, bg=bg, fg=fg, font=_font(SIZE_CAPTION, bold=True), padx=SPACE_SM, pady=1)

    def _bar(self, parent: tk.Misc, percent: int) -> tk.Canvas:
        bar = tk.Canvas(parent, width=40, height=6, bg=self.pal.neutral_soft, highlightthickness=0)
        bar.create_rectangle(0, 0, round(40 * percent / 100), 6, fill=self.pal.accent, width=0)
        return bar

    def _row(self, label: str) -> tk.Frame:
        row = tk.Frame(self.frame, bg=self.pal.card)
        row.pack(fill="x", pady=(SPACE_SM, 0))
        self._label(row, label, side="left", fill="none")
        return row

    # ------------------------------------------------------------ build

    def build(self) -> None:
        if self.model is None:
            self._label(self.frame, "Couldn't archive this mail", SIZE_TITLE, True, pady=(0, 4))
            self._label(self.frame, self.doc["error"]["message"], fg=self.pal.muted)
            self._button(self.frame, "Close", self.close).pack(anchor="e", pady=(SPACE_MD, 0))
            return
        # The footer is packed first, so a short screen cuts the content above it and never the buttons.
        self._build_footer()
        self._build_mail()
        if self.model.filed:
            self._build_filed()
        self._build_options()
        self._build_other()
        self._build_settings()
        self._paint_summary()
        self.built = True
        self._fit()

    def _build_footer(self) -> None:
        """What stays on screen whatever the list above holds: the status line, the found folder's actions and the main buttons."""
        footer = tk.Frame(self.frame, bg=self.pal.card)
        footer.pack(side="bottom", fill="x")
        self.status = tk.Label(footer, text="", bg=self.pal.card, fg=self.pal.danger_text, font=_font(SIZE_SMALL), wraplength=_WRAP,
                               justify="left", anchor="w")
        self.status.pack(fill="x", pady=(SPACE_SM, 0))
        self.actions = tk.Frame(footer, bg=self.pal.card)
        self.actions.pack(fill="x")
        buttons = tk.Frame(footer, bg=self.pal.card)
        buttons.pack(fill="x", pady=(SPACE_SM, 0))
        self._button(buttons, "Cancel", self.close).pack(side="right")
        self.go = self._button(buttons, "", self._archive, primary=True)
        self.go.pack(side="right", padx=(0, SPACE_SM))

    def _build_mail(self) -> None:
        pal, mail = self.pal, self.doc["mail"]
        card = tk.Frame(self.frame, bg=pal.card, highlightthickness=1, highlightbackground=pal.line)
        card.pack(fill="x")
        head = tk.Frame(card, bg=pal.card, padx=14, pady=SPACE_SM + 4)
        head.pack(fill="x")
        self._label(head, mail.get("subject") or "(no subject)", SIZE_TITLE, True, pady=(0, 2))
        self._label(head, mail_line(mail), SIZE_SMALL, fg=pal.muted)
        toggle = tk.Frame(card, bg=pal.card, padx=14, pady=SPACE_SM, cursor="hand2", highlightthickness=1, highlightbackground=pal.line_muted)
        toggle.pack(fill="x")
        self.preview_chevron = self._label(toggle, "▸", SIZE_SMALL, fg=pal.muted, side="right", fill="none")
        self._label(toggle, "Preview", SIZE_BODY, True, side="left", fill="none")
        self.preview = tk.Label(card, text=mail.get("preview") or "(no readable text)", bg=pal.card, fg=pal.ink, font=_font(SIZE_SMALL),
                                wraplength=_WRAP - 12, justify="left", anchor="w", padx=14, pady=SPACE_SM)
        for widget in (toggle, *toggle.winfo_children()):
            widget.bind("<Button-1>", lambda _e: self._toggle_preview())

    def _toggle_preview(self) -> None:
        self.preview_open = not self.preview_open
        self.preview_chevron.configure(text="▾" if self.preview_open else "▸")
        if self.preview_open:
            self.preview.pack(fill="x")
        else:
            self.preview.pack_forget()
        self._fit()

    def _build_filed(self) -> None:
        assert self.model is not None
        text = "Already archived as " + under_root(self.model.filed or "", self.model.roots)
        self._label(self.frame, text, SIZE_SMALL, fg=self.pal.attention_text, pady=(SPACE_SM, 0))
        row = self._row("Archive a copy anyway")
        Segmented(row, self.pal, [("off", "Off"), ("on", "On")], "off", self._set_copy).pack(side="right")

    def _build_options(self) -> None:
        head = tk.Frame(self.frame, bg=self.pal.card)
        head.pack(fill="x", pady=(SPACE_MD, 0))
        self._label(head, "Suggested folders", SIZE_BODY, True, side="left", fill="none")
        info = tk.Label(head, text="ⓘ", bg=self.pal.card, fg=self.pal.accent_text, font=_font(SIZE_BODY), cursor="hand2")
        info.pack(side="right")
        info.bind("<Button-1>", lambda _e: self._toggle_note())
        self.note = tk.Label(self.frame, text=RANK_NOTE, bg=self.pal.card, fg=self.pal.muted, font=_font(SIZE_SMALL), wraplength=_WRAP,
                             justify="left", anchor="w")
        self.options = tk.Frame(self.frame, bg=self.pal.card)
        self.options.pack(fill="x", pady=(SPACE_SM, 0))
        self._paint_options()

    def _toggle_note(self) -> None:
        if self.note.winfo_ismapped():
            self.note.pack_forget()
        else:
            self.note.pack(fill="x", before=self.options)
        self._fit()

    def _paint_options(self) -> None:
        assert self.model is not None
        for child in self.options.winfo_children():
            child.destroy()
        opts = self.model.options()
        if not opts:
            self._label(self.options, self.model.empty_note(), SIZE_SMALL, fg=self.pal.muted)
        for i, option in enumerate(opts):
            self._option_row(i, option, i == self.model.choice)
        self._fit()

    def _option_row(self, i: int, o: Option, on: bool) -> None:
        pal = self.pal
        bg = pal.accent_soft if on else pal.card
        row = tk.Frame(self.options, bg=bg, padx=SPACE_SM, pady=SPACE_SM, cursor="hand2", highlightthickness=1,
                       highlightbackground=pal.accent if on else pal.line_muted)
        row.pack(fill="x", pady=(0, 4))
        tk.Label(row, text="◉" if on else "○", bg=bg, fg=pal.accent_text if on else pal.muted, font=_font(SIZE_TITLE), width=2).pack(side="left", anchor="n", padx=(0, SPACE_SM))
        open_btn = tk.Button(row, text="Open", font=_font(SIZE_SMALL), relief="flat", bd=0, bg=bg, fg=pal.accent_text, cursor="hand2",
                             activebackground=pal.neutral_soft, activeforeground=pal.accent_text, disabledforeground=pal.muted,
                             command=lambda: self._open(o), state="normal" if o.open_target else "disabled")
        open_btn.pack(side="right", anchor="n")
        body = tk.Frame(row, bg=bg)
        body.pack(side="left", fill="x", expand=True)
        top = tk.Frame(body, bg=bg)
        top.pack(fill="x")
        if o.percent is not None:
            tk.Label(top, text=str(o.rank), bg=bg, fg=pal.muted, font=_font(SIZE_SMALL, True)).pack(side="left", padx=(0, SPACE_SM))
            self._chip(top, "%d%%" % o.percent, weak=o.weak).pack(side="left")
            self._bar(top, o.percent).pack(side="left", padx=SPACE_SM)
        tk.Label(top, text=o.shown, bg=bg, fg=pal.ink, font=_font(SIZE_SMALL, True, mono=True), wraplength=_WRAP - 150, justify="left",
                 anchor="w").pack(side="left", fill="x")
        why = tk.Frame(body, bg=bg)
        why.pack(fill="x", pady=(2, 0))
        for label, tone in o.chips:
            self._chip(why, label, tone).pack(side="left", padx=(0, 6))
        if o.note:
            tk.Label(why, text=o.note, bg=bg, fg=pal.muted, font=_font(SIZE_SMALL)).pack(side="left")
        if o.similar:
            tk.Label(body, text=o.similar, bg=bg, fg=pal.muted, font=_font(SIZE_SMALL), wraplength=_WRAP - 80, justify="left", anchor="w").pack(fill="x")
        for widget in self._descendants(row):
            if widget is not open_btn:
                widget.bind("<Button-1>", lambda _e, k=i: self._choose(k))

    def _descendants(self, widget: tk.Misc) -> list[tk.Misc]:
        found = [widget]
        for child in widget.winfo_children():
            found.extend(self._descendants(child))
        return found

    def _build_other(self) -> None:
        pal = self.pal
        row = tk.Frame(self.frame, bg=pal.card)
        row.pack(fill="x", pady=(SPACE_SM, 0))
        self._button(row, "Browse…", self._pick).pack(side="right", padx=(SPACE_SM, 0))
        if self.current_explorer is not None:
            self._button(row, EXPLORER_BUTTON, self._use_explorer).pack(side="right", padx=(SPACE_SM, 0))
        self.placeholder = True
        self.query = tk.StringVar(value=PLACEHOLDER)
        self.entry = tk.Entry(row, textvariable=self.query, font=_font(SIZE_BODY), relief="flat", bg=pal.card_off, fg=pal.muted,
                              insertbackground=pal.ink, highlightthickness=1, highlightbackground=pal.line, highlightcolor=pal.accent)
        self.entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.entry.bind("<FocusIn>", self._clear_placeholder)
        self.entry.bind("<FocusOut>", self._restore_placeholder)
        for key, act in (("<Down>", lambda: self._move(1)), ("<Up>", lambda: self._move(-1)), ("<Return>", self._archive_here)):
            self.entry.bind(key, lambda _e, a=act: (a(), "break")[1])
        self.entry.bind("<Right>", self._key_right)
        self.entry.bind("<Left>", self._key_back)
        self.entry.bind("<BackSpace>", self._key_back)
        self.query.trace_add("write", lambda *_a: self._schedule_search())
        self.found = tk.Frame(self.frame, bg=pal.card)
        self.found.pack(fill="x")

    def _clear_placeholder(self, _e: object) -> None:
        if self.placeholder:
            self.placeholder = False
            self.query.set("")
        self.entry.configure(fg=self.pal.ink)

    def _restore_placeholder(self, _e: object) -> None:
        if not self.query.get():
            self.placeholder = True
            self.entry.configure(fg=self.pal.muted)
            self.query.set(PLACEHOLDER)

    def _build_settings(self) -> None:
        assert self.model is not None
        if self.model.show_category:
            row = self._row("Outlook category")
            items = [(c, c) for c in self.model.category_choices] + [("", NO_CATEGORY)]
            self.category = Segmented(row, self.pal, items, self.model.category, self._set_category)
            self.category.pack(side="right")
        row = self._row("Date prefix")
        self.date = Segmented(row, self.pal, list(DATE_CHOICES), self.model.date_prefix, self._set_date)
        self.date.pack(side="right")
        row = self._row("Saved as")
        self.saved_as = tk.Label(row, text="", bg=self.pal.card, fg=self.pal.ink, font=_font(SIZE_SMALL, mono=True), wraplength=_WRAP - 110,
                                 justify="right", anchor="e")
        self.saved_as.pack(side="right")

    # ------------------------------------------------------------ state changes

    def _paint_summary(self) -> None:
        assert self.model is not None
        self.saved_as.configure(text=self.model.saved_as_text())
        self.go.configure(text="Archiving…" if self.busy else self.model.go_label(),
                          state="normal" if self.model.can_archive() and not self.busy else "disabled")

    def _choose(self, index: int) -> None:
        assert self.model is not None
        self.model.choice = index
        self._paint_options()
        self._paint_summary()

    def _set_copy(self, key: str) -> None:
        assert self.model is not None
        self.model.copy = key == "on"
        self._paint_summary()

    def _set_category(self, key: str) -> None:
        assert self.model is not None
        self.model.category = key

    def _set_date(self, key: str) -> None:
        assert self.model is not None
        self.model.date_prefix = key
        self._paint_summary()

    def _open(self, option: Option) -> None:
        if option.open_target:
            self._show_in_explorer(option.open_target)

    def _show_in_explorer(self, folder: str) -> None:
        try:
            desktop.open_in_explorer(folder)
            self._say("Opened in File Explorer", self.pal.muted)
        except (OSError, FileNotFoundError) as exc:
            self._say(str(exc) or "File Explorer couldn't open the folder", self.pal.danger_text)

    def _say(self, text: str, color: str) -> None:
        self.status.configure(text=text, fg=color)
        self._fit()

    # ------------------------------------------------------------ ranking

    def fill(self, suggestions: list[dict[str, Any]]) -> None:
        """Offer the ranked folders in place of the "Finding folders" line."""
        assert self.model is not None
        self.model.fill(suggestions)
        self._paint_options()
        self._paint_summary()

    def fill_in_background(self, rank: Rank, progress: Optional[Callable[[str], None]] = None) -> None:
        """Rank on a worker thread, so the window is already up, and fill the folders in on Tk's thread."""
        answer: queue.Queue[tuple[bool, list[dict[str, Any]]]] = queue.Queue(maxsize=1)

        def work() -> None:
            try:
                answer.put((True, rank()))
            except Exception:
                logger.exception("❌ archive: ranking the folders failed")
                answer.put((False, []))

        def poll() -> None:
            try:
                ok, suggestions = answer.get_nowait()
            except queue.Empty:
                self.root.after(RANK_POLL_MS, poll)
                return
            self.fill(suggestions)
            if not ok:
                self._say(RANK_FAILED, self.pal.danger_text)
            if progress:
                progress("suggestions_shown")

        threading.Thread(target=work, name="archive-rank", daemon=True).start()
        self.root.after(RANK_POLL_MS, poll)

    # ------------------------------------------------------------ another folder

    def _pick(self) -> None:
        assert self.model is not None
        folder = filedialog.askdirectory(parent=self.root, initialdir=(self.model.roots or [None])[0], title="Pick an email folder")
        if folder:
            self._take(folder.replace("/", "\\"))

    def _use_explorer(self) -> None:
        """Offer the folder open in the foremost File Explorer window; the user still presses Archive."""
        assert self.model is not None and self.current_explorer is not None
        self._say("Reading File Explorer…", self.pal.muted)
        self.root.update_idletasks()
        answer = self.current_explorer()
        if not answer.get("ok"):
            self._say((answer.get("error") or {}).get("message") or "File Explorer couldn't be read", self.pal.danger_text)
            return
        self._say("", self.pal.muted)
        self._take(answer["folder_path"], EXPLORER_CHIP, explorer_note(answer["folder_path"]))

    def _take(self, path: str, chip: str = "Chosen", note: str = "picked from the folder index") -> None:
        assert self.model is not None
        self.model.pick_extra(path, chip, note)
        self._paint_options()
        self._paint_summary()

    # ------------------------------------------------------------ find a folder

    def _schedule_search(self) -> None:
        if self.search_job:
            self.root.after_cancel(self.search_job)
        self.search_job = self.root.after(DEBOUNCE_MS, self._search)

    def _search(self) -> None:
        self.search_job = None
        if self.finder is None:
            return
        text = "" if self.placeholder else self.query.get()
        if text.strip() or self.finder.query.strip():
            self.finder.search(text)
        self._paint_found()

    def _paint_found(self) -> None:
        """The search's folders, or the subfolders of the folder stepped into, with the actions for the selected one."""
        for child in (*self.found.winfo_children(), *self.actions.winfo_children()):
            child.destroy()
        self.list_canvas = None
        self._paint_found_body()
        self._fit()

    def _paint_found_body(self) -> None:
        finder, pal = self.finder, self.pal
        if finder is None or not (finder.browsing or finder.query.strip()):
            return
        if finder.browsing:
            crumb = tk.Frame(self.found, bg=pal.card)
            crumb.pack(fill="x", pady=(4, 0))
            tk.Button(crumb, text=BACK, command=self._back, font=_font(SIZE_SMALL), relief="flat", bd=0, bg=pal.card, fg=pal.accent_text,
                      cursor="hand2", activebackground=pal.neutral_soft).pack(side="left", padx=(0, SPACE_SM))
            self._label(crumb, " › ".join(finder.crumbs()), SIZE_SMALL, True, side="left", fill="x", expand=True)
        message = finder.message()
        if message:
            self._label(self.found, message, SIZE_SMALL, fg=pal.danger_text if finder.failed() else pal.muted, pady=(4, 0))
        shown = finder.shown()
        if shown:
            self._open_list()
        for i, found in enumerate(shown):
            self._found_row(i, found, i == finder.selected)
        if finder.count_line():
            self._label(self.found, finder.count_line(), SIZE_CAPTION, fg=pal.muted, pady=(2, 0))
        here = finder.current()
        if here is None:
            return
        bar = tk.Frame(self.actions, bg=pal.card)
        bar.pack(fill="x", pady=(SPACE_SM, 0))
        self.here_button = self._button(bar, ARCHIVE_HERE, self._archive_here)
        self.here_button.configure(state="normal" if here.fileable and not self.busy else "disabled")
        self.here_button.pack(side="left")
        self._button(bar, OPEN_HERE, self._explore_found).pack(side="left", padx=(SPACE_SM, 0))
        self._button(bar, STEP_IN, self._enter).pack(side="left", padx=(SPACE_SM, 0))

    def _found_row(self, i: int, found: Found, on: bool) -> None:
        pal = self.pal
        bg = pal.accent_soft if on else pal.card
        row = tk.Frame(self.list_inner, bg=bg, padx=SPACE_SM, pady=2, cursor="hand2", highlightthickness=1,
                       highlightbackground=pal.accent if on else pal.card)
        row.pack(fill="x")
        self.list_rows.append(row)
        step = tk.Button(row, text="›", font=_font(SIZE_TITLE), relief="flat", bd=0, bg=bg, fg=pal.accent_text, cursor="hand2",
                         activebackground=pal.neutral_soft, command=lambda: self._enter(i))
        step.pack(side="right")
        top = tk.Frame(row, bg=bg)
        top.pack(fill="x")
        tk.Label(top, text=found.name, bg=bg, fg=pal.ink if found.fileable else pal.muted, font=_font(SIZE_SMALL, True),
                 anchor="w").pack(side="left")
        if not found.fileable:
            tk.Label(top, text=OUTSIDE, bg=pal.neutral_soft, fg=pal.muted, font=_font(SIZE_CAPTION), padx=SPACE_SM).pack(side="left", padx=SPACE_SM)
        roots = self.model.roots if self.model else []
        tk.Label(row, text=under_root(found.row.path, roots), bg=bg, fg=pal.muted, font=_font(SIZE_CAPTION, mono=True),
                 wraplength=_WRAP - 60, justify="left", anchor="w").pack(fill="x")
        for widget in self._descendants(row):
            if widget is not step:
                widget.bind("<Button-1>", lambda _e, k=i: self._select(k))
                widget.bind("<Double-Button-1>", lambda _e, k=i: self._enter(k))
            widget.bind("<MouseWheel>", self._wheel)

    # ------------------------------------------------------------ the found folders' list and the window's size

    def _open_list(self) -> None:
        """An empty scrollable list for the found folders; ``_fit`` gives it the height the screen leaves."""
        pal = self.pal
        box = tk.Frame(self.found, bg=pal.card)
        box.pack(fill="x")
        self.list_bar = tk.Scrollbar(box, orient="vertical")
        self.list_canvas = canvas = tk.Canvas(box, width=1, height=1, bg=pal.card, highlightthickness=0, bd=0, yscrollincrement=1)
        self.list_bar.configure(command=canvas.yview)
        canvas.configure(yscrollcommand=self._on_scrolled)
        canvas.pack(side="left", fill="x", expand=True)
        self.list_inner = tk.Frame(canvas, bg=pal.card)
        self.list_rows: list[tk.Frame] = []
        item = canvas.create_window(0, 0, window=self.list_inner, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(item, width=e.width))
        canvas.bind("<MouseWheel>", self._wheel)

    def _on_scrolled(self, first: str, last: str) -> None:
        self.list_bar.set(first, last)
        if self.list_canvas is not None:
            self.list_top = round(float(first) * self._list_need())

    def _list_need(self) -> int:
        return self.list_inner.winfo_reqheight()

    def _wheel(self, event: Any) -> Optional[str]:
        """A wheel notch over the list moves it, when it holds more rows than it shows."""
        canvas = self.list_canvas
        if canvas is None or int(canvas.cget("height")) >= self._list_need():
            return None
        canvas.yview_scroll(-WHEEL_STEP if event.delta > 0 else WHEEL_STEP, "units")
        return "break"

    def _reveal(self) -> None:
        """Scroll the list the least that shows the selected row whole, and keep the offset the list had otherwise."""
        canvas, finder = self.list_canvas, self.finder
        if canvas is None or finder is None or not self.list_rows:
            return
        need, shown = self._list_need(), int(canvas.cget("height"))
        top = max(0, min(self.list_top, need - shown))
        row = min(finder.selected, len(self.list_rows) - 1)
        first = sum(r.winfo_reqheight() for r in self.list_rows[:row])
        last = first + self.list_rows[row].winfo_reqheight()
        top = min(first if first < top else max(top, last - shown), max(0, need - shown))
        self.list_top = top
        canvas.yview_moveto(top / need if need else 0)

    def _fit(self) -> None:
        """Keep the window inside the screen's work area: the list takes the height left, the window moves up if it would hang below."""
        if not self.built:
            return
        root = self.root
        if self.clamped:
            root.geometry("")
            self.clamped = False
        left, top, right, bottom = self.work_area()
        room = bottom - top - CHROME
        if self.list_canvas is not None:
            self.list_inner.update_idletasks()
            need = self._list_need()
            self.list_canvas.configure(height=need, scrollregion=(0, 0, 1, need))
            root.update_idletasks()
            over = root.winfo_reqheight() - room
            if over > 0:
                self.list_canvas.configure(height=max(LIST_MIN, need - over))
                root.update_idletasks()
            if int(self.list_canvas.cget("height")) < need:
                self.list_bar.pack(side="right", fill="y", before=self.list_canvas)
            else:
                self.list_bar.pack_forget()
            root.update_idletasks()
        width, height = max(root.winfo_reqwidth(), WIDTH), min(root.winfo_reqheight(), room)
        shown = bool(root.winfo_viewable())
        if shown:
            x, y = root.winfo_x(), root.winfo_y()
        else:
            x, y = left + (right - left - width) // 2, top + (bottom - top - height - CHROME) // 2
        placed = (max(left, min(x, right - width)), max(top, min(y, bottom - height - CHROME)))
        if height < root.winfo_reqheight():
            self.clamped = True
            root.geometry("%dx%d+%d+%d" % (width, height, *placed))
        elif not shown or placed != (x, y):
            root.geometry("+%d+%d" % placed)
        self._reveal()

    def _select(self, index: int) -> None:
        if self.finder is not None:
            self.finder.current(index)
            self._paint_found()

    def _move(self, delta: int) -> None:
        if self.finder is not None:
            self.finder.move(delta)
            self._paint_found()

    def _enter(self, index: Optional[int] = None) -> None:
        if self.finder is not None:
            self.finder.enter(index)
            self._paint_found()

    def _back(self) -> None:
        if self.finder is not None and self.finder.back():
            self._paint_found()

    def _key_right(self, _e: object) -> Optional[str]:
        """→ at the end of the box steps into the selected folder; elsewhere it moves the cursor."""
        if self.finder is None or self.finder.current() is None or self.entry.index("insert") < len(self.query.get()):
            return None
        self._enter()
        return "break"

    def _key_back(self, event: Any) -> Optional[str]:
        """← at the start of the box, or Backspace in an empty one, steps back up while browsing."""
        if self.finder is None or not self.finder.browsing:
            return None
        empty = self.placeholder or not self.query.get()
        at_start = self.entry.index("insert") == 0
        if not (empty or (getattr(event, "keysym", "") == "Left" and at_start)):
            return None
        self._back()
        return "break"

    def _explore_found(self) -> None:
        here = self.finder.current() if self.finder else None
        if here is not None:
            self._show_in_explorer(here.row.path)

    def _archive_here(self) -> None:
        """File the mail in the selected folder at once, with the window's category, copy and date-prefix choices."""
        here = self.finder.current() if self.finder else None
        if here is None or self.busy:
            return
        if not here.fileable:
            self._say(OUTSIDE + ": pick a folder inside them to archive.", self.pal.danger_text)
            return
        self._take(here.row.path, FOUND_CHIP, FOUND_NOTE)
        self._archive()

    # ------------------------------------------------------------ filing

    def _archive(self) -> None:
        assert self.model is not None
        request = self.model.request()
        if request is None:
            self._say("Pick a folder first." if self.model.selected() is None else "Turn on “Archive a copy anyway” to file it again.",
                      self.pal.danger_text)
            return
        self.busy = True
        self._paint_summary()
        self.root.update_idletasks()
        result = self.on_file(request.folder, request.category, request.duplicate, request.date_prefix)
        self.busy = False
        out = self.model.outcome(result)
        if out.filed:
            # The .msg is confirmed on disk; tagging and indexing run after the window is gone, and say so if they fail.
            logger.info("ℹ️ archive: %s", out.text)
            self.close()
            return
        self._say(out.text, self.pal.danger_text)
        self._paint_summary()
