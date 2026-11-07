"""The Tk widgets of the fallback Archive window: one painting of ``ModelView``, in the app's tokens.

Filing goes through the caller's ``on_file``, which runs in this same process and COM
apartment. A failure is shown in its own words and the window stays open, so the user
can pick another folder or close it.
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import filedialog
from typing import Any, Callable, Optional

from src import desktop
from src.archive_window.model import (
    DATE_CHOICES,
    NO_CATEGORY,
    RANK_NOTE,
    ModelView,
    Option,
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

logger = logging.getLogger(__name__)

# How long the window stays up after a filing, so the result can be read.
CLOSE_AFTER_MS = 1800
# How long typing in "Another email folder" pauses before it searches.
DEBOUNCE_MS = 200
WIDTH = 640
_WRAP = WIDTH - 2 * SPACE_MD - 24
PLACEHOLDER = "Another email folder"

OnFile = Callable[[str, Optional[str], bool, str], dict[str, Any]]
FindFolders = Callable[[str], list[dict[str, Any]]]


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
    def __init__(self, doc: dict[str, Any], on_file: OnFile, find_folders: Optional[FindFolders], pal: Palette,
                 root: Optional[tk.Tk] = None) -> None:
        self.doc, self.on_file, self.find_folders, self.pal = doc, on_file, find_folders, pal
        self.model = ModelView(doc) if doc.get("ok") else None
        # A caller that opens many windows in one process (the tests) passes the one root it owns.
        self.root = root or tk.Tk()
        self.root.title("Archive mail")
        self.root.configure(bg=pal.card)
        self.root.attributes("-topmost", True)
        self.root.minsize(WIDTH, 0)
        self.frame = tk.Frame(self.root, bg=pal.card, padx=SPACE_MD, pady=SPACE_MD)
        self.frame.pack(fill="both", expand=True)
        self.status = tk.Label(self.frame, text="", bg=pal.card, fg=pal.danger_text, font=_font(SIZE_SMALL), wraplength=_WRAP, justify="left", anchor="w")
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
        self._build_mail()
        if self.model.filed:
            self._build_filed()
        self._build_options()
        self._build_other()
        self._build_settings()
        self.status.pack(fill="x", pady=(SPACE_SM, 0))
        buttons = tk.Frame(self.frame, bg=self.pal.card)
        buttons.pack(fill="x", pady=(SPACE_SM, 0))
        self._button(buttons, "Cancel", self.close).pack(side="right")
        self.go = self._button(buttons, "", self._archive, primary=True)
        self.go.pack(side="right", padx=(0, SPACE_SM))
        self._paint_summary()

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

    def _paint_options(self) -> None:
        assert self.model is not None
        for child in self.options.winfo_children():
            child.destroy()
        opts = self.model.options()
        if not opts:
            self._label(self.options, "No folder matches this mail yet. Find one below.", SIZE_SMALL, fg=self.pal.muted)
        for i, option in enumerate(opts):
            self._option_row(i, option, i == self.model.choice)

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
        self.placeholder = True
        self.query = tk.StringVar(value=PLACEHOLDER)
        self.entry = tk.Entry(row, textvariable=self.query, font=_font(SIZE_BODY), relief="flat", bg=pal.card_off, fg=pal.muted,
                              insertbackground=pal.ink, highlightthickness=1, highlightbackground=pal.line, highlightcolor=pal.accent)
        self.entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.entry.bind("<FocusIn>", self._clear_placeholder)
        self.entry.bind("<FocusOut>", self._restore_placeholder)
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
        if not option.open_target:
            return
        try:
            desktop.open_in_explorer(option.open_target)
            self._say("Opened in File Explorer", self.pal.muted)
        except (OSError, FileNotFoundError) as exc:
            self._say(str(exc) or "File Explorer couldn't open the folder", self.pal.danger_text)

    def _say(self, text: str, color: str) -> None:
        self.status.configure(text=text, fg=color)

    # ------------------------------------------------------------ another folder

    def _pick(self) -> None:
        assert self.model is not None
        folder = filedialog.askdirectory(parent=self.root, initialdir=(self.model.roots or [None])[0], title="Pick an email folder")
        if folder:
            self._take(folder.replace("/", "\\"))

    def _take(self, path: str) -> None:
        assert self.model is not None
        self.model.pick_extra(path)
        for child in self.found.winfo_children():
            child.destroy()
        self._paint_options()
        self._paint_summary()

    def _schedule_search(self) -> None:
        if self.search_job:
            self.root.after_cancel(self.search_job)
        self.search_job = self.root.after(DEBOUNCE_MS, self._search)

    def _search(self) -> None:
        self.search_job = None
        text = self.query.get()
        for child in self.found.winfo_children():
            child.destroy()
        if self.find_folders is None or not text.strip() or self.placeholder:
            return
        try:
            hits = self.find_folders(text)
        except Exception as exc:  # noqa: BLE001 - a search failure is shown, never fatal to the window
            logger.exception("❌ archive window: the folder search failed")
            self._label(self.found, "%s: %s" % (type(exc).__name__, exc), SIZE_SMALL, fg=self.pal.danger_text)
            return
        if not hits:
            self._label(self.found, "No email folder matches every word.", SIZE_SMALL, fg=self.pal.muted, pady=(4, 0))
        assert self.model is not None
        for hit in hits:
            btn = tk.Button(self.found, text=under_root(hit["path"], self.model.roots), anchor="w", relief="flat", bd=0, bg=self.pal.card,
                            fg=self.pal.ink, font=_font(SIZE_SMALL, mono=True), activebackground=self.pal.accent_soft, cursor="hand2",
                            command=lambda p=hit["path"]: self._take(p), padx=SPACE_SM, pady=4)
            btn.pack(fill="x")

    # ------------------------------------------------------------ filing

    def _archive(self) -> None:
        assert self.model is not None
        request = self.model.request()
        if request is None:
            self._say("Pick a folder first.", self.pal.danger_text)
            return
        self.busy = True
        self._paint_summary()
        self.root.update_idletasks()
        result = self.on_file(request.folder, request.category, request.duplicate, request.date_prefix)
        self.busy = False
        out = self.model.outcome(result)
        self._say(out.text, self.pal.success if out.ok else self.pal.danger_text)
        if out.filed:
            self.go.configure(state="disabled")
            self.root.after(CLOSE_AFTER_MS, self.close)
            return
        self._paint_summary()
