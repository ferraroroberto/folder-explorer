"""The email run end to end on an invented tree: manifests, the lock, scoped runs, the CLI and the API."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.cli import email_batch
from app.cli.__main__ import main as cli
from app.webapp.routers import emails as email_routes
from app.webapp.server import app
from app.webapp.state import EMAILS
from src import config, scan_runner, store
from src.email import indexer, manifest, subject_audit
from src.email.schema import own_index_path
from src.index import Index, open_index
from src.scanner import exclude_keys, scan_to_index
from tests.conftest import write_config
from tests.test_email_stage import FakeReader

LOCAL = ("127.0.0.1", 50000)


def mail_tree(base: Path) -> Path:
    """An invented drive with two email folders' worth of .msg files and an excluded folder."""
    for rel in ("Mail/Clients/001 - Kickoff.msg", "Mail/Clients/002 - Budget.msg", "Mail/Suppliers/003 - Invoice.msg",
                "Mail/Old/004 - Skipped.msg", "Mail/Clients/notes.txt", "Photos/a.jpg"):
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"invented")
    return base


@pytest.fixture()
def setup(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    drive = mail_tree(isolated / "Drive")
    write_config(isolated, roots=[str(drive)], exclude=[str(drive / "Mail" / "Old")],
                 email={"roots": [str(drive / "Mail")], "download_budget_mb": 1})
    monkeypatch.setattr("src.email.settings.reader_installed", lambda: True)
    monkeypatch.setattr(indexer, "read_facts", FakeReader())
    EMAILS.close()
    yield drive
    EMAILS.close()


def indexed_names() -> list[str]:
    conn = open_index(own_index_path())
    try:
        return sorted(r[0] for r in conn.execute("SELECT filename FROM emails"))
    finally:
        conn.close()


def test_exclusions_hold_at_the_walk_and_in_the_manifest(setup: Path, tmp_path: Path) -> None:
    keys = exclude_keys([str(setup / "Mail" / "Old")])
    counters = scan_to_index(str(setup), tmp_path / "x.sqlite3", exclude=keys)
    assert counters.excluded == 1
    ix = Index(open_index(tmp_path / "x.sqlite3"))
    assert ix.find_folder(["Mail", "Old"]) is None and ix.find_folder(["Mail", "Clients"]) is not None
    from_index = manifest.from_index(ix, str(setup), str(setup / "Mail"))
    walked = manifest.walk(str(setup / "Mail"), exclude=keys)
    names = sorted(Path(f.path).name for f in walked.files)
    assert names == sorted(Path(f.path).name for f in from_index.files) == ["001 - Kickoff.msg", "002 - Budget.msg", "003 - Invoice.msg"]
    assert from_index.complete and walked.complete


def test_a_full_run_reads_the_scan_index_not_the_disk(setup: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scan_runner.run_scans()
    monkeypatch.setattr(indexer, "walk", lambda *a, **k: pytest.fail("listed the disk again"))
    result = indexer.run(read=FakeReader())
    assert result["status"] == "ok" and result["counts"]["new"] == 3
    assert indexed_names() == ["001 - Kickoff.msg", "002 - Budget.msg", "003 - Invoice.msg"]
    assert store.email_runs(1)[0]["message"].startswith("3 new")
    # A mail filed after the scan reaches the index only through a fresh listing.
    filed = setup / "Mail" / "Clients" / "005 - Filed today.msg"
    filed.write_bytes(b"invented")
    assert indexer.run(read=FakeReader())["counts"]["new"] == 0
    monkeypatch.setattr(indexer, "walk", manifest.walk)
    assert indexer.run(read=FakeReader(), fresh=True)["counts"]["new"] == 1
    assert cli(["email", "index", "--fresh"]) == 0


def test_a_scoped_run_lists_only_its_folder(setup: Path) -> None:
    result = indexer.run(str(setup / "Mail" / "Suppliers"), read=FakeReader())
    assert result["counts"]["files"] == 1 and indexed_names() == ["003 - Invoice.msg"]
    with pytest.raises(indexer.UnknownFolder):
        indexer.run(str(setup / "Photos"))


def test_the_subject_audit_and_fix_cut_names_refuse_a_folder_outside_the_email_folders(
    setup: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    listed: list[Any] = []
    monkeypatch.setattr(indexer, "manifests", lambda *a, **k: listed.append(a) or [])
    outside = str(setup / "Photos")
    with pytest.raises(indexer.UnknownFolder):
        subject_audit.run(outside)
    assert cli(["email", "subjects", "--root", outside]) == 3
    monkeypatch.setattr(email_batch, "load_config", config.load)
    capsys.readouterr()
    assert email_batch.main(["fix-cut-names", "--root", outside]) == email_batch.EXIT_CANNOT_START
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "bad_input"
    assert listed == []
    held = indexer.lock()
    held.acquire()  # the refusals above left the email lock free
    held.release()
    assert subject_audit.run(str(setup / "Mail" / "Clients")).files == 0
    assert len(listed) == 1


def test_busy_is_its_own_outcome(setup: Path) -> None:
    held = indexer.lock()
    held.acquire()
    try:
        started = time.monotonic()
        with pytest.raises(indexer.EmailBusy):
            indexer.run(wait_s=0.6, read=FakeReader())
        assert time.monotonic() - started >= 0.5
        assert cli(["email", "index"]) == 2
    finally:
        held.release()


def test_cli_exit_codes(setup: Path, isolated: Path, capsys: pytest.CaptureFixture[str]) -> None:
    capsys.readouterr()
    assert cli(["email", "config", "--json"]) == 0
    conf = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert conf == {"roots": [str(setup / "Mail")], "db": str(own_index_path()), "own": True}
    assert cli(["email", "index", "--root", str(setup / "Photos")]) == 3
    assert cli(["email", "index"]) == 0
    assert cli(["email", "status"]) == 0
    write_config(isolated, roots=[str(setup)], email={"index_path": str(isolated / "other.db"), "roots": [str(setup / "Mail")]})
    assert cli(["email", "index"]) == 4
    assert cli(["email", "import", "--from", str(isolated / "missing.db")]) == 1


def test_the_api_starts_runs_and_reports_them(setup: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[Any] = []
    monkeypatch.setattr(email_routes.indexer, "spawn",
                        lambda trigger, scope=None, fresh=False: started.append((scope, fresh)))
    indexer.run(read=FakeReader(fail=("002 - Budget.msg",)))
    with TestClient(app, client=LOCAL) as c:
        body = c.get("/api/email/status").json()
        assert body["ready"] and body["indexed"] == 2 and body["failed"] == 1 and body["runs"][0]["status"] == "ok"
        assert c.get("/api/email/failures").json()["failures"][0]["path"].endswith("002 - Budget.msg")
        assert c.post("/api/email/index", json={}).status_code == 202
        assert c.post("/api/email/index", json={"root": str(setup / "Mail" / "Clients")}).status_code == 202
        assert c.post("/api/email/index", json={"root": str(setup / "Photos")}).status_code == 400
        # "Index emails now" lists the folders afresh, so a mail filed since the last scan is read too.
        assert started == [(None, True), (str(setup / "Mail" / "Clients"), False)]
        held = indexer.lock()
        held.acquire()
        try:
            assert c.post("/api/email/index", json={}).status_code == 409
        finally:
            held.release()
        status = c.get("/api/search/status").json()
        assert status["emails"]["ready"] and status["emails"]["count"] == 2


def test_a_running_row_carries_live_progress_through_the_api(setup: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scan_runner.run_scans()
    monkeypatch.setattr("src.email.stage.PROGRESS_EVERY_S", 0.0)
    live: list[dict[str, Any]] = []
    with TestClient(app, client=LOCAL) as c:
        inner = FakeReader()

        def read(path: str, preview: int) -> dict[str, Any]:
            if len(inner.reads) == 1:
                live.append(c.get("/api/email/status").json())
            return inner(path, preview)

        result = indexer.run(read=read)
        done = c.get("/api/email/status").json()
    assert result["counts"]["to_index"] == 3
    during = live[0]["runs"][0]
    assert during["status"] == "running" and during["counts"]["to_index"] == 3 and during["counts"]["done"] == 1
    assert done["runs"][0]["status"] == "ok" and done["runs"][0]["counts"]["done"] == 3
    # A saved cap is reported as the card needs it; the fixture saved 1 MB.
    assert during and done["budget_mb"] == 1


def test_a_config_without_a_budget_has_no_limit_in_the_status(isolated: Path, setup: Path) -> None:
    write_config(isolated, roots=[str(setup)], email={"roots": [str(setup / "Mail")]})
    with TestClient(app, client=LOCAL) as c:
        assert c.get("/api/email/status").json()["budget_mb"] is None


def test_spawn_passes_fresh_to_the_child(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(indexer.subprocess, "Popen", lambda cmd, **kw: seen.append(cmd))
    indexer.spawn("app", fresh=True)
    indexer.spawn("app", "X")
    assert seen[0][-1] == "--fresh" and "--fresh" not in seen[1]
