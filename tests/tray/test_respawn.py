"""The tray's respawn path never stacks a second webapp while the first child is still booting (fake clock, fake process table)."""

from __future__ import annotations

from app.tray.respawn import make_launch, make_on_wedge
from app.tray.watchdog import HealthWatchdog, RespawnGuard, RespawnOutcome


class _World:
    """A clock, a process table and the webapp port: a child binds ``bind_after_s`` after it spawns."""

    def __init__(self, bind_after_s: float) -> None:
        self.now = 0.0
        self.bind_after_s = bind_after_s
        self.alive: dict[int, float] = {}
        self.stopped: list[int] = []
        self._next = 100

    def spawn(self) -> int:
        self._next += 1
        self.alive[self._next] = self.now
        return self._next

    def identity(self, pid: int) -> int | None:
        return pid if pid in self.alive else None

    def stop_tree(self, pid: int) -> None:
        self.stopped.append(pid)
        self.alive.pop(pid, None)

    def clock(self) -> float:
        return self.now

    def bound(self) -> bool:
        return any(self.now - born >= self.bind_after_s for born in self.alive.values())

    # The WebappManager surface the tray uses.
    def healthy(self) -> bool:
        return self.bound()

    def port_in_use(self) -> bool:
        return self.bound()


def _tray(world: _World) -> tuple[RespawnGuard, HealthWatchdog, list[str]]:
    crumbs: list[str] = []
    guard = RespawnGuard(crumbs.append, identity=world.identity, stop_tree=world.stop_tree, clock=world.clock)
    manager = type("M", (), {"healthy": world.healthy, "port_in_use": world.port_in_use, "spawn": world.spawn})()
    on_wedge = make_on_wedge(manager, guard, crumbs.append, lambda: watchdog.rearm())
    watchdog = HealthWatchdog(world.healthy, on_wedge, guard.mark_healthy, failures_to_alert=3)
    make_launch(manager, guard, crumbs.append)()  # the boot spawn
    return guard, watchdog, crumbs


def test_a_slow_boot_never_gets_a_second_child() -> None:
    world = _World(bind_after_s=200.0)  # the child needs 200 s; the watchdog alerts after 3 probes of 60 s
    guard, watchdog, crumbs = _tray(world)
    first = guard.pid
    for _ in range(3):
        world.now += 60.0
        watchdog.tick()
    assert list(world.alive) == [first] and world.stopped == []
    assert any("still starting" in line for line in crumbs)
    world.now += 60.0
    assert watchdog.tick() is True  # the child bound: the watchdog recovers on the one copy
    assert list(world.alive) == [first]


def test_a_second_spawn_is_refused_while_the_first_child_is_alive_and_starting() -> None:
    world = _World(bind_after_s=10_000.0)
    guard, _watchdog, crumbs = _tray(world)
    world.now += 100.0
    assert guard.respawn(world.spawn) is RespawnOutcome.STARTING
    assert len(world.alive) == 1 and "respawn refused" in crumbs[-1]


def test_a_child_that_never_binds_is_stopped_before_its_replacement_spawns() -> None:
    world = _World(bind_after_s=10_000.0)
    guard, watchdog, _crumbs = _tray(world)
    first = guard.pid
    for _ in range(8):  # 480 s: past the 300 s startup window, so the wedged-or-dead branch runs
        world.now += 60.0
        watchdog.tick()
    assert world.stopped == [first]
    assert list(world.alive) == [guard.pid] and guard.pid != first
