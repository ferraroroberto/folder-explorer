"""The tray redraws its Screenshot menu when config.json changes under it, so a setting changed in the web app shows there too."""

from __future__ import annotations

import os
from pathlib import Path

from app.tray import snap_menu


def test_the_menu_is_redrawn_once_per_change_to_config_json(isolated: Path) -> None:
    path = isolated / "config.json"
    path.write_text("{}", encoding="utf-8")
    redraws: list[int] = []

    # Drive the loop by hand: each wait() is one poll, and the file changes between polls.
    script = [None, "changed", None, "changed again", None]
    polls = iter(script)

    class Stop:
        def wait(self, _interval: float) -> bool:
            action = next(polls, "stop")
            if action == "stop":
                return True
            if action:
                path.write_text(action, encoding="utf-8")
                stamp = path.stat().st_mtime_ns + 1_000_000_000
                os.utime(path, ns=(stamp, stamp))
            return False

    snap_menu.refresh_when_config_changes(lambda: redraws.append(1), Stop(), interval=0)  # type: ignore[arg-type]
    assert redraws == [1, 1]


def test_a_failed_redraw_does_not_end_the_watch(isolated: Path) -> None:
    path = isolated / "config.json"
    path.write_text("{}", encoding="utf-8")
    steps = iter([1, 2, 3])
    calls: list[int] = []

    class Stop:
        def wait(self, _interval: float) -> bool:
            step = next(steps, None)
            if step is None:
                return True
            stamp = path.stat().st_mtime_ns + 1_000_000_000
            os.utime(path, ns=(stamp, stamp))
            return False

    def refresh() -> None:
        calls.append(1)
        raise RuntimeError("the tray is closing")

    snap_menu.refresh_when_config_changes(refresh, Stop(), interval=0)  # type: ignore[arg-type]
    assert calls == [1, 1, 1]
