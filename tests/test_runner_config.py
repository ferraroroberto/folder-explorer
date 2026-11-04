"""Scan runs, the lock, the schedule, the roots and the settings."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from src import config, roots, scan_runner, store
from src.scan_lock import ScanBusy, ScanLock, is_locked
from tests.conftest import write_config
from tests.fixture_tree import FILES, build_tree


def test_run_scans_writes_a_current_index_and_history(isolated: Path) -> None:
    root = build_tree(isolated / "Invented")
    write_config(isolated, roots=[str(root)])
    [result] = scan_runner.run_scans()
    assert result["status"] == "ok" and result["files"] == len(FILES)
    current = store.current_index(result["root"])
    assert current is not None and (store.index_dir() / current["index_file"]).exists()


def test_failed_rescan_keeps_the_previous_index(isolated: Path) -> None:
    root = build_tree(isolated / "Invented")
    write_config(isolated, roots=[str(root)])
    [ok] = scan_runner.run_scans()
    good = store.current_index(ok["root"])["index_file"]
    import shutil

    shutil.rmtree(root)
    [failed] = scan_runner.run_scans()
    assert failed["status"] == "failed"
    assert store.current_index(ok["root"])["index_file"] == good
    assert store.latest_scan(ok["root"])["status"] == "failed"


def test_second_scan_is_refused_while_the_lock_is_held(isolated: Path) -> None:
    write_config(isolated, roots=[str(build_tree(isolated / "Invented"))])
    with ScanLock(scan_runner.lock().path):
        assert is_locked(scan_runner.lock().path)
        with pytest.raises(ScanBusy):
            scan_runner.run_scans()
        with pytest.raises(ScanBusy):
            scan_runner.start_background(None, "app")
        assert scan_runner.settle_if_idle() is True
    assert not is_locked(scan_runner.lock().path)


def test_unknown_root_key_is_rejected(isolated: Path) -> None:
    write_config(isolated, roots=[str(build_tree(isolated / "Invented"))])
    with pytest.raises(KeyError):
        scan_runner.run_scans(["nope"])


def test_dead_running_scan_is_settled_as_interrupted(isolated: Path) -> None:
    scan_id = store.begin_scan("x", "somewhere", "cli")
    assert scan_runner.settle_if_idle() is False
    assert store.latest_scan("x")["status"] == "interrupted"
    assert store.latest_scan("x")["id"] == scan_id


def test_schedule_due_rules() -> None:
    at = datetime(2026, 11, 3, 3, 30)
    slot = datetime(2026, 11, 3, 3, 0).timestamp()
    assert scan_runner.schedule_due("nightly", "03:00", None, at)
    assert not scan_runner.schedule_due("nightly", "03:00", slot + 60, at)
    assert scan_runner.schedule_due("nightly", "03:00", slot - 3600, at)
    assert not scan_runner.schedule_due("nightly", "04:00", None, at)
    assert scan_runner.schedule_due("hourly", "03:00", at.timestamp() - 3600, at)
    assert not scan_runner.schedule_due("hourly", "03:00", at.timestamp() - 600, at)
    assert not scan_runner.schedule_due("off", "03:00", None, at)


def test_scheduler_counts_any_recent_scan(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(isolated, schedule="hourly")
    started = []
    monkeypatch.setattr(scan_runner, "start_background", lambda keys, trigger: started.append(trigger))
    store.begin_scan("x", "somewhere", "cli")
    assert scan_runner.Scheduler().tick(datetime.now()) is False
    assert started == []


def test_config_defaults_and_validation(isolated: Path) -> None:
    cfg = config.load()
    assert cfg["remote_access"] is True and cfg["roots"] is None and cfg["schedule"] == "off"
    saved = config.save({"schedule": "nightly", "schedule_time": "02:15", "remote_access": False})
    assert (saved["schedule"], saved["schedule_time"], saved["remote_access"]) == ("nightly", "02:15", False)
    for bad in ({"schedule": "weekly"}, {"schedule_time": "25:00"}, {"remote_access": "yes"}, {"roots": [""]}, {"port": 1}, {"nope": 1}):
        with pytest.raises(ValueError):
            config.save(bad)


def test_roots_are_deduplicated_by_resolved_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cloud = tmp_path / "OneDrive"
    cloud.mkdir()
    monkeypatch.setenv("OneDrive", str(cloud))
    monkeypatch.setenv("OneDriveConsumer", str(cloud) + "\\")
    monkeypatch.delenv("OneDriveCommercial", raising=False)
    monkeypatch.setattr(roots, "fixed_drives", lambda: [str(tmp_path / "Disk A"), str(tmp_path / "Disk B")])
    resolved = roots.resolve_roots(None)
    assert [r.name for r in resolved] == ["Disk A", "Disk B", "OneDrive"]
    assert [r.kind for r in resolved] == ["local", "local", "synced"]
    assert len({r.key for r in resolved}) == 3


def test_configured_roots_keep_order_and_get_unique_keys(tmp_path: Path) -> None:
    a, b = tmp_path / "x" / "Data", tmp_path / "y" / "Data"
    resolved = roots.resolve_roots([str(a), str(b), str(a)])
    assert [r.key for r in resolved] == ["data", "data-2"]


def test_server_defaults_to_hypercorn_and_ignores_unknown_values(isolated: Path) -> None:
    assert config.load()["server"] == "hypercorn"
    write_config(isolated, server="uvicorn")
    assert config.load()["server"] == "uvicorn"
    write_config(isolated, server="gunicorn")
    assert config.load()["server"] == "hypercorn"
    with pytest.raises(ValueError):
        config.validate_update({"server": "uvicorn"})
