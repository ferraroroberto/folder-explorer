"""What the region screenshot may do, held by reading the code: where it writes, and who starts it."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _files(*folders: str) -> list[Path]:
    return [p for folder in folders for p in (ROOT / folder).rglob("*.py")]


def test_only_the_overlay_writes_a_capture() -> None:
    writers = sorted(p.relative_to(ROOT).as_posix() for p in _files("src/snap") if "atomic_write_bytes(" in p.read_text(encoding="utf-8"))
    assert writers == ["src/snap/overlay.py"]


def test_only_the_tray_builds_the_service() -> None:
    # No test and no e2e instance can start a hotkey, because only the tray's own main() constructs a SnapService.
    builders = sorted(p.relative_to(ROOT).as_posix() for p in _files("app", "src", "scripts")
                      if "SnapService(" in p.read_text(encoding="utf-8") and p.name != "service.py")
    assert builders == ["app/tray/tray.py"]
