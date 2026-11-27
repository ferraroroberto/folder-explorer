"""The formats a capture leaves on the clipboard, written through a fake clipboard: the real one is never touched."""

from __future__ import annotations

import struct
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from src.snap import overlay
from src.snap.selection import Outcome

CF_DIB, CF_HDROP, DROP_EFFECT_ID = 8, 15, 49999
FILE_FORMATS = {CF_DIB, CF_HDROP, DROP_EFFECT_ID}


class FakeClipboard:
    """The ``win32clipboard`` calls the copy makes; ``refusals`` opens fail first, as when another app holds the clipboard."""

    def __init__(self, refusals: int = 0) -> None:
        self.refusals = refusals
        self.data: dict[int, bytes] = {}
        self.open = False

    def OpenClipboard(self) -> None:
        if self.refusals:
            self.refusals -= 1
            raise OSError("another app has the clipboard open")
        self.open = True

    def EmptyClipboard(self) -> None:
        self.data.clear()

    def RegisterClipboardFormat(self, name: str) -> int:
        assert name == "Preferred DropEffect"
        return DROP_EFFECT_ID

    def SetClipboardData(self, kind: int, data: bytes) -> None:
        assert self.open
        self.data[kind] = data

    def CloseClipboard(self) -> None:
        self.open = False


@pytest.fixture
def clipboard(monkeypatch: pytest.MonkeyPatch) -> FakeClipboard:
    fake = FakeClipboard()
    monkeypatch.setitem(sys.modules, "win32clipboard", fake)
    return fake


def capture(folder: Path, **settings: Any) -> None:
    """One selection released on a bare overlay (no Tk window, no screen grab): it saves into ``folder`` and copies."""
    shot = object.__new__(overlay.CaptureOverlay)
    shot.config = SimpleNamespace(folder=folder, file_prefix="snap", jpeg_quality=90, **settings)
    shot._selection = SimpleNamespace(release=lambda _point, _size: (Outcome.SAVE, (0, 0, 8, 6)))
    shot._frozen = Image.new("RGB", (20, 20), (200, 30, 30))
    shot._image_point = lambda _event: (8, 6)
    shot._close_overlay = lambda: None
    shot._finish = lambda: None
    shot._t0 = 0.0
    shot._on_release(None)


def dropped_files(block: bytes) -> list[str]:
    offset, _x, _y, _non_client, wide = struct.unpack_from("<IiiII", block)
    assert wide == 1
    names = block[offset:].decode("utf-16-le")
    assert names.endswith("\0\0")
    return names[:-2].split("\0")


def test_a_capture_copies_the_image_and_the_saved_file_as_a_copy(clipboard: FakeClipboard, tmp_path: Path) -> None:
    capture(tmp_path / "shots", copy_to_clipboard=True, copy_as_file=True)
    (saved,) = (tmp_path / "shots").iterdir()
    assert set(clipboard.data) == FILE_FORMATS
    assert dropped_files(clipboard.data[CF_HDROP]) == [str(saved)]
    assert struct.unpack("<I", clipboard.data[DROP_EFFECT_ID]) == (1,)  # DROPEFFECT_COPY: a paste never moves the capture
    assert struct.unpack_from("<Iii", clipboard.data[CF_DIB]) == (40, 8, 6)
    assert not clipboard.open


def test_with_copy_as_file_off_the_clipboard_holds_only_the_image(clipboard: FakeClipboard, tmp_path: Path) -> None:
    capture(tmp_path / "shots", copy_to_clipboard=True, copy_as_file=False)
    assert set(clipboard.data) == {CF_DIB}


def test_with_the_clipboard_off_nothing_is_written(clipboard: FakeClipboard, tmp_path: Path) -> None:
    capture(tmp_path / "shots", copy_to_clipboard=False, copy_as_file=True)
    assert clipboard.data == {} and list((tmp_path / "shots").iterdir())


def test_a_capture_that_cannot_be_saved_still_copies_the_image_and_says_why(
    clipboard: FakeClipboard, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def refuse(_path: Path, _data: bytes) -> None:
        raise PermissionError("the folder is read-only")

    monkeypatch.setattr(overlay, "atomic_write_bytes", refuse)
    capture(tmp_path / "shots", copy_to_clipboard=True, copy_as_file=True)
    assert set(clipboard.data) == {CF_DIB}
    assert "the folder is read-only" in caplog.text and "only the image" in caplog.text


def test_a_clipboard_held_by_another_app_is_retried(clipboard: FakeClipboard, tmp_path: Path) -> None:
    clipboard.refusals = 3
    capture(tmp_path / "shots", copy_to_clipboard=True, copy_as_file=True)
    assert set(clipboard.data) == FILE_FORMATS
