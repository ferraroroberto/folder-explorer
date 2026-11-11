"""The readers of the run state: a row and a lock that disagree are answered the same way on every route."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.webapp.server import app
from app.webapp.state import EMAILS
from src import runtime_files, scan_runner, store
from src.email import indexer
from src.email.schema import open_writer, own_index_path
from tests.conftest import write_config
from tests.fixture_tree import build_tree

LOCAL = ("127.0.0.1", 50000)


@pytest.fixture()
def drive(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An invented drive with an email folder, the email part set up on this app's own index."""
    root = build_tree(isolated / "Invented drive")
    mail = root / "Mail"
    mail.mkdir()
    write_config(isolated, roots=[str(root)], email={"roots": [str(mail)]})
    monkeypatch.setattr("src.email.settings.reader_installed", lambda: True)
    EMAILS.close()
    yield root
    EMAILS.close()


def _unreadable_locks(monkeypatch: pytest.MonkeyPatch) -> None:
    real = runtime_files.open_file

    def open_file(path: Any, *args: Any, **kwargs: Any) -> Any:
        if str(path).endswith(".lock"):
            raise PermissionError("invented: the lock file is not readable")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(runtime_files, "open_file", open_file)


def test_a_dead_running_scan_row_is_interrupted_on_the_scan_route(drive: Path) -> None:
    store.begin_scan("x", "invented", "cli")
    with TestClient(app, client=LOCAL) as c:
        body = c.get("/api/scan").json()
    assert body["scanning"] is False and body["running"] == []
    assert body["state"] == "interrupted" and body["reason"]


def test_a_live_scan_is_running_on_the_scan_route(drive: Path) -> None:
    store.begin_scan("x", "invented", "cli")
    with scan_runner.lock(), TestClient(app, client=LOCAL) as c:
        body = c.get("/api/scan").json()
    assert body["scanning"] is True and body["state"] == "running" and len(body["running"]) == 1


def test_an_unreadable_scan_lock_is_unknown_never_idle_or_running(drive: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store.begin_scan("x", "invented", "cli")
    _unreadable_locks(monkeypatch)
    with TestClient(app, client=LOCAL) as c:
        body = c.get("/api/scan").json()
        roots = c.get("/api/roots").json()
    assert body["scanning"] is False and body["running"] == [] and body["state"] == "unknown" and body["reason"]
    assert roots["scanning"] is False and roots["state"] == "unknown"


def test_each_root_carries_its_state(drive: Path) -> None:
    [result] = scan_runner.run_scans()
    with TestClient(app, client=LOCAL) as c:
        [root] = c.get("/api/roots").json()["roots"]
    assert root["key"] == result["root"] and root["state"] == "ok" and root["reason"] is None


def test_a_dead_running_email_run_is_interrupted_on_the_status_route(drive: Path) -> None:
    store.begin_email_run("app", "all")
    with TestClient(app, client=LOCAL) as c:
        body = c.get("/api/email/status").json()
    assert body["running"] is False and body["state"] == "interrupted" and body["reason"]
    assert body["runs"][0]["status"] == "running" and body["runs"][0]["state"] == "interrupted"
    assert body["runs"][0]["live"] is False and body["runs"][0]["attention"] is True


def test_a_live_email_run_is_running_on_the_status_route(drive: Path) -> None:
    store.begin_email_run("app", "all")
    with indexer.lock(), TestClient(app, client=LOCAL) as c:
        body = c.get("/api/email/status").json()
    assert body["running"] is True and body["state"] == "running" and body["runs"][0]["state"] == "running"
    assert body["runs"][0]["live"] is True and body["runs"][0]["attention"] is False


def test_an_unreadable_email_lock_is_unknown(drive: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _unreadable_locks(monkeypatch)
    with TestClient(app, client=LOCAL) as c:
        body = c.get("/api/email/status").json()
    assert body["running"] is False and body["state"] == "unknown" and body["reason"]


def test_v1_tells_a_failed_first_scan_from_never_scanned(drive: Path) -> None:
    with TestClient(app, client=LOCAL) as c:
        never = c.get("/api/v1/search/status").json()["folders"]
        scan_id = store.begin_scan("x", str(drive), "cli")
        store.finish_scan(scan_id, store.STATUS_FAILED, {}, None, "invented: the drive vanished")
        failed = c.get("/api/v1/search/status").json()["folders"]
        route = c.get("/api/v1/search/folders", params={"q": "garden"})
    assert never["state"] == "not-configured" and never["run"] == "idle" and "failed" not in never["reason"]
    assert failed["state"] == "not-configured" and failed["run"] == "failed"
    assert "failed" in failed["reason"] and "the drive vanished" in failed["reason"]
    assert route.status_code == 503 and route.json()["reason"] == failed["reason"]


def test_v1_reads_a_first_scan_that_is_running_as_indexing(drive: Path) -> None:
    store.begin_scan("x", str(drive), "cli")
    with scan_runner.lock(), TestClient(app, client=LOCAL) as c:
        folders = c.get("/api/v1/search/status").json()["folders"]
    assert folders["state"] == "indexing" and folders["run"] == "running" and folders["scanning"] is True


def test_v1_reads_the_first_email_build_as_indexing_until_a_run_finished_ok(drive: Path) -> None:
    open_writer(own_index_path()).close()  # the index file exists from the start of the first run
    run_id = store.begin_email_run("app", "all")
    with TestClient(app, client=LOCAL) as c:
        with indexer.lock():
            building = c.get("/api/v1/search/status").json()["emails"]
            hit = c.get("/api/v1/search/emails", params={"q": "budget"})
        assert building["state"] == "indexing" and building["run"] == "running"
        assert hit.status_code == 503 and hit.json()["state"] == "indexing"

        died = c.get("/api/v1/search/status").json()["emails"]
        assert died["state"] == "not-configured" and died["run"] == "interrupted" and "stopped" in died["reason"]

        store.finish_email_run(run_id, store.STATUS_FAILED, {}, "invented: the reader broke")
        failed = c.get("/api/v1/search/status").json()["emails"]
        assert failed["state"] == "not-configured" and failed["run"] == "failed" and "the reader broke" in failed["reason"]

        store.finish_email_run(store.begin_email_run("app", "all"), store.STATUS_OK, {}, "0 new")
        ready = c.get("/api/v1/search/status").json()["emails"]
        assert ready["state"] == "ok" and ready["run"] == "ok" and ready["reason"] is None


def test_v1_trusts_an_imported_index_that_no_run_built(drive: Path) -> None:
    open_writer(own_index_path()).close()
    with TestClient(app, client=LOCAL) as c:
        emails = c.get("/api/v1/search/status").json()["emails"]
    assert emails["state"] == "ok" and emails["run"] == "idle"
