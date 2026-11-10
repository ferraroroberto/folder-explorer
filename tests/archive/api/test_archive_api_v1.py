"""The versioned archive API: the CLI's verbs over HTTP, each outcome typed, on an invented mailbox and a fake Outlook.

The route runs the CLI as a child; here the child is ``email_batch.main`` called
in this process against a fake Outlook, so the semantics under test are the
CLI's own. Nothing reaches a real mailbox.
"""

from __future__ import annotations

import json
import subprocess
import sys
import types
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from app.cli import email_batch
from app.webapp.routers import archive_v1
from app.webapp.server import app
from src import archive_api
from src.email import body as child
from src.outlook import process
from tests.archive.batch_support import (  # noqa: F401 - fixtures are used by name
    _mail,
    _no_retry_pauses,
    _seed_index,
    archive_root,
    cfg,
)
from tests.archive.test_batch_targeted import TargetedFakeClient
from tests.conftest import write_config

LOCAL = ("127.0.0.1", 50000)
FIELDS = {"schema_version", "verb", "outcome", "code", "reason", "acknowledged", "confirmed", "failed", "result"}
MAIL_A, MAIL_B = "a@example.invalid", "b@example.invalid"


class Cli:
    """The child the route spawns, replaced by the CLI's ``main`` in this process; records each argv."""

    def __init__(self, capsys: pytest.CaptureFixture[str]) -> None:
        self.capsys = capsys
        self.calls: list[list[str]] = []
        self.payloads: list[Any] = []
        self.payload_paths: list[Path] = []

    def __call__(self, argv: list[str], timeout: float) -> "subprocess.CompletedProcess[str]":
        rest = argv[3:]  # sys.executable -m app.cli
        self.calls.append(rest)
        for arg in rest:
            if arg.startswith(("--decisions=", "--items=")):
                path = Path(arg.split("=", 1)[1])
                self.payload_paths.append(path)
                self.payloads.append(json.loads(path.read_text(encoding="utf-8")))
        self.capsys.readouterr()
        code = email_batch.main(rest[2:])
        return subprocess.CompletedProcess(argv, code, self.capsys.readouterr().out, "")


@pytest.fixture
def api(cfg, archive_root, tmp_path, monkeypatch, capsys):
    """A client on this PC and the CLI stand-in, over an archive with one folder and two Inbox mails."""
    write_config(tmp_path, roots=[])
    monkeypatch.setattr(email_batch, "load_config", lambda: cfg)
    monkeypatch.setattr(email_batch, "setup_logging", lambda: None)
    monkeypatch.setitem(sys.modules, "pythoncom", types.SimpleNamespace(
        CoInitialize=lambda: None, CoUninitialize=lambda: None))
    monkeypatch.setattr(process, "windows_session_id", lambda: 1)
    monkeypatch.setattr(process, "get_active_application", lambda: object())
    _seed_index(cfg, archive_root, {"Project Alpha": ["Project Alpha kickoff", "Project Alpha budget"]})
    outlook = TargetedFakeClient([_mail(MAIL_A, "Project Alpha weekly update"), _mail(MAIL_B, "Project Beta retro")])
    monkeypatch.setattr(email_batch, "OutlookClient", lambda mailbox=None: outlook)
    cli = Cli(capsys)
    monkeypatch.setattr(child, "run_child", cli)
    with TestClient(app, client=LOCAL) as client:
        client.cli, client.outlook, client.root = cli, outlook, archive_root  # type: ignore[attr-defined]
        yield client


def _lock_is_free() -> bool:
    free = archive_v1._OUTLOOK.acquire(blocking=False)
    if free:
        archive_v1._OUTLOOK.release()
    return free


def _decision(root: Path, mail: str = MAIL_A) -> dict[str, Any]:
    return {"message_id": mail, "folder_path": str(root / "Project Alpha"), "date_prefix": "auto"}


# ------------------------------------------------------------ through the CLI ---

def test_plan_answers_the_cli_document_inside_the_envelope(api) -> None:
    resp = api.post("/api/v1/archive/plan", json={"candidates": 3})

    body = resp.json()
    assert resp.status_code == 200 and set(body) == FIELDS
    assert body["outcome"] == "done" and body["code"] is None and body["acknowledged"] is True
    assert body["result"]["verb"] == "plan" and body["result"]["schema_version"] == 1
    assert [m["message_id"] for m in body["result"]["mails"]] == [MAIL_A, MAIL_B]
    assert api.cli.calls[0][:4] == ["email", "batch", "--start-timeout=60.0", "plan"]
    assert "--candidates=3" in api.cli.calls[0] and "--folder=inbox" in api.cli.calls[0]


def test_plan_filters_become_one_argument_each(api) -> None:
    api.post("/api/v1/archive/plan", json={
        "candidates": 0, "message_ids": [MAIL_A, MAIL_B], "since": "2026-03-01", "search": ["budget", "--odd"],
        "ref": "ref-1", "folder": "sent", "mailbox": "owner"})

    flags = api.cli.calls[0][4:]
    assert flags == ["--candidates=0", "--folder=sent", "--mailbox=owner", "--message-id=" + MAIL_A,
                     "--message-id=" + MAIL_B, "--search=budget", "--search=--odd", "--since=2026-03-01", "--ref=ref-1"]


def test_plan_naming_only_absent_mails_is_not_found(api) -> None:
    resp = api.post("/api/v1/archive/plan", json={"candidates": 0, "message_ids": ["gone@example.invalid"]})

    body = resp.json()
    assert resp.status_code == 404 and body["outcome"] == "not-found" and body["code"] == "not_in_inbox"
    assert body["acknowledged"] is True and body["result"]["mails"] == []


def test_apply_files_the_mail_and_confirms_it_apart_from_acknowledging_it(api) -> None:
    resp = api.post("/api/v1/archive/apply", json={"decisions": [_decision(api.root)], "renumber": True})

    body = resp.json()
    assert resp.status_code == 200 and body["outcome"] == "done"
    assert body["acknowledged"] is True and body["confirmed"] == [MAIL_A] and body["failed"] == []
    assert "renumbered" in body["result"]
    [filed] = body["result"]["results"][0]["files"]
    assert Path(filed).is_file() and Path(filed).parent == api.root / "Project Alpha"
    assert api.cli.calls[0][4:] == ["--decisions=" + str(api.cli.payload_paths[0]), "--renumber"]
    assert api.cli.payloads == [[_decision(api.root)]]
    assert not api.cli.payload_paths[0].exists(), "the payload lives for one call only"


def test_apply_with_one_bad_mail_is_a_partial_error_that_names_it(api) -> None:
    outside = {"message_id": MAIL_B, "folder_path": str(api.root.parent / "elsewhere"), "date_prefix": "auto"}

    resp = api.post("/api/v1/archive/apply", json={"decisions": [_decision(api.root), outside]})

    body = resp.json()
    assert resp.status_code == 207 and body["outcome"] == "error" and body["code"] == "partial"
    assert body["acknowledged"] is True and body["confirmed"] == [MAIL_A]
    assert body["failed"] == [{"message_id": MAIL_B, "code": "bad_decision"}]
    assert not (api.root.parent / "elsewhere").exists(), "nothing was written outside the email folders"


def test_apply_for_a_mail_not_in_the_inbox_is_not_found(api) -> None:
    resp = api.post("/api/v1/archive/apply", json={"decisions": [_decision(api.root, "gone@example.invalid")]})

    body = resp.json()
    assert resp.status_code == 404 and body["outcome"] == "not-found" and body["confirmed"] == []
    assert body["failed"] == [{"message_id": "gone@example.invalid", "code": "not_in_inbox"}]


def test_revert_puts_the_mail_back(api) -> None:
    applied = api.post("/api/v1/archive/apply", json={"decisions": [_decision(api.root)]}).json()
    items = [{"message_id": MAIL_A, "files": applied["result"]["results"][0]["files"]}]

    resp = api.post("/api/v1/archive/revert", json={"items": items, "category": "Filed by batch"})

    body = resp.json()
    assert resp.status_code == 200 and body["outcome"] == "done" and body["confirmed"] == [MAIL_A]
    assert body["result"]["results"][0]["moved_back"] is True
    assert not Path(items[0]["files"][0]).exists()
    assert api.cli.calls[1][4:] == ["--items=" + str(api.cli.payload_paths[1]), "--category=Filed by batch"]


def test_a_run_that_cannot_start_is_refused_with_the_clis_code(api) -> None:
    resp = api.post("/api/v1/archive/plan", json={"since": "yesterday"})

    body = resp.json()
    assert resp.status_code == 422 and body["outcome"] == "refused" and body["code"] == "bad_input"
    assert "--since" in body["reason"] and body["acknowledged"] is True


def test_an_unknown_mailbox_is_not_found(api) -> None:
    resp = api.post("/api/v1/archive/plan", json={"mailbox": "nobody"})

    assert resp.status_code == 404 and resp.json()["outcome"] == "not-found"
    assert resp.json()["code"] == "mailbox_unknown"


def test_outlook_unreachable_is_refused_so_nothing_reads_as_filed(api, monkeypatch) -> None:
    from src.outlook.mapi import OutlookUnavailableError

    def down(timeout: float) -> None:
        raise OutlookUnavailableError("Outlook did not answer")

    monkeypatch.setattr(api.outlook, "ensure_running", down, raising=False)

    resp = api.post("/api/v1/archive/apply", json={"decisions": [_decision(api.root)]})

    body = resp.json()
    assert resp.status_code == 503 and body["outcome"] == "refused" and body["code"] == "outlook_unavailable"
    assert body["confirmed"] == []


# ----------------------------------------------------------------- the edges ---

def test_a_body_with_an_unknown_field_is_a_422_before_any_child_runs(api) -> None:
    resp = api.post("/api/v1/archive/apply", json={"decisions": [], "dry_run": True})

    assert resp.status_code == 422 and api.cli.calls == []


def test_only_this_pc_may_archive(api, monkeypatch) -> None:
    monkeypatch.setattr(archive_v1, "is_pc_itself", lambda _request: False)

    for path, payload in (("plan", {}), ("apply", {"decisions": []}), ("revert", {"items": []}),
                          ("index", {"root": "x"})):
        resp = api.post("/api/v1/archive/" + path, json=payload)
        assert resp.status_code == 403 and resp.json()["code"] == "not_this_pc" and resp.json()["acknowledged"] is False
    assert api.cli.calls == []


def test_an_instance_with_outlook_off_never_spawns_the_archiver(api, monkeypatch) -> None:
    monkeypatch.setenv("FOLDER_EXPLORER_OUTLOOK", "off")

    resp = api.post("/api/v1/archive/apply", json={"decisions": [_decision(api.root)]})

    assert resp.status_code == 503 and resp.json()["code"] == "outlook_off" and api.cli.calls == []


def test_a_second_call_while_one_runs_is_busy(api, monkeypatch) -> None:
    seen: list[int] = []

    def reentrant(argv: list[str], timeout: float) -> "subprocess.CompletedProcess[str]":
        seen.append(api.post("/api/v1/archive/plan", json={}).status_code)
        return subprocess.CompletedProcess(argv, 0, json.dumps({"schema_version": 1, "verb": "plan", "mails": []}), "")

    monkeypatch.setattr(child, "run_child", reentrant)

    first = api.post("/api/v1/archive/plan", json={})

    assert seen == [409] and first.status_code == 200
    assert _lock_is_free()


@pytest.mark.parametrize("fail, code, status", [
    (subprocess.TimeoutExpired("x", 1), "timeout", 504),
    (OSError("no interpreter"), "spawn_failed", 502),
])
def test_a_child_that_never_answers_is_unacknowledged(api, monkeypatch, fail, code, status) -> None:
    def broken(argv: list[str], timeout: float) -> None:
        raise fail

    monkeypatch.setattr(child, "run_child", broken)

    resp = api.post("/api/v1/archive/apply", json={"decisions": [_decision(api.root)]})

    body = resp.json()
    assert resp.status_code == status and body["outcome"] == "error" and body["code"] == code
    assert body["acknowledged"] is False and body["confirmed"] == [] and body["result"] is None
    assert _lock_is_free(), "the lock was freed"


@pytest.mark.parametrize("stdout, code", [("", "no_answer"), ("Traceback...", "no_answer"), ("[1]", "no_answer"),
                                          ('{"schema_version": 2}', "schema_mismatch")])
def test_output_that_is_not_the_document_is_unacknowledged(api, monkeypatch, stdout, code) -> None:
    monkeypatch.setattr(child, "run_child", lambda argv, timeout: subprocess.CompletedProcess(argv, 1, stdout, "boom"))

    body = api.post("/api/v1/archive/plan", json={}).json()

    assert body["outcome"] == "error" and body["code"] == code and body["acknowledged"] is False


# ----------------------------------------------------------------- the index ---

@pytest.mark.parametrize("exit_code, outcome, status, code", [
    (0, "done", 200, None),
    (2, "refused", 409, "busy"),
    (3, "refused", 422, "not_in_email_folder"),
    (4, "refused", 503, "email_index_not_set_up"),
    (1, "error", 500, "failed"),
])
def test_index_answers_by_the_exit_code_of_the_scoped_reindex(api, monkeypatch, exit_code, outcome, status, code) -> None:
    calls: list[tuple[list[str], float]] = []

    def run(argv: list[str], timeout: float) -> "subprocess.CompletedProcess[str]":
        calls.append((argv[3:], timeout))
        return subprocess.CompletedProcess(argv, exit_code, "", "")

    monkeypatch.setattr(child, "run_child", run)

    resp = api.post("/api/v1/archive/index", json={"root": str(api.root / "Project Alpha"), "wait": 30})

    body = resp.json()
    assert resp.status_code == status and body["outcome"] == outcome and body["code"] == code
    assert calls == [(["email", "index", "--root=" + str(api.root / "Project Alpha"), "--wait=30.0"],
                      archive_v1.INDEX_TIMEOUT_S + 30)]


def test_index_without_a_root_is_a_422(api) -> None:
    assert api.post("/api/v1/archive/index", json={"root": ""}).status_code == 422


# ------------------------------------------------------------ the pure contract ---

def _doc(**fields: Any) -> dict[str, Any]:
    return {"schema_version": 1, **fields}


@pytest.mark.parametrize("make, outcome, status", [
    (lambda: archive_api.from_document("apply", _doc(results=[]), 0), "done", 200),
    (lambda: archive_api.from_document("plan", _doc(mails=[{"message_id": "a"}], skipped=[]), 0), "done", 200),
    (lambda: archive_api.from_document("apply", _doc(error={"code": "run_failed", "message": "disk"}), 2), "error", 500),
    (lambda: archive_api.from_document("apply", _doc(error={"code": "never_seen", "message": "?"}), 2), "error", 500),
    (lambda: archive_api.from_document("apply", _doc(error={"code": "config_missing", "message": "x"}), 2), "refused", 503),
    (lambda: archive_api.from_document("apply", _doc(results=[{"message_id": "a", "ok": False}]), 0), "error", 502),
    (lambda: archive_api.from_document("revert", _doc(results=[{"message_id": "a", "ok": False,
                                                                "error": {"code": "not_in_archive_folder"}}]), 0),
     "not-found", 404),
    (lambda: archive_api.from_document("apply", ["not", "a", "document"], 0), "error", 502),
])
def test_each_child_answer_lands_in_one_outcome(make: Callable[[], Any], outcome: str, status: int) -> None:
    body, code = make()

    assert (body["outcome"], code) == (outcome, status)
    assert set(body) == FIELDS and body["schema_version"] == 1
