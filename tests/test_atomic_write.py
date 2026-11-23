"""The atomic writer swaps new content in; a failed write leaves the old file and no temp."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

from src import atomic_write
from src.atomic_write import atomic_write_text


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    """A folder of its own, so the listing sees only what the writer left."""
    path = tmp_path / "out"
    path.mkdir()
    return path


def test_a_replace_lands_the_new_content(folder: Path) -> None:
    target = folder / "config.json"
    target.write_text("old", encoding="utf-8")
    atomic_write_text(target, '{"k": "ü"}')
    assert target.read_text(encoding="utf-8") == '{"k": "ü"}'
    assert [p.name for p in folder.iterdir()] == ["config.json"]


def test_a_failed_replace_keeps_the_old_content_and_no_temp(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = folder / ".env"
    target.write_text("old", encoding="utf-8")

    def locked(src: str, dst: Path) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(atomic_write.os, "replace", locked)
    with pytest.raises(PermissionError):
        atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "old"
    assert [p.name for p in folder.iterdir()] == [".env"]


def test_a_failed_write_keeps_the_old_content_and_no_temp(folder: Path) -> None:
    target = folder / "config.json"
    target.write_text("old", encoding="utf-8")
    with pytest.raises(UnicodeEncodeError):
        atomic_write_text(target, "\udcff")  # a lone surrogate cannot be encoded
    assert target.read_text(encoding="utf-8") == "old"
    assert [p.name for p in folder.iterdir()] == ["config.json"]


def _held(code: int) -> OSError:
    """The error Windows raises on a replace while another program, such as a sync engine, holds the target."""
    err = PermissionError(13, "Access is denied")
    err.winerror = code  # type: ignore[attr-defined]
    return err


@pytest.mark.parametrize("code", [5, 32, 33])
def test_a_replace_waits_out_a_brief_hold_on_the_target(folder: Path, monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    target = folder / "note.md"
    target.write_bytes(b"old")
    real, calls = atomic_write.os.replace, []

    def held_twice(src: str, dst: Path) -> None:
        calls.append(dst)
        if len(calls) <= 2:
            raise _held(code)
        real(src, dst)

    monkeypatch.setattr(atomic_write.os, "replace", held_twice)
    monkeypatch.setattr(atomic_write.time, "sleep", lambda _s: None)
    atomic_write.atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"new" and len(calls) == 3
    assert [p.name for p in folder.iterdir()] == ["note.md"]


def test_a_hold_that_outlasts_the_budget_keeps_the_old_content(folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = folder / "note.md"
    target.write_bytes(b"old")
    waits: list[float] = []

    def held(src: str, dst: Path) -> None:
        raise _held(32)

    monkeypatch.setattr(atomic_write.os, "replace", held)
    monkeypatch.setattr(atomic_write.time, "sleep", waits.append)
    with pytest.raises(PermissionError):
        atomic_write.atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"old" and [p.name for p in folder.iterdir()] == ["note.md"]
    assert 0 < sum(waits) <= 3


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only sharing semantics")
def test_a_real_handle_released_mid_save_lets_the_save_land(folder: Path) -> None:
    """A sync engine's read handle (no delete sharing) refuses the swap until it closes; the save then lands."""
    target = folder / "note.md"
    target.write_bytes(b"old")
    handle = open(target, "rb")  # noqa: SIM115 - held on purpose, closed by the timer
    timer = threading.Timer(0.3, handle.close)
    timer.start()
    try:
        atomic_write.atomic_write_bytes(target, b"new")
    finally:
        timer.cancel()
        handle.close()
    assert target.read_bytes() == b"new" and [p.name for p in folder.iterdir()] == ["note.md"]
