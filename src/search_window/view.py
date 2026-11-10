"""The Tk widgets of the Quick search window: one painting of ``QuickSearchModel``, in the app's tokens.

Typing searches after a short pause, on a worker thread, so a slow or stopped web app
never freezes the window; the answer is painted on Tk's thread and dropped when the
query moved on meanwhile. Esc or the close box hides the window and keeps its state.
"""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from typing import Callable, Optional

from src.archive_window.theme import FONT, MONO, SIZE_BODY, SIZE_CAPTION, SIZE_SMALL, SPACE_MD, SPACE_SM, Palette
from src.archive_window.view import Segmented
from src.search_window.model import TABS, Answer, QuickSearchModel, Row, Segments, Status, fetch

logger = logging.getLogger(__name__)

TITLE = "Quick search"
# How long typing pauses before it searches; the page's Search tab waits about as long.
DEBOUNCE_MS = 200
# How often Tk's thread looks for a worker's answer.
POLL_MS = 15
WIDTH, HEIGHT = 680, 520
KEYS = "↑ ↓ move · Enter open · Ctrl+Shift+C copy path · Tab switches tab · Esc hide"


def _font(size: int = SIZE_BODY, bold: bool = False, mono: bool = False) -> tuple[str, int, str]:
    return (MONO if mono else FONT, size, "bold" if bold else "normal")


class QuickSearchView:
    def __init__(self, model: QuickSearchModel, pal: Palette, root: Optional[tk.Tk] = None,
                 on_hide: Optional[Callable[[], None]] = None) -> None:
        self.model, self.pal, self.on_hide = model, pal, on_hide
        self.root = root or tk.Tk()
        self.search_job: Optional[str] = None
        self.answers: queue.Queue[Answer] = queue.Queue()
        self.inflight = 0
        # Each row's first and last text line, for a click to find its row.
        self.spans: list[tuple[int, int]] = []

    # ------------------------------------------------------------ build

    def build(self) -> None:
        pal, root = self.pal, self.root
        root.title(TITLE)
        root.configure(bg=pal.card)
        root.attributes("-topmost", True)
        root.geometry("%dx%d" % (WIDTH, HEIGHT))
        root.minsize(420, 300)
        root.protocol("WM_DELETE_WINDOW", self.hide)
        frame = self.frame = tk.Frame(root, bg=pal.card, padx=SPACE_MD, pady=SPACE_MD)
        frame.pack(fill="both", expand=True)

        top = tk.Frame(frame, bg=pal.card)
        top.pack(fill="x")
        self.tabs = Segmented(top, pal, list(TABS), self.model.tab, self.switch)
        self.tabs.pack(side="left")
        self.count = tk.Label(top, text="", bg=pal.card, fg=pal.muted, font=_font(SIZE_SMALL))
        self.count.pack(side="right")

        self.query = tk.StringVar(value=self.model.query)
        self.entry = tk.Entry(frame, textvariable=self.query, font=_font(SIZE_BODY), relief="flat", bg=pal.card_off, fg=pal.ink,
                              insertbackground=pal.ink, highlightthickness=1, highlightbackground=pal.line, highlightcolor=pal.accent)
        self.entry.pack(fill="x", pady=(SPACE_SM, SPACE_SM), ipady=6)
        self.query.trace_add("write", lambda *_a: self._typed())

        body = tk.Frame(frame, bg=pal.card, highlightthickness=1, highlightbackground=pal.line_muted)
        body.pack(fill="both", expand=True)
        self.list = tk.Text(body, wrap="none", relief="flat", bd=0, bg=pal.card, fg=pal.ink, cursor="arrow", padx=SPACE_SM,
                            pady=4, font=_font(SIZE_SMALL), highlightthickness=0, takefocus=0, spacing1=1)
        bar = tk.Scrollbar(body, command=self.list.yview)
        self.list.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.list.pack(side="left", fill="both", expand=True)
        self._tags()
        self.list.bind("<Button-1>", self._click)
        self.list.bind("<Double-Button-1>", self._double)

        foot = tk.Frame(frame, bg=pal.card)
        foot.pack(fill="x", pady=(SPACE_SM, 0))
        self.status = tk.Label(foot, text="", bg=pal.card, fg=pal.muted, font=_font(SIZE_SMALL), anchor="w", justify="left",
                               wraplength=WIDTH - 260)
        self.status.pack(side="left", fill="x", expand=True)
        self.open_btn = self._button(foot, "Open", self.open_selected, primary=True)
        self.open_btn.pack(side="right")
        self.copy_btn = self._button(foot, "Copy path", self.copy_path)
        self.copy_btn.pack(side="right", padx=(0, SPACE_SM))
        tk.Label(frame, text=KEYS, bg=pal.card, fg=pal.muted, font=_font(SIZE_CAPTION), anchor="w").pack(fill="x", pady=(4, 0))

        for key, handler in (("<Escape>", lambda _e: self.hide()), ("<Return>", lambda _e: self.enter()),
                             ("<Up>", lambda _e: self.move(-1)), ("<Down>", lambda _e: self.move(1)),
                             ("<Prior>", lambda _e: self.move(-10)), ("<Next>", lambda _e: self.move(10)),
                             ("<Control-Shift-C>", lambda _e: self.copy_path()), ("<Control-Shift-c>", lambda _e: self.copy_path())):
            root.bind(key, lambda e, h=handler: (h(e), "break")[1])
        self.entry.bind("<Tab>", lambda _e: (self.switch(self._other_tab()), "break")[1])
        self.paint()

    def _button(self, parent: tk.Misc, text: str, command: Callable[[], None], primary: bool = False) -> tk.Button:
        pal = self.pal
        return tk.Button(parent, text=text, command=command, font=_font(SIZE_BODY, bold=primary), relief="flat", cursor="hand2",
                         bg=pal.accent_fill if primary else pal.card_off, fg=pal.accent_fg if primary else pal.ink,
                         activebackground=pal.accent if primary else pal.neutral_soft,
                         activeforeground=pal.accent_fg if primary else pal.ink, disabledforeground=pal.muted,
                         padx=SPACE_MD, pady=4, bd=0, highlightthickness=0 if primary else 1, highlightbackground=pal.line)

    def _tags(self) -> None:
        pal, t = self.pal, self.list
        t.tag_configure("title", font=_font(SIZE_BODY, bold=True), foreground=pal.ink)
        t.tag_configure("aside", foreground=pal.muted)
        t.tag_configure("detail", font=_font(SIZE_SMALL, mono=True), foreground=pal.ink)
        t.tag_configure("meta", foreground=pal.muted, spacing3=6)
        t.tag_configure("mark", background=pal.attention_soft, foreground=pal.attention_text)
        t.tag_configure("selected", background=pal.accent_soft)
        t.tag_configure("note", foreground=pal.muted, spacing1=SPACE_SM, lmargin1=SPACE_SM, lmargin2=SPACE_SM)
        t.tag_configure("problem", foreground=pal.danger_text, spacing1=SPACE_SM, lmargin1=SPACE_SM, lmargin2=SPACE_SM)
        t.tag_raise("mark")
        t.tag_lower("selected")

    # ------------------------------------------------------------ painting

    def paint(self) -> None:
        """Paint the model's answer: the rows, or the hint, the empty line or the problem, never mixed up."""
        m, t = self.model, self.list
        t.configure(state="normal")
        t.delete("1.0", "end")
        self.spans = []
        status = m.status
        if m.answer is not None and m.answer.notes and status is Status.RESULTS:
            for note in m.answer.notes:
                t.insert("end", "ⓘ " + note + "\n", "note")
        if status is Status.RESULTS:
            for i, row in enumerate(m.rows):
                self._row(i, row)
        else:
            tag = "problem" if status in (Status.UNREACHABLE, Status.ERROR) else "note"
            t.insert("end", m.message, tag)
        t.configure(state="disabled")
        self.count.configure(text=m.count_line())
        has = m.current() is not None
        self.open_btn.configure(state="normal" if has else "disabled")
        self.copy_btn.configure(state="normal" if has else "disabled")
        self._paint_selection()

    def _segments(self, segments: Segments, tag: str) -> None:
        for text, mark in segments:
            self.list.insert("end", text, (tag, "mark") if mark else (tag,))

    def _row(self, i: int, row: Row) -> None:
        t = self.list
        first = int(t.index("end-1c").split(".")[0])
        self._segments(row.title, "title")
        if row.aside:
            t.insert("end", "   " + row.aside, ("aside",))
        t.insert("end", "\n")
        if row.detail:
            self._segments(row.detail, "detail")
            t.insert("end", "\n")
        t.insert("end", row.meta + "\n", ("meta",))
        self.spans.append((first, int(t.index("end-1c").split(".")[0]) - 1))

    def _paint_selection(self) -> None:
        t = self.list
        t.tag_remove("selected", "1.0", "end")
        if not self.spans or self.model.current() is None:
            return
        first, last = self.spans[self.model.selected]
        t.tag_add("selected", "%d.0" % first, "%d.0" % (last + 1))
        t.see("%d.0" % (last + 1))
        t.see("%d.0" % first)

    # ------------------------------------------------------------ searching

    def _typed(self) -> None:
        self.model.query = self.query.get()
        if self.search_job:
            self.root.after_cancel(self.search_job)
        self.search_job = self.root.after(DEBOUNCE_MS, self.search)

    def search(self) -> None:
        """Ask for the current tab and query on a worker; ``_poll`` paints the answer when it comes."""
        if self.search_job:
            self.root.after_cancel(self.search_job)
        self.search_job = None
        tab, query, client = self.model.tab, self.model.query, self.model.client
        self.status.configure(text="Searching…", fg=self.pal.muted)

        def work() -> None:
            try:
                self.answers.put(fetch(client, tab, query))
            except Exception:  # noqa: BLE001 - a bug in a search is logged and shown, never fatal to the window
                logger.exception("❌ quick search: the search failed")
                self.answers.put(Answer(tab, query, Status.ERROR, message="The search failed; the tray log has the details."))

        threading.Thread(target=work, name="quick-search", daemon=True).start()
        self.inflight += 1
        if self.inflight == 1:
            self.root.after(POLL_MS, self._poll)

    def _poll(self) -> None:
        shown = False
        while True:
            try:
                answer = self.answers.get_nowait()
            except queue.Empty:
                break
            self.inflight -= 1
            shown = self.model.apply(answer) or shown
        if shown:
            self.status.configure(text="")
            self.paint()
        if self.inflight > 0:
            self.root.after(POLL_MS, self._poll)

    def switch(self, tab: str) -> None:
        if tab == self.model.tab:
            return
        self.model.tab = tab
        self.tabs.paint(tab)
        self.search()
        self.paint()

    def _other_tab(self) -> str:
        keys = [k for k, _ in TABS]
        return keys[(keys.index(self.model.tab) + 1) % len(keys)]

    # ------------------------------------------------------------ acting

    def move(self, delta: int) -> None:
        self.model.move(delta)
        self._paint_selection()

    def enter(self) -> None:
        """Enter searches now when the shown answer isn't for the text typed; otherwise it opens the selected row."""
        if self.search_job or not self.model.is_current():
            self.search()
            return
        self.open_selected()

    def _row_at(self, event: tk.Event) -> Optional[int]:
        line = int(self.list.index("@%d,%d" % (event.x, event.y)).split(".")[0])
        return next((i for i, (a, b) in enumerate(self.spans) if a <= line <= b), None)

    def _click(self, event: tk.Event) -> str:
        i = self._row_at(event)
        if i is not None:
            self.model.selected = i
            self._paint_selection()
        self.entry.focus_set()
        return "break"

    def _double(self, event: tk.Event) -> str:
        i = self._row_at(event)
        if i is not None:
            self.open_selected(i)
        return "break"

    def open_selected(self, index: Optional[int] = None) -> None:
        ok, text = self.model.open(index)
        self._paint_selection()
        if not text:
            return
        self.status.configure(text=text, fg=self.pal.muted if ok else self.pal.danger_text)
        if ok:
            # The window stays on top of everything, so it steps aside for the folder or mail it just opened.
            self.hide()

    def copy_path(self) -> None:
        row = self.model.current()
        if row is None:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(row.path)
        self.status.configure(text="Copied " + row.path, fg=self.pal.muted)

    # ------------------------------------------------------------ show / hide

    def hide(self) -> None:
        self.root.withdraw()
        if self.on_hide:
            self.on_hide()

    def show(self) -> None:
        """Bring the window to the front with the query selected, so typing replaces it and arrows move the list."""
        root = self.root
        root.deiconify()
        root.attributes("-topmost", True)
        root.lift()
        root.focus_force()
        self.entry.focus_set()
        self.entry.select_range(0, "end")
        self.entry.icursor("end")
