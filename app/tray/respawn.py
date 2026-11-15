"""What the tray does when the webapp is not answering, split from ``tray.py`` so a test can drive it.

Every spawn goes through one ``RespawnGuard``: a slow boot (after an unclean
reboot) leaves a child still importing when the next probe fails, and a second
spawn then leaves two copies on one port.
"""

from __future__ import annotations

from typing import Callable, Protocol

from app.tray.watchdog import RespawnGuard, RespawnOutcome


class _Manager(Protocol):
    def healthy(self) -> bool: ...
    def port_in_use(self) -> bool: ...
    def spawn(self) -> int: ...


def make_launch(manager: _Manager, guard: RespawnGuard, crumbs: Callable[[str], None]) -> Callable[[], RespawnOutcome | None]:
    """Adopt a webapp that already answers, else spawn one through the guard."""

    def launch() -> RespawnOutcome | None:
        if manager.healthy():
            crumbs("webapp already answering; adopted")
            return None
        return guard.respawn(manager.spawn)

    return launch


def make_on_wedge(
    manager: _Manager,
    guard: RespawnGuard,
    crumbs: Callable[[str], None],
    rearm: Callable[[], None],
) -> Callable[[int], None]:
    """The watchdog's ``on_wedge``: back off while the child boots, alert on a listening wedge, respawn a dead one."""

    def on_wedge(failures: int) -> None:
        if guard.is_starting():
            crumbs("webapp pid %s still starting after %d failed probes; waiting" % (guard.pid, failures))
            rearm()
            return
        if manager.port_in_use():
            crumbs("webapp listening but not answering /healthz after %d probes; leaving it running" % failures)
            return
        crumbs("webapp not listening after %d probes; respawning" % failures)
        try:
            guard.respawn(manager.spawn)
        except Exception as exc:  # noqa: BLE001 -- the tray must outlive a failed respawn
            crumbs("webapp respawn failed: %s" % exc)
        rearm()

    return on_wedge
