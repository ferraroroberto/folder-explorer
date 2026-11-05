"""The Outlook macro's entry end to end over a fake Outlook layer: hand-off, the sheet's filing, the fallback window.

No COM, no Outlook, no live mailbox: the bridge's COM calls and the Outlook
client are replaced, the app runs in a TestClient, and the server's child runs
``app.outlook`` in this process. Invented data only.
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import outlook
from app.webapp.routers import archive as archive_routes
from app.webapp.server import app
from src import config
from src.email import body as child
from src.email.archive_sheet import ERR_GONE, ERR_NO_COM, ERR_NO_ROOT, ERR_OUTLOOK, ERR_TIMEOUT, MESSAGES
from src.outlook.mapi import OutlookUnavailableError
from tests.archive.batch_support import FAKE_STORE_ID, FakeOutlookClient, _mail, _seed_index
from tests.conftest import write_config

LOCAL = ("127.0.0.1", 50000)
REMOTE = ("100.64.0.7", 50000)


class _LiveCopyClient(FakeOutlookClient):
    """The fake client, plus the lookup Find in mailbox uses."""

    def find_live_copy(self, message_id: str, archive_folder: str):
        return self.find_by_message_id(message_id, None) or self.find_by_message_id(message_id, archive_folder)


@pytest.fixture()
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """An email folder with filed mail, one mail in a fake Inbox, and the bridge pointed at them."""
    root = tmp_path / "archive"
    root.mkdir()
    write_config(tmp_path, roots=[str(tmp_path)], email={"roots": [str(root)]})
    cfg = config.load()
    _seed_index(cfg, root, {"Project Alpha": ["Kickoff agenda"]})
    item = _mail("kick@example.invalid", "Kickoff agenda")
    client = _LiveCopyClient([item])
    spawned: list[tuple[str, str]] = []
    shown: list[object] = []
    monkeypatch.setattr(outlook, "_apartment", contextlib.nullcontext)
    monkeypatch.setattr(outlook.bridge, "pywin32_installed", lambda: True)
    monkeypatch.setattr(outlook.bridge, "attach", lambda: object())
    monkeypatch.setattr(outlook.bridge, "mail_by_ids", lambda _app, entry, _store: item if entry == item.EntryID else None)
    monkeypatch.setattr(outlook.bridge, "folder_name", lambda _item: "Inbox")
    monkeypatch.setattr(outlook.bridge, "display", shown.append)
    monkeypatch.setattr(outlook, "OutlookClient", lambda mailbox=None: client)
    monkeypatch.setattr(outlook.indexer, "spawn", lambda trigger, scope: spawned.append((trigger, scope)))
    monkeypatch.setattr(archive_routes, "PREPARED", archive_routes._Prepared())
    monkeypatch.setattr(archive_routes, "bridge", _in_process)
    return SimpleNamespace(root=root, cfg=cfg, item=item, client=client, spawned=spawned, shown=shown)


def _in_process(args: list[str], timeout: float) -> dict:
    """The server's child, run here: ``app.outlook`` with the fake layer, its one document parsed."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        outlook.main(args)
    return json.loads(out.getvalue())


def _route_handoff(monkeypatch: pytest.MonkeyPatch, c: TestClient) -> None:
    """The entry's POST to 127.0.0.1 lands on the TestClient, as a caller on this PC."""
    def post(url: str, json: dict, timeout: float, verify: bool) -> SimpleNamespace:
        r = c.post("/api/archive/handoff", json=json)
        return SimpleNamespace(ok=r.status_code < 400, status_code=r.status_code, json=r.json)
    monkeypatch.setattr(outlook.requests, "post", post)


def test_the_macro_hands_the_mail_to_an_open_page_which_files_it(fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    with TestClient(app, client=LOCAL) as c:
        _route_handoff(monkeypatch, c)
        assert c.get("/api/archive/pending").json() == {"mail": None}
        assert outlook.archive(fake.cfg, fake.item.EntryID, FAKE_STORE_ID) == outlook.EXIT_OK
        handed = c.get("/api/archive/pending").json()["mail"]
        assert handed["from_macro"] and handed["mail"]["subject"] == "Kickoff agenda" and "ids" not in handed
        assert c.get("/api/archive/pending").json() == {"mail": None}
        dest = handed["suggestions"][0]["folder_path"]
        assert dest == str(fake.root / "Project Alpha")
        filed = c.post("/api/archive/file", json={"token": handed["token"], "folder": dest, "category": "Filed"}).json()
        assert filed["ok"] and filed["categorized"] and fake.item.Categories == "Filed"
        assert Path(filed["files"][0]).name == "001 - Kickoff agenda.msg" and Path(filed["files"][0]).is_file()
        assert fake.spawned == [("archive", str(Path(dest).resolve()))]
        again = c.post("/api/archive/file", json={"token": handed["token"], "folder": dest})
        assert again.status_code == 404


def test_with_no_page_open_the_fallback_window_files_the_mail(fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def window(doc: dict, on_file) -> None:  # type: ignore[no-untyped-def]
        seen["doc"] = doc
        seen["result"] = on_file(doc["suggestions"][0]["folder_path"], None)

    monkeypatch.setattr("app.archive_window.show_window", window)
    with TestClient(app, client=LOCAL) as c:
        _route_handoff(monkeypatch, c)
        assert outlook.archive(fake.cfg, fake.item.EntryID, FAKE_STORE_ID) == outlook.EXIT_OK
    assert seen["doc"]["ok"] and seen["result"]["ok"] and not seen["result"]["categorized"]
    assert Path(seen["result"]["files"][0]).is_file() and fake.item.Categories == ""


def test_the_window_opens_when_the_app_is_not_running(fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(*_a: object, **_k: object) -> None:
        raise outlook.requests.ConnectionError("nothing listens")

    monkeypatch.setattr(outlook.requests, "post", refused)
    assert outlook.hand_to_page({"ok": True}, fake.cfg) is False


def test_a_failure_reaches_the_page_in_its_own_words(fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    with TestClient(app, client=LOCAL) as c:
        _route_handoff(monkeypatch, c)
        c.get("/api/archive/pending")
        outlook.archive(fake.cfg, "entry-of-a-moved-mail", FAKE_STORE_ID)
        handed = c.get("/api/archive/pending").json()["mail"]
    assert handed["ok"] is False and handed["error"] == {"code": ERR_GONE, "message": MESSAGES[ERR_GONE]}
    assert "token" not in handed


def test_each_failure_has_its_own_message(fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def unreachable() -> None:
        raise OutlookUnavailableError("down")

    monkeypatch.setattr(outlook.bridge, "attach", unreachable)
    down = outlook._guarded(lambda: outlook.prepare(fake.cfg, None, ""))
    monkeypatch.setattr(outlook.bridge, "attach", lambda: object())
    unmounted = outlook._guarded(lambda: outlook.prepare({**fake.cfg, "email": {"roots": [str(tmp_path / "gone")]}},
                                                         fake.item.EntryID, FAKE_STORE_ID))
    monkeypatch.setattr(outlook.bridge, "pywin32_installed", lambda: False)
    no_com = outlook._guarded(lambda: outlook.prepare(fake.cfg, None, ""))
    codes = [d["error"]["code"] for d in (down, unmounted, no_com)]
    assert codes == [ERR_OUTLOOK, ERR_NO_ROOT, ERR_NO_COM]
    assert len({d["error"]["message"] for d in (down, unmounted, no_com)}) == 3
    assert "Outlook isn't reachable" in down["error"]["message"] and "No archive root is mounted" in unmounted["error"]["message"]


def test_find_in_mailbox_opens_the_live_copy(fake: SimpleNamespace) -> None:
    assert _in_process(["show", "--message-id", "kick@example.invalid"], 1) == {"ok": True, "folder": "Inbox", "mailbox": "default"}
    assert fake.shown == [fake.item]
    missing = _in_process(["show", "--message-id", "gone@example.invalid"], 1)
    assert missing["error"]["code"] == "not_in_mailbox"


def test_the_archive_routes_answer_the_pc_only(fake: SimpleNamespace) -> None:
    with TestClient(app, client=REMOTE) as c:
        for method, path in (("post", "/api/archive/current"), ("post", "/api/archive/handoff"), ("get", "/api/archive/pending"),
                             ("post", "/api/archive/file"), ("get", "/api/archive/folders")):
            kwargs = {"json": {}} if method == "post" else {}
            assert getattr(c, method)(path, headers={"Authorization": "Bearer nope"}, **kwargs).status_code in (401, 403)


def test_the_macro_text_has_no_path_and_no_module_header() -> None:
    with TestClient(app, client=LOCAL) as c:
        text = c.get("/api/archive/macro").json()["text"]
    assert text.startswith("'") and "Sub ArchiveCurrentItem()" in text and "FOLDER_EXPLORER_HOME" in text
    assert "Attribute VB_Name" not in text and ":\\" not in text
    assert "-m app.outlook archive" in text and ", 0, False" in text


def test_the_bridge_child_reports_a_hang_and_a_silent_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    def hang(cmd: list[str], timeout: float) -> None:
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(child, "run_child", hang)
    assert archive_routes.bridge(["prepare", "--current"], 1)["error"]["code"] == ERR_TIMEOUT
    monkeypatch.setattr(child, "run_child", lambda cmd, timeout: SimpleNamespace(stdout="", stderr="boom", returncode=1))
    assert "without an answer" in archive_routes.bridge(["prepare", "--current"], 1)["error"]["message"]


def test_the_bridge_is_off_where_the_instance_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(archive_routes.OUTLOOK_ENV, "off")
    monkeypatch.setattr(child, "run_child", lambda *a: pytest.fail("the bridge must not start"))
    assert archive_routes.archive_status({"email": {"roots": ["x"]}}, True) == {"ready": False, "reason": archive_routes.OFF_REASON}
    assert archive_routes.bridge(["prepare", "--current"], 1)["ok"] is False


def test_a_folder_that_looks_like_a_flag_is_read_as_a_folder(fake: SimpleNamespace) -> None:
    doc = _in_process(["file", "--entry-id=" + fake.item.EntryID, "--store-id=x", "--folder=--entry-id", "--category="], 1)
    assert doc["error"]["code"] == "outside_email_folders" and fake.item.saved_as == []


def test_the_fallback_window_lists_the_folders_and_the_mail_like_the_sheet() -> None:
    from app.archive_window import mail_line, option_lines

    doc = {"suggestions": [{"folder_path": "/archive/Mail/Clients/Alpha", "display_name": "Clients/Alpha",
                            "why": [{"label": "Same subject", "tone": "accent"}], "note": "This conversation is filed here"}]}
    assert option_lines(doc) == [("/archive/Mail/Clients/Alpha", "Clients/Alpha    Same subject · This conversation is filed here")]
    mail = {"sender": "leo@example.org", "date_sent": "2026-11-05T09:12:00", "attachment_count": 2, "where": "Inbox"}
    assert mail_line(mail) == "leo@example.org · 2026-11-05 09:12 · 2 attachments · Inbox"
