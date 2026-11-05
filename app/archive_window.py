"""The Archive window the Outlook macro opens when no app page is open: the sheet's suggestions, in Tk.

It shows the same document the Archive sheet does: the mail, the suggested
folders with why, another folder from a folder picker, the category, and one
Archive button. Filing goes through the caller's ``on_file``, which runs in
this same process and COM apartment. A failure is shown in its own words and
the window stays open, so the user can pick another folder or close it.
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import filedialog
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# How long the window stays up after a filing, so the result can be read.
CLOSE_AFTER_MS = 1800
_WRAP = 460


def option_lines(doc: dict[str, Any]) -> list[tuple[str, str]]:
    """``(folder path, label)`` for each suggested folder: its two last parts, then why."""
    lines = []
    for s in doc.get("suggestions") or []:
        why = " · ".join(c["label"] for c in s.get("why") or [])
        lines.append((s["folder_path"], "%s    %s%s" % (s["display_name"], why, (" · " + s["note"]) if s.get("note") else "")))
    return lines


def mail_line(mail: dict[str, Any]) -> str:
    """The sender, the date and the attachments, under the subject."""
    parts = [mail.get("sender") or "(no sender)", (mail.get("date_sent") or "")[:16].replace("T", " ")]
    n = int(mail.get("attachment_count") or 0)
    if n:
        parts.append("%d attachment%s" % (n, "" if n == 1 else "s"))
    if mail.get("where"):
        parts.append(mail["where"])
    return " · ".join(p for p in parts if p)


class _Window:
    def __init__(self, doc: dict[str, Any], on_file: Callable[[str, Optional[str]], dict[str, Any]]) -> None:
        self.doc = doc
        self.on_file = on_file
        self.root = tk.Tk()
        self.root.title("Archive mail")
        self.root.attributes("-topmost", True)
        self.frame = tk.Frame(self.root, padx=16, pady=14)
        self.frame.pack(fill="both", expand=True)
        self.choice = tk.StringVar(value="")
        self.tag = tk.BooleanVar(value=bool(doc.get("category")))
        self.status = tk.Label(self.frame, text="", fg="#b42318", wraplength=_WRAP, justify="left")

    def _label(self, text: str, **kw: Any) -> tk.Label:
        label = tk.Label(self.frame, text=text, wraplength=_WRAP, justify="left", anchor="w", **kw)
        label.pack(fill="x", pady=(0, 4))
        return label

    def build(self) -> None:
        if not self.doc.get("ok"):
            self._label("Couldn't archive this mail", font=("Segoe UI", 11, "bold"))
            self._label(self.doc["error"]["message"])
            tk.Button(self.frame, text="Close", command=self.root.destroy).pack(anchor="e", pady=(10, 0))
            return
        mail = self.doc["mail"]
        self._label(mail.get("subject") or "(no subject)", font=("Segoe UI", 11, "bold"))
        self._label(mail_line(mail), fg="#57606a")
        if self.doc.get("already_archived"):
            self._label("Already archived: " + self.doc["already_archived"], fg="#9a6700")
        self._label("Suggested folders", font=("Segoe UI", 10, "bold"))
        lines = option_lines(self.doc)
        if lines:
            self.choice.set(lines[0][0])
        else:
            self._label("No folder matches this mail yet: pick one below.", fg="#57606a")
        for path, text in lines:
            tk.Radiobutton(self.frame, text=text, value=path, variable=self.choice, anchor="w", justify="left",
                           wraplength=_WRAP).pack(fill="x")
        tk.Button(self.frame, text="Another folder…", command=self._pick).pack(anchor="w", pady=(6, 6))
        if self.doc.get("category"):
            tk.Checkbutton(self.frame, text="Tag with the Outlook category " + self.doc["category"],
                           variable=self.tag, anchor="w").pack(fill="x")
        self.status.pack(fill="x", pady=(6, 0))
        buttons = tk.Frame(self.frame)
        buttons.pack(fill="x", pady=(10, 0))
        tk.Button(buttons, text="Cancel", command=self.root.destroy).pack(side="right")
        self.go = tk.Button(buttons, text="Archive", command=self._archive, default="active")
        self.go.pack(side="right", padx=(0, 8))
        if self.doc.get("already_archived"):
            self.go.configure(state="disabled")

    def _pick(self) -> None:
        roots = self.doc.get("roots") or []
        folder = filedialog.askdirectory(parent=self.root, initialdir=roots[0] if roots else None,
                                         title="Pick an email folder")
        if folder:
            self.choice.set(folder)
            self.status.configure(text="Chosen: " + folder, fg="#57606a")

    def _archive(self) -> None:
        folder = self.choice.get()
        if not folder:
            self.status.configure(text="Pick a folder first.", fg="#b42318")
            return
        self.go.configure(state="disabled", text="Archiving…")
        self.root.update_idletasks()
        result = self.on_file(folder, self.doc["category"] if self.tag.get() else None)
        if result.get("ok"):
            self.status.configure(text="Archived to " + result["folder_path"], fg="#1a7f37")
            self.root.after(CLOSE_AFTER_MS, self.root.destroy)
            return
        self.status.configure(text=result["error"]["message"], fg="#b42318")
        self.go.configure(state="normal", text="Archive")


def show_window(doc: dict[str, Any], on_file: Callable[[str, Optional[str]], dict[str, Any]]) -> None:
    """Show the window until it is closed or the mail is filed."""
    window = _Window(doc, on_file)
    window.build()
    logger.info("ℹ️ archive: the fallback window is open")
    window.root.mainloop()
