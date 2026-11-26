"""The standalone restart (`tray.bat --restart` without the shared helper) touches only this app.

`select_targets` is the whole decision of what gets ended, so it is tested against a process
table that holds this app's tray and server beside other apps' (the same `-m app.tray`
command line under another venv, a lookalike venv name, a stranger on this app's port).
`stop` is tested with the kill and the process list faked, and the enumeration helpers once
against the live process table without ending anything.
"""

from __future__ import annotations

import os
import re
import socket
import sys
from pathlib import Path

import pytest

from scripts import standalone_restart as sr

VENV = Path(r"X:\apps\folder-explorer\.venv")
OTHER_VENV = Path(r"X:\apps\other-app\.venv")
ME = 900


def _py(venv: Path, args: str, exe: str = "pythonw.exe") -> str:
    return f'"{venv}\\Scripts\\{exe}" {args}'


def _table() -> list[sr.Proc]:
    return [
        sr.Proc(10, 1, _py(VENV, "-m app.tray")),
        sr.Proc(11, 10, _py(VENV, "-m app.webapp --port 8453", "python.exe")),
        sr.Proc(12, 11, _py(VENV, "-m app.cli scan", "python.exe")),
        sr.Proc(20, 1, _py(OTHER_VENV, "-m app.tray")),
        sr.Proc(21, 20, _py(OTHER_VENV, "-m uvicorn other.server:app", "python.exe")),
        sr.Proc(30, 1, r'"X:\apps\folder-explorer\.venv-old\Scripts\pythonw.exe" -m app.tray'),
        sr.Proc(40, 1, r'"C:\Windows\System32\svchost.exe" -k netsvcs'),
        sr.Proc(41, 1, "pythonw.exe somewhere_else.py"),
        sr.Proc(ME - 1, 1, "cmd.exe /c tray.bat --restart"),
        sr.Proc(ME, ME - 1, _py(VENV, "-m scripts.standalone_restart stop", "python.exe")),
    ]


def _select(listeners: set[int], processes: list[sr.Proc] | None = None) -> list[int]:
    table = processes or _table()
    return sr.select_targets(table, listeners, VENV, sr.ancestors(table, ME))


def test_this_apps_tray_is_selected_without_its_port_owner() -> None:
    assert _select(set()) == [10]


def test_the_port_owner_under_this_venv_is_selected_with_the_tray() -> None:
    assert _select({11}) == [10, 11]


def test_an_orphaned_server_is_selected_when_its_tray_is_gone() -> None:
    table = [p for p in _table() if p.pid != 10]
    assert _select({11}, table) == [11]


def test_another_apps_tray_and_server_are_never_selected() -> None:
    assert _select({21}) == [10]
    assert 20 not in _select({20, 21})


def test_a_stranger_on_this_apps_port_is_never_selected() -> None:
    # The port is the app's, the process behind it is not: it is not under this venv.
    assert _select({40}) == [10]


def test_a_lookalike_venv_directory_does_not_match() -> None:
    assert 30 not in _select({30})


def test_a_tray_command_line_outside_any_venv_does_not_match() -> None:
    assert 41 not in _select({41})


def test_the_script_and_its_callers_are_never_selected() -> None:
    assert ME not in _select({ME})
    assert ME - 1 not in _select({ME - 1})


def test_path_case_and_slashes_do_not_matter() -> None:
    table = [sr.Proc(50, 1, 'X:/APPS/Folder-Explorer/.VENV/Scripts/pythonw.exe -m  app.tray')]
    assert sr.select_targets(table, set(), VENV, set()) == [50]


def test_a_process_table_without_this_app_selects_nothing() -> None:
    table = [p for p in _table() if p.pid in (20, 21, 40)]
    assert sr.select_targets(table, {21, 40}, VENV, set()) == []


def test_ancestors_stop_at_a_cycle_and_at_the_root() -> None:
    table = [sr.Proc(1, 2, ""), sr.Proc(2, 1, ""), sr.Proc(3, 2, "")]
    assert sr.ancestors(table, 3) == {1, 2, 3}
    assert sr.ancestors([sr.Proc(5, 0, "")], 5) == {5}


def test_stop_ends_exactly_the_selected_trees_and_waits_for_them_to_go(monkeypatch, capsys) -> None:
    ended: list[int] = []
    table = _table()

    def end_tree(pid: int) -> None:
        ended.append(pid)
        table[:] = [p for p in table if p.pid not in (10, 11, 12)]

    monkeypatch.setattr(sr, "list_processes", lambda: list(table))
    monkeypatch.setattr(sr, "listener_pids", lambda ports: {11, 21, 40} & {p.pid for p in table})
    monkeypatch.setattr(sr, "app_ports", lambda: [8453])
    monkeypatch.setattr(sr, "VENV_DIR", VENV)
    monkeypatch.setattr(sr.os, "getpid", lambda: ME)
    monkeypatch.setattr(sr, "_end_tree", end_tree)

    assert sr.stop() == 0
    assert ended == [10, 11]
    assert "stopped" in capsys.readouterr().out
    assert {p.pid for p in table} >= {20, 21, 30, 40, 41, ME}


def test_stop_with_nothing_running_ends_nothing(monkeypatch) -> None:
    table = [p for p in _table() if p.pid in (20, 21, 40)]
    monkeypatch.setattr(sr, "list_processes", lambda: list(table))
    monkeypatch.setattr(sr, "listener_pids", lambda ports: {21, 40})
    monkeypatch.setattr(sr, "app_ports", lambda: [8453])
    monkeypatch.setattr(sr, "VENV_DIR", VENV)
    monkeypatch.setattr(sr, "_end_tree", lambda pid: pytest.fail(f"ended {pid}"))
    assert sr.stop() == 0


def test_stop_reports_a_survivor_instead_of_success(monkeypatch, capsys) -> None:
    table = _table()
    monkeypatch.setattr(sr, "list_processes", lambda: list(table))
    monkeypatch.setattr(sr, "listener_pids", lambda ports: set())
    monkeypatch.setattr(sr, "app_ports", lambda: [8453])
    monkeypatch.setattr(sr, "VENV_DIR", VENV)
    monkeypatch.setattr(sr.os, "getpid", lambda: ME)
    monkeypatch.setattr(sr, "_end_tree", lambda pid: None)
    monkeypatch.setattr(sr, "STOP_TIMEOUT_S", 0.0)

    assert sr.stop() == sr.EXIT_FAILED
    assert "still running" in capsys.readouterr().out


def test_stop_that_cannot_read_the_process_list_is_not_confirmed_and_ends_nothing(monkeypatch, capsys) -> None:
    def unreadable() -> list[sr.Proc]:
        raise RuntimeError("powershell exited 1")

    monkeypatch.setattr(sr, "list_processes", unreadable)
    monkeypatch.setattr(sr, "app_ports", lambda: [8453])
    monkeypatch.setattr(sr, "_end_tree", lambda pid: pytest.fail(f"ended {pid}"))

    assert sr.stop() == sr.EXIT_NOT_CONFIRMED
    assert "NOT CONFIRMED" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("served", "expected", "code"),
    [("abc1234", "abc1234", 0), ("old0000", "abc1234", sr.EXIT_FAILED), ("abc1234", "unknown", sr.EXIT_NOT_CONFIRMED)],
)
def test_verify_compares_the_served_build_with_head(monkeypatch, served: str, expected: str, code: int) -> None:
    monkeypatch.setattr(sr, "resolve_git_sha", lambda root: expected)
    monkeypatch.setattr(sr, "_probe_urls", lambda: ["http://127.0.0.1:1/api/version"])
    monkeypatch.setattr(sr, "_served_sha", lambda url: served)
    monkeypatch.setattr(sr, "VERIFY_TIMEOUT_S", 0.0)
    assert sr.verify() == code


def test_verify_fails_when_nothing_answers(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sr, "resolve_git_sha", lambda root: "abc1234")
    monkeypatch.setattr(sr, "_probe_urls", lambda: ["http://127.0.0.1:1/api/version"])
    monkeypatch.setattr(sr, "_served_sha", lambda url: None)
    monkeypatch.setattr(sr, "VERIFY_TIMEOUT_S", 0.0)
    assert sr.verify() == sr.EXIT_FAILED
    assert "did not answer" in capsys.readouterr().out


@pytest.mark.skipif(sys.platform != "win32", reason="reads the Windows process and socket tables")
def test_the_enumeration_helpers_see_this_process_and_its_listener() -> None:
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        port = srv.getsockname()[1]
        mine = {p.pid: p for p in sr.list_processes()}[os.getpid()]
        assert sr.listener_pids([port]) == {os.getpid()}
    assert os.path.basename(sys.executable).lower() in mine.cmdline.lower()
    assert sr.listener_pids([port]) == set()


def test_tray_bat_routes_a_missing_helper_restart_to_the_standalone_restart() -> None:
    bat = (Path(__file__).resolve().parents[2] / "tray.bat").read_bytes()
    assert bat.isascii()
    text = bat.decode("ascii")
    assert 'if not exist "%TRAY_PS%" goto :standalone_restart' in text
    assert text.index(":standalone_restart") > text.index("goto :standalone_restart")
    assert "-m scripts.standalone_restart stop" in text
    assert "-m scripts.standalone_restart verify" in text
    # The helper path keeps its own invocation, ahead of both standalone labels.
    assert text.index('-m src.ps_script "%TRAY_PS%" launch') < text.index("\n:standalone_start")


def test_the_webapp_child_alone_is_not_taken_for_the_tray() -> None:
    assert sr.TRAY_PATTERN.search("pythonw.exe -m app.tray")
    assert not sr.TRAY_PATTERN.search("python.exe -m app.webapp")
    assert not sr.TRAY_PATTERN.search("python.exe -m app.tray_other")


def test_the_owned_port_comes_from_the_config(isolated: Path, capsys) -> None:
    from tests.conftest import write_config

    write_config(isolated, port=9123)
    assert sr.app_ports() == [9123]
    assert sr.main(["standalone_restart", "ports"]) == 0
    assert capsys.readouterr().out.strip() == "9123"


def test_with_no_config_the_owned_port_is_the_default(isolated: Path) -> None:
    assert sr.app_ports() == [8453]


def test_tray_bat_carries_no_port_of_its_own() -> None:
    text = (Path(__file__).resolve().parents[2] / "tray.bat").read_text(encoding="ascii")
    assert not re.search(r"OWNED_PORTS=\d", text) and "8453" not in text
    assert "-m scripts.standalone_restart ports" in text
