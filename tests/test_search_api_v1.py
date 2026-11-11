"""The versioned search API another app calls: its states, shapes, scoping and sign-in, on an invented tree and mailbox."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.webapp.routers import search_v1
from app.webapp.server import app
from app.webapp.state import EMAILS
from src import scan_runner, search_api
from src.email import indexer
from src.email.query import compile_query
from tests.conftest import write_config
from tests.email_fixture import build_email_index
from tests.fixture_tree import build_tree

LOCAL = ("127.0.0.1", 50000)
REMOTE = ("100.64.0.7", 50000)
STATE_KEYS = {"schema_version", "state", "reason"}


@pytest.fixture()
def drive(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scanned invented drive with an email folder inside it, and an email index over that folder."""
    root = build_tree(isolated / "Invented drive")
    mail = root / "Mail"
    index = build_email_index(isolated / "emails.db", mail)
    write_config(isolated, roots=[str(root)], email={"index_path": str(index), "roots": [str(mail)]})
    EMAILS.close()
    with TestClient(app, client=LOCAL) as c:
        assert c.post("/api/scan", json={}).status_code == 202
        for _ in range(100):
            if not c.get("/api/scan").json()["scanning"]:
                break
            time.sleep(0.05)
    yield root
    EMAILS.close()


def test_not_configured_is_a_state_never_an_empty_result(isolated: Path) -> None:
    write_config(isolated, roots=[])
    EMAILS.close()
    with TestClient(app, client=LOCAL) as c:
        status = c.get("/api/v1/search/status").json()
        assert status["state"] == "ok" and status["schema_version"] == 1
        assert status["folders"]["state"] == "not-configured" and status["emails"]["state"] == "not-configured"
        folders = c.get("/api/v1/search/folders", params={"q": "garden"})
        assert folders.status_code == 503 and folders.json()["state"] == "not-configured"
        assert folders.json()["reason"] and folders.json()["results"] == []
        emails = c.get("/api/v1/search/emails", params={"q": "budget"})
        assert emails.status_code == 503 and emails.json()["state"] == "not-configured" and emails.json()["hits"] == []


def test_a_first_index_being_built_reads_indexing(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = build_tree(isolated / "Invented drive")
    write_config(isolated, roots=[str(root)], email={"index_path": str(isolated / "missing.db"), "roots": [str(root)]})
    EMAILS.close()
    with TestClient(app, client=LOCAL) as c, scan_runner.lock(), indexer.lock():
        folders = c.get("/api/v1/search/folders", params={"q": "garden"})
        assert folders.status_code == 503 and folders.json()["state"] == "indexing"
        emails = c.get("/api/v1/search/emails", params={"q": "budget"})
        assert emails.status_code == 503 and emails.json()["state"] == "indexing"


def test_folders_answer_in_the_documented_shape(drive: Path) -> None:
    with TestClient(app, client=LOCAL) as c:
        status = c.get("/api/v1/search/status").json()
        assert status["folders"] == {"state": "ok", "reason": None, "roots": 1, "scanned": 1, "scanning": False, "run": "ok"}
        assert status["emails"] == {"state": "ok", "reason": None, "count": 5, "run": "idle"}
        body = c.get("/api/v1/search/folders", params={"q": "garden"}).json()
        assert set(body) == STATE_KEYS | {"query", "results", "total", "truncated", "needs_rescan", "missing", "page"}
        assert body["state"] == "ok" and body["page"] == "/#search?mode=folders&q=garden"
        assert {r["name"] for r in body["results"]} == {"garden", "garden-planner", "data", "src"}
        hit = body["results"][0]
        assert tuple(hit) == search_api.FOLDER_FIELDS and hit["path"].startswith(str(drive)) and hit["depth"] >= 1
        assert c.get("/api/v1/search/folders", params={"q": "garden", "limit": 0}).status_code == 422
        nothing = c.get("/api/v1/search/folders", params={"q": "zebra-quartz"})
        assert nothing.status_code == 200 and nothing.json()["results"] == [] and nothing.json()["state"] == "ok"


def test_within_keeps_the_search_to_those_folders(drive: Path) -> None:
    with TestClient(app, client=LOCAL) as c:
        scoped = c.get("/api/v1/search/folders", params={"q": "garden", "within": str(drive / "projects")}).json()
        assert {r["name"] for r in scoped["results"]} == {"garden-planner", "data", "src"} and scoped["missing"] == []
        whole = c.get("/api/v1/search/folders", params={"q": "garden", "within": str(drive)}).json()
        assert len(whole["results"]) == 4
        both = c.get("/api/v1/search/folders",
                     params=[("q", "garden"), ("within", str(drive / "Photos")), ("within", str(drive / "Nowhere"))]).json()
        assert [r["name"] for r in both["results"]] == ["garden"] and both["missing"] == [str(drive / "Nowhere")]


def test_emails_answer_in_the_documented_shape(drive: Path) -> None:
    with TestClient(app, client=LOCAL) as c:
        body = c.get("/api/v1/search/emails", params={"q": "budget"}).json()
        assert set(body) == STATE_KEYS | {"query", "hits", "total", "notes", "facets", "marks", "page"}
        assert body["total"] == 3 and body["facets"] is None and body["marks"] == search_api.MARKS
        hit = body["hits"][0]
        assert tuple(hit) == search_api.EMAIL_FIELDS and hit["filename"].endswith(".msg") and hit["recipients"]
        assert search_api.MARKS["start"] in hit["subject"] + hit["snippet"]
        assert c.get("/api/v1/search/emails", params={"q": "budget", "facets": "true"}).json()["facets"]["flagged"] == 1
        newest = c.get("/api/v1/search/emails", params={"limit": 2}).json()
        assert newest["total"] == 5 and len(newest["hits"]) == 2
        assert c.get("/api/v1/search/emails", params={"sort": "bogus"}).status_code == 422


def test_an_unreadable_index_is_an_error_state(drive: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(path: Path) -> None:
        raise OSError("invented read failure")

    monkeypatch.setattr(search_v1.EMAILS, "get", broken)
    with TestClient(app, client=LOCAL) as c:
        res = c.get("/api/v1/search/emails", params={"q": "budget"})
        assert res.status_code == 500 and res.json()["state"] == "error" and "invented" in res.json()["reason"]


def test_a_caller_on_this_pc_needs_no_sign_in_and_others_do(drive: Path) -> None:
    with TestClient(app, client=LOCAL) as c:
        assert c.get("/api/v1/search/folders", params={"q": "garden"}).status_code == 200
    with TestClient(app, client=REMOTE) as c:
        assert c.get("/api/v1/search/folders", params={"q": "garden"}).status_code == 401


def test_a_single_letter_matches_only_itself() -> None:
    from datetime import date

    assert compile_query("a", set(), date(2026, 1, 1)).match == '"a"'
    assert compile_query("ab", set(), date(2026, 1, 1)).match == '"ab"*'


def _flagged(c: TestClient, **params: object) -> dict:
    return c.get("/api/v1/mail/flagged", params=params).json()


def test_flagged_mail_answers_in_the_documented_shape(drive: Path) -> None:
    with TestClient(app, client=LOCAL) as c:
        res = c.get("/api/v1/mail/flagged")
        body = res.json()
        assert res.status_code == 200 and set(body) == STATE_KEYS | {"mails", "total"}
        # Only flag_status 2 counts: the mail marked done (1) and the unflagged ones stay out.
        assert body["state"] == "ok" and body["reason"] is None and body["total"] == 1
        assert [m["subject"] for m in body["mails"]] == ["Q4 budget draft"]
        first = body["mails"][0]
        assert tuple(first) == search_api.FLAGGED_FIELDS
        assert first["sender"] == "Ana Lopez <ana@example.com>" and first["from_name"] == "Ana Lopez"
        assert first["from_addr"] == "ana@example.com" and first["date"] == "2025-10-14T09:12:00+02:00"
        assert first["preview"] == "Here is the revised budget for the fourth quarter."
        assert first["filename"].endswith(".msg") and first["path"].endswith(first["filename"])


def test_flagged_mail_pages_oldest_first_with_the_total_of_all(isolated: Path) -> None:
    mail = isolated / "Mail"
    index = build_email_index(isolated / "emails.db", mail)
    conn = sqlite3.connect(str(index))
    conn.execute("UPDATE emails SET flag_status = 2")
    conn.commit()
    conn.close()
    write_config(isolated, roots=[], email={"index_path": str(index), "roots": [str(mail)]})
    EMAILS.close()
    with TestClient(app, client=LOCAL) as c:
        pages = [_flagged(c, limit=2, offset=n) for n in (0, 2, 4, 6)]
        assert [p["total"] for p in pages] == [5, 5, 5, 5]
        assert [len(p["mails"]) for p in pages] == [2, 2, 1, 0]
        assert [m["subject"] for p in pages for m in p["mails"]] == [
            "Invoice 0042", "Re: budget notes", "Q4 budget draft", "Kickoff agenda", "Site visit photos"]
        assert pages[3]["state"] == "ok"
        assert c.get("/api/v1/mail/flagged", params={"limit": 0}).status_code == 422
        assert c.get("/api/v1/mail/flagged", params={"limit": 201}).status_code == 422
        assert c.get("/api/v1/mail/flagged", params={"offset": -1}).status_code == 422


def test_no_flagged_mail_is_ok_with_an_empty_list(isolated: Path) -> None:
    mail = isolated / "Mail"
    index = build_email_index(isolated / "emails.db", mail)
    conn = sqlite3.connect(str(index))
    conn.execute("UPDATE emails SET flag_status = 0")
    conn.commit()
    conn.close()
    write_config(isolated, roots=[], email={"index_path": str(index), "roots": [str(mail)]})
    EMAILS.close()
    with TestClient(app, client=LOCAL) as c:
        res = c.get("/api/v1/mail/flagged")
        assert res.status_code == 200 and res.json()["state"] == "ok" and res.json()["total"] == 0
    EMAILS.close()


def test_flagged_mail_without_an_email_setup_is_not_configured(isolated: Path) -> None:
    write_config(isolated, roots=[])
    EMAILS.close()
    with TestClient(app, client=LOCAL) as c:
        res = c.get("/api/v1/mail/flagged")
        assert res.status_code == 503 and res.json()["state"] == "not-configured"
        assert res.json()["reason"] and res.json()["mails"] == [] and res.json()["total"] == 0


def test_flagged_mail_while_the_first_index_builds_is_indexing(isolated: Path) -> None:
    root = build_tree(isolated / "Invented drive")
    write_config(isolated, roots=[str(root)], email={"index_path": str(isolated / "missing.db"), "roots": [str(root)]})
    EMAILS.close()
    with TestClient(app, client=LOCAL) as c, indexer.lock():
        res = c.get("/api/v1/mail/flagged")
        assert res.status_code == 503 and res.json()["state"] == "indexing" and res.json()["mails"] == []


def test_an_index_without_flag_status_is_not_configured_never_empty(isolated: Path) -> None:
    mail = isolated / "Mail"
    index = build_email_index(isolated / "emails.db", mail)
    conn = sqlite3.connect(str(index))
    conn.execute("ALTER TABLE emails DROP COLUMN flag_status")
    conn.commit()
    conn.close()
    write_config(isolated, roots=[], email={"index_path": str(index), "roots": [str(mail)]})
    EMAILS.close()
    with TestClient(app, client=LOCAL) as c:
        res = c.get("/api/v1/mail/flagged")
        assert res.status_code == 503 and res.json()["state"] == "not-configured"
        assert "flag_status" in res.json()["reason"] and res.json()["mails"] == []
    EMAILS.close()


def test_an_unreadable_index_is_an_error_for_flagged_mail(drive: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(path: Path) -> None:
        raise OSError("invented read failure")

    monkeypatch.setattr(search_v1.EMAILS, "get", broken)
    with TestClient(app, client=LOCAL) as c:
        res = c.get("/api/v1/mail/flagged")
        assert res.status_code == 500 and res.json()["state"] == "error" and "invented" in res.json()["reason"]
        assert res.json()["mails"] == []


def test_flagged_mail_needs_the_token_off_this_pc(drive: Path) -> None:
    with TestClient(app, client=REMOTE) as c:
        assert c.get("/api/v1/mail/flagged").status_code == 401
