"""Fake Outlook mail items and an extract-msg stand-in, shared by the archive tests (invented data only)."""
from __future__ import annotations

import sys
import types
from datetime import datetime


class _FakeAttachment:
    """A real (non-inline) attachment: no MAPI properties, so the archiver's
    inline detection falls through both branches and saves it."""

    def __init__(self, filename):
        self.FileName = filename

    @property
    def PropertyAccessor(self):
        raise AttributeError("no PropertyAccessor on this fake")

    def SaveAsFile(self, path):  # noqa: N802 - COM-shaped API
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("att")


class _FakeAttachments:
    def __init__(self, items=()):
        self._items = list(items)

    def __iter__(self):
        return iter(self._items)


class _FakeMailItem:
    """Enough of a MailItem for the archiver: dates, SaveAs, attachments."""

    def __init__(
        self, sent_on=datetime(2026, 3, 14, 9, 30), attachments=(), entry_id="entry-1"
    ):
        self.SentOn = sent_on
        self.EntryID = entry_id
        self.ReceivedTime = None
        self.Attachments = _FakeAttachments(
            _FakeAttachment(n) for n in attachments
        )

    def SaveAs(self, path, fmt):  # noqa: N802 - COM-shaped API
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("msg")


def win32com_client(monkeypatch):
    """The real ``win32com.client`` when pywin32 is installed, else a stub in ``sys.modules`` for this test only."""
    try:
        import win32com.client as client
    except ImportError:
        client = types.ModuleType("win32com.client")
        client.Dispatch = lambda obj: obj
        package = types.ModuleType("win32com")
        package.client = client
        monkeypatch.setitem(sys.modules, "win32com", package)
        monkeypatch.setitem(sys.modules, "win32com.client", client)
    return client
