"""The Outlook macro's entry end to end over a fake Outlook layer: hand-off, the sheet's filing, the fallback window.

No COM, no Outlook, no live mailbox: the bridge's COM calls and the Outlook
client are replaced, the app runs in a TestClient, and the server's child runs
``app.outlook`` in this process. Invented data only.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import outlook
from app.webapp.routers import archive as archive_routes
from app.webapp.server import app
from src import config
from src.archive_window.finder import Finder
from src.email import archive_sheet
from src.email import body as child
from src.email.archive_sheet import (
    ERR_GONE,
    ERR_NO_COM,
    ERR_NO_ROOT,
    ERR_OUTLOOK,
    ERR_TIMEOUT,
    MESSAGES,
)
from src.explorer_window import ExplorerUnavailableError
from src.outlook.mapi import OutlookUnavailableError
from tests.archive.batch_support import (
    FAKE_STORE_ID,
    FakeOutlookClient,
    _index_archived_file,
    _mail,
    _seed_index,
)
from tests.conftest import write_config
from tests.test_search_window import _scan

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
    # The setting is off here so the hand-off tests reach the page; the tests of the default turn it on.
    write_config(tmp_path, roots=[str(tmp_path)], email={"roots": [str(root)]}, macro={"prefer_window": False},
                 outlook={"add_category": True})
    cfg = config.load()
    _seed_index(cfg, root, {"Project Alpha": ["Kickoff agenda"]})
    item = _mail("kick@example.invalid", "Kickoff agenda")
    client = _LiveCopyClient([item])
    spawned: list[tuple[str, str]] = []
    shown: list[object] = []
    notices: list[tuple[str, str]] = []
    monkeypatch.setattr(outlook, "_apartment", contextlib.nullcontext)
    monkeypatch.setattr(outlook.bridge, "pywin32_installed", lambda: True)
    monkeypatch.setattr(outlook.bridge, "attach", lambda: object())
    monkeypatch.setattr(outlook.bridge, "mail_by_ids", lambda _app, entry, _store: item if entry == item.EntryID else None)
    monkeypatch.setattr(outlook.bridge, "folder_name", lambda _item: "Inbox")
    monkeypatch.setattr(outlook.bridge, "display", shown.append)
    monkeypatch.setattr(outlook, "OutlookClient", lambda mailbox=None: client)
    monkeypatch.setattr("src.email.indexer.spawn", lambda trigger, scope: spawned.append((trigger, scope)))
    monkeypatch.setattr(archive_routes, "PREPARED", archive_routes._Prepared())
    monkeypatch.setattr(archive_routes, "bridge", _in_process)
    monkeypatch.setattr(archive_routes, "bridge_file", _in_process_file)
    monkeypatch.setattr(archive_routes, "FOLLOW_UPS", archive_routes._FollowUps())
    monkeypatch.setattr("src.archive_window.show_notice", lambda title, text: notices.append((title, text)))
    # The server ranks a handed-over mail on a thread; here it ranks before the hand-off answers, so a test reads one document.
    monkeypatch.setattr(archive_routes, "_rank_later", lambda work: work())
    return SimpleNamespace(root=root, cfg=cfg, item=item, client=client, spawned=spawned, shown=shown, notices=notices)


def _child_lines(args: list[str]) -> list[dict]:
    """The server's child, run here: ``app.outlook`` with the fake layer, each document it printed."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        outlook.main(args)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def _in_process(args: list[str], timeout: float) -> dict:
    return _child_lines(args)[0]


def _in_process_file(args: list[str], timeout: float, follow_up_timeout: float, on_follow_up) -> dict:  # type: ignore[no-untyped-def]
    """``bridge_file`` run here: the save's document answers, the follow-up's reaches ``on_follow_up`` before it does."""
    lines = _child_lines(args)
    if len(lines) > 1:
        on_follow_up(lines[1])
    return lines[0]


def _route_handoff(monkeypatch: pytest.MonkeyPatch, c: TestClient) -> None:
    """The entry's POST to 127.0.0.1 lands on the TestClient, as a caller on this PC."""
    def post(url: str, doc: dict, timeout: float) -> tuple[int, dict]:
        r = c.post("/api/archive/handoff", json=doc)
        return r.status_code, r.json()
    monkeypatch.setattr(outlook, "_post_json", post)


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
        assert filed["ok"] and filed["follow_up"] and fake.item.Categories == "Filed"
        assert Path(filed["files"][0]).name == "001 - Kickoff agenda.msg" and Path(filed["files"][0]).is_file()
        assert fake.spawned == [("archive", str(Path(dest).resolve()))]
        after = c.get("/api/archive/followup", params={"token": handed["token"]}).json()
        assert after == {"done": True, "result": {"ok": True, "error": None, "categorized": True, "moved": False, "indexing": True}}
        assert c.get("/api/archive/followup", params={"token": handed["token"]}).status_code == 404
        again = c.post("/api/archive/file", json={"token": handed["token"], "folder": dest})
        assert again.status_code == 404


def test_with_the_switch_off_the_sheet_offers_no_category_and_the_server_stamps_none(fake: SimpleNamespace,
                                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = config.save({"outlook": {"add_category": False}})
    with TestClient(app, client=LOCAL) as c:
        _route_handoff(monkeypatch, c)
        c.get("/api/archive/pending")
        assert outlook.archive(cfg, fake.item.EntryID, FAKE_STORE_ID) == outlook.EXIT_OK
        handed = c.get("/api/archive/pending").json()["mail"]
        assert handed["add_category"] is False and handed["category"] == ""
        dest = handed["suggestions"][0]["folder_path"]
        filed = c.post("/api/archive/file", json={"token": handed["token"], "folder": dest, "category": "Filed"}).json()
        assert filed["ok"] and filed["category"] == "" and fake.item.Categories == ""
        after = c.get("/api/archive/followup", params={"token": handed["token"]}).json()["result"]
        assert after["ok"] and not after["categorized"]


def test_the_page_hears_the_move_to_the_archive_folder_after_the_save(fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = config.save({"outlook": {"move_to_archive": True}})
    with TestClient(app, client=LOCAL) as c:
        _route_handoff(monkeypatch, c)
        c.get("/api/archive/pending")
        assert outlook.archive(cfg, fake.item.EntryID, FAKE_STORE_ID) == outlook.EXIT_OK
        handed = c.get("/api/archive/pending").json()["mail"]
        dest = handed["suggestions"][0]["folder_path"]
        filed = c.post("/api/archive/file", json={"token": handed["token"], "folder": dest}).json()
        assert filed["ok"] and Path(filed["files"][0]).is_file()
        after = c.get("/api/archive/followup", params={"token": handed["token"]}).json()["result"]
        assert after["ok"] and after["moved"] is True
    assert fake.client.moves == [(fake.item.message_id, "Archive")] and fake.client.folders[None] == []


def test_a_move_the_page_cannot_get_is_a_failed_follow_up_and_the_file_stays(
        fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = config.save({"outlook": {"move_to_archive": True}})
    fake.client.move_should_fail = True
    with TestClient(app, client=LOCAL) as c:
        _route_handoff(monkeypatch, c)
        c.get("/api/archive/pending")
        assert outlook.archive(cfg, fake.item.EntryID, FAKE_STORE_ID) == outlook.EXIT_OK
        handed = c.get("/api/archive/pending").json()["mail"]
        dest = handed["suggestions"][0]["folder_path"]
        filed = c.post("/api/archive/file", json={"token": handed["token"], "folder": dest}).json()
        assert filed["ok"] and Path(filed["files"][0]).is_file()
        after = c.get("/api/archive/followup", params={"token": handed["token"]}).json()["result"]
        assert not after["ok"] and after["moved"] is False and after["error"]["code"] == "move_failed"
    assert fake.client.folders[None] == [fake.item]


def test_the_window_shows_a_move_that_failed_after_it_closed(fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    config.save({"outlook": {"move_to_archive": True}})
    fake.client.move_should_fail = True

    def window(doc: dict, on_file, find_folders, current_explorer=None, rank=None, progress=None) -> None:  # type: ignore[no-untyped-def]
        on_file(str(fake.root / "Project Alpha"), None, False, "auto")

    monkeypatch.setattr("src.archive_window.show_window", window)
    with TestClient(app, client=LOCAL) as c:
        _route_handoff(monkeypatch, c)
        assert outlook.archive(config.load(), fake.item.EntryID, FAKE_STORE_ID) == outlook.EXIT_OK
    assert len(fake.notices) == 1 and "wouldn't move it" in fake.notices[0][1] and "Project Alpha" in fake.notices[0][1]
    assert list((fake.root / "Project Alpha").glob("*.msg")) and fake.client.folders[None] == [fake.item]


def test_the_move_setting_saves_with_the_category_one_and_defaults_off(fake: SimpleNamespace) -> None:
    assert config.DEFAULTS["outlook"]["move_to_archive"] is False
    with TestClient(app, client=LOCAL) as c:
        for bad in ("yes", None, 1):
            assert c.post("/api/config", json={"outlook": {"move_to_archive": bad}}).status_code == 400
        saved = c.post("/api/config", json={"outlook": {"move_to_archive": True}})
        assert saved.status_code == 200 and saved.json()["config"]["outlook"]["move_to_archive"] is True
        assert c.post("/api/config", json={"outlook": {"add_category": False, "move_to_archive": False}}).status_code == 200
    assert config.load()["outlook"]["move_to_archive"] is False and config.load()["outlook"]["add_category"] is False


def test_the_setting_saves_alone_and_keeps_the_other_outlook_keys(fake: SimpleNamespace, tmp_path: Path) -> None:
    config.save({"outlook": {"add_category": False}})
    path = tmp_path / "config.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"add_category": false', '"add_category": false, "category": "Mine"'), encoding="utf-8")
    with TestClient(app, client=LOCAL) as c:
        for bad in ("yes", None, 1):
            assert c.post("/api/config", json={"outlook": {"add_category": bad}}).status_code == 400
        for bad in ({}, {"add_category": True, "category": "Other"}, {"archive_folder": "X"}):
            assert c.post("/api/config", json={"outlook": bad}).status_code == 400
        saved = c.post("/api/config", json={"outlook": {"add_category": True}})
        assert saved.status_code == 200 and saved.json()["config"]["outlook"]["add_category"] is True
    assert config.load()["outlook"]["add_category"] is True and config.load()["outlook"]["category"] == "Mine"
    assert config.DEFAULTS["outlook"]["add_category"] is False


def test_an_archived_mail_is_refused_until_the_page_asks_for_a_copy(fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[list[str]] = []
    monkeypatch.setattr(archive_routes, "bridge_file", lambda args, *rest: asked.append(args) or _in_process_file(args, *rest))
    with TestClient(app, client=LOCAL) as c:
        _route_handoff(monkeypatch, c)
        dest = str(fake.root / "Project Alpha")

        def prepared() -> str:
            c.get("/api/archive/pending")  # a page that polled is open, so the hand-off is shown there, not in the Tk window
            assert outlook.archive(fake.cfg, fake.item.EntryID, FAKE_STORE_ID) == outlook.EXIT_OK
            return c.get("/api/archive/pending").json()["mail"]["token"]

        token = prepared()
        first = c.post("/api/archive/file", json={"token": token, "folder": dest}).json()
        _index_archived_file(fake.cfg, first, message_id="kick@example.invalid", subject="Kickoff agenda")
        token = prepared()
        for extra in ({}, {"duplicate": False}, {"duplicate": "true"}, {"duplicate": 1}):
            refused = c.post("/api/archive/file", json={"token": token, "folder": dest, **extra}).json()
            assert not refused["ok"] and refused["error"]["code"] == "already_archived"
        assert not any("--duplicate" in args for args in asked)
        copy = c.post("/api/archive/file", json={"token": token, "folder": dest, "duplicate": True}).json()
        assert copy["ok"] and Path(copy["files"][0]).name == "002 - Kickoff agenda.msg"
        assert Path(first["files"][0]).is_file() and "--duplicate" in asked[-1]


def test_with_no_page_open_the_fallback_window_files_the_mail(fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def window(doc: dict, on_file, find_folders, current_explorer=None, rank=None, progress=None) -> None:  # type: ignore[no-untyped-def]
        seen["doc"], seen["find"], seen["explorer"] = doc, find_folders, current_explorer
        assert doc["ranking"] and doc["suggestions"] == [] and "ids" not in doc and "rank_input" not in doc
        suggestions = rank()
        seen["result"] = on_file(suggestions[0]["folder_path"], None, False, "auto")

    monkeypatch.setattr("src.archive_window.show_window", window)
    with TestClient(app, client=LOCAL) as c:
        _route_handoff(monkeypatch, c)
        assert outlook.archive(fake.cfg, fake.item.EntryID, FAKE_STORE_ID) == outlook.EXIT_OK
    assert seen["doc"]["ok"] and callable(seen["find"].folders) and seen["result"]["ok"]
    assert Path(seen["result"]["files"][0]).is_file() and fake.item.Categories == ""
    assert fake.spawned == [("archive", str(Path(seen["result"]["folder_path"])))] and fake.notices == []


def test_the_window_closes_on_the_save_and_a_failure_after_it_gets_its_own_notice(
        fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    def window(doc: dict, on_file, find_folders, current_explorer=None, rank=None, progress=None) -> None:  # type: ignore[no-untyped-def]
        saved = on_file(str(fake.root / "Project Alpha"), "Filed", False, "auto")
        events.append("saved" if Path(saved["files"][0]).is_file() else "not saved")
        events.append("closed")  # returning is the window's mainloop ending

    def no_index(trigger: str, scope: str) -> None:
        events.append("index")
        raise OSError("no process")

    monkeypatch.setattr("src.archive_window.show_window", window)
    monkeypatch.setattr("src.email.indexer.spawn", no_index)
    monkeypatch.setattr(fake.client, "set_category", lambda item, name: events.append("category"))
    with TestClient(app, client=LOCAL) as c:
        _route_handoff(monkeypatch, c)
        assert outlook.archive(fake.cfg, fake.item.EntryID, FAKE_STORE_ID) == outlook.EXIT_OK
    assert events == ["saved", "closed", "category", "index"], "tagging and indexing wait until the window is gone"
    assert len(fake.notices) == 1 and "indexing" in fake.notices[0][1] and "Project Alpha" in fake.notices[0][1]


# A stand-in for the ``file`` child: the save's line at once, the follow-up's once the gate file exists (or never).
_FILE_CHILD = """
import json, os, sys, time
print(json.dumps({"ok": True, "folder_path": "F", "files": ["F/001 - x.msg"], "category": "", "error": None}), flush=True)
mode, gate = sys.argv[1], sys.argv[2]
if mode == "quiet":
    sys.exit(0)
end = time.monotonic() + 20
while not os.path.exists(gate) and time.monotonic() < end:
    time.sleep(0.01)
print(json.dumps({"ok": True, "error": None, "categorized": False, "indexing": True}), flush=True)
"""


_START_CHILD = child.start_child


def _scripted_child(monkeypatch: pytest.MonkeyPatch, mode: str, gate: Path) -> None:
    monkeypatch.setattr(child, "start_child", lambda cmd: _START_CHILD([sys.executable, "-c", _FILE_CHILD, mode, str(gate)]))


def test_the_server_answers_on_the_save_and_hears_the_follow_up_after(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    gate = tmp_path / "gate"
    _scripted_child(monkeypatch, "gated", gate)
    heard: list[dict] = []
    done = threading.Event()
    doc = archive_routes.bridge_file(["file"], 30, 30, lambda after: heard.append(after) or done.set())
    assert doc["files"] == ["F/001 - x.msg"] and heard == [], "the page has its answer while the follow-up is still running"
    gate.touch()
    assert done.wait(30) and heard == [{"ok": True, "error": None, "categorized": False, "indexing": True}]


def test_a_follow_up_that_hangs_or_stops_silently_is_reported_not_confirmed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for mode, code in (("gated", ERR_TIMEOUT), ("quiet", "failed")):
        _scripted_child(monkeypatch, mode, tmp_path / "never")
        heard: list[dict] = []
        done = threading.Event()
        doc = archive_routes.bridge_file(["file"], 30, 0.5, lambda after: heard.append(after) or done.set())
        assert doc["ok"] and done.wait(30)
        assert heard[0]["ok"] is False and heard[0]["error"]["code"] == code and "aren't confirmed" in heard[0]["error"]["message"]


def test_the_follow_up_route_says_running_then_gives_the_result_once(fake: SimpleNamespace) -> None:
    follow = archive_routes.FOLLOW_UPS
    with TestClient(app, client=LOCAL) as c:
        assert c.get("/api/archive/followup", params={"token": "t1"}).status_code == 404
        follow.start("t1")
        assert c.get("/api/archive/followup", params={"token": "t1"}).json() == {"done": False}
        follow.finish("t1", {"ok": False, "error": {"code": "category_failed", "message": "no tag"}})
        assert c.get("/api/archive/followup", params={"token": "t1", "wait": 1}).json()["result"]["error"]["message"] == "no tag"
        assert c.get("/api/archive/followup", params={"token": "t1"}).status_code == 404
    with TestClient(app, client=REMOTE) as c:
        assert c.get("/api/archive/followup", params={"token": "t1"}, headers={"Authorization": "Bearer nope"}).status_code in (401, 403)


def test_a_folder_the_window_finds_takes_the_mail_like_any_filing(fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """Find a folder, then Archive here: the bridge's own search, then the same filer as every other filing."""
    (fake.root / "Clients" / "Harbour works").mkdir(parents=True)
    (fake.root.parent / "Elsewhere" / "Harbour photos").mkdir(parents=True)
    seen: dict = {}

    def window(doc: dict, on_file, folders, current_explorer=None, rank=None, progress=None) -> None:  # type: ignore[no-untyped-def]
        finder = Finder(folders, doc["roots"])
        finder.search("harbour")
        seen["found"] = [(f.name, f.fileable) for f in finder.found()]
        here = finder.current(0)
        seen["first"] = on_file(here.row.path, "Filed", False, "on")
        seen["again"] = on_file(here.row.path, None, False, "auto")
        seen["copy"] = on_file(here.row.path, None, True, "off")

    monkeypatch.setattr("src.archive_window.show_window", window)
    # The disk walk alone: its email stage would reach the spawn this fixture records for filings.
    monkeypatch.setattr("src.scan_runner.index_emails_after", lambda trigger: None)
    with TestClient(app, client=LOCAL) as c:
        _scan(c)
        _route_handoff(monkeypatch, c)
        outlook.archive(fake.cfg, fake.item.EntryID, FAKE_STORE_ID)
    dest = fake.root / "Clients" / "Harbour works"
    assert seen["found"] == [("Harbour works", True), ("Harbour photos", False)]
    first = Path(seen["first"]["files"][0])
    assert seen["first"]["ok"] and fake.item.Categories == "Filed"
    assert first.parent == dest.resolve() and re.match(r"^\d{4}-\d{2}-\d{2} - 001 - Kickoff agenda\.msg$", first.name), first.name
    assert ("archive", str(dest.resolve())) in fake.spawned
    assert not seen["again"]["ok"] and seen["again"]["error"]["code"] == "already_archived"
    assert seen["copy"]["ok"] and Path(seen["copy"]["files"][0]).name == "002 - Kickoff agenda.msg"


def _window_recorder(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Replace the Tk window with a recorder of the documents it was asked to show."""
    opened: list[dict] = []
    monkeypatch.setattr("src.archive_window.show_window",
                        lambda doc, on_file, find, current_explorer=None, rank=None, progress=None: opened.append(doc))
    return opened


def test_the_window_opens_over_an_open_page_by_default_and_nothing_is_handed_over(
        fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    assert config.DEFAULTS["macro"] == {"prefer_window": True}
    cfg = {**fake.cfg, "macro": config.DEFAULTS["macro"]}
    opened = _window_recorder(monkeypatch)
    handed: list[dict] = []
    monkeypatch.setattr(outlook, "_post_json", lambda url, doc, timeout: handed.append(doc) or (200, {"shown": True}))
    with TestClient(app, client=LOCAL) as c, caplog.at_level("INFO", logger="app.outlook"):
        c.get("/api/archive/pending")  # a page is open on this PC
        assert outlook.archive(cfg, fake.item.EntryID, FAKE_STORE_ID) == outlook.EXIT_OK
        assert c.get("/api/archive/pending").json() == {"mail": None}
    assert handed == [] and len(opened) == 1 and opened[0]["ok"]
    assert any("macro.prefer_window is on" in r.getMessage() for r in caplog.records)


def test_with_the_setting_off_an_open_page_takes_the_mail_and_the_window_stays_shut(
        fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    opened = _window_recorder(monkeypatch)
    with TestClient(app, client=LOCAL) as c:
        _route_handoff(monkeypatch, c)
        c.get("/api/archive/pending")
        assert outlook.archive(fake.cfg, fake.item.EntryID, FAKE_STORE_ID) == outlook.EXIT_OK
        assert c.get("/api/archive/pending").json()["mail"]["from_macro"]
    assert opened == []


def test_with_the_setting_off_and_no_page_open_the_window_opens(fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    opened = _window_recorder(monkeypatch)
    with TestClient(app, client=LOCAL) as c:
        _route_handoff(monkeypatch, c)
        assert outlook.archive(fake.cfg, fake.item.EntryID, FAKE_STORE_ID) == outlook.EXIT_OK
    assert len(opened) == 1


def test_the_macro_sheet_reads_and_saves_the_setting(fake: SimpleNamespace) -> None:
    with TestClient(app, client=LOCAL) as c:
        assert c.get("/api/archive/macro").json()["prefer_window"] is False
        for bad in ("yes", None, 1):
            assert c.post("/api/config", json={"macro": {"prefer_window": bad}}).status_code == 400
        assert c.post("/api/config", json={"macro": {"prefer_window": True, "other": 1}}).status_code == 400
        assert c.post("/api/config", json={"macro": {}}).status_code == 400
        saved = c.post("/api/config", json={"macro": {"prefer_window": True}})
        assert saved.status_code == 200 and saved.json()["config"]["macro"] == {"prefer_window": True}
        assert c.get("/api/archive/macro").json()["prefer_window"] is True
    assert config.load()["macro"] == {"prefer_window": True}


def test_the_window_opens_when_the_app_is_not_running(fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(*_a: object, **_k: object) -> None:
        raise ConnectionRefusedError("nothing listens")

    monkeypatch.setattr(outlook, "_post_json", refused)
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
                             ("post", "/api/archive/file"), ("get", "/api/archive/folders"), ("get", "/api/archive/subfolders"),
                             ("post", "/api/archive/explorer")):
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


def test_the_date_prefix_choice_reaches_the_child_and_a_bad_one_never_does(fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[list[str]] = []
    monkeypatch.setattr(archive_routes, "bridge_file", lambda args, *rest: asked.append(args) or _in_process_file(args, *rest))
    with TestClient(app, client=LOCAL) as c:
        _route_handoff(monkeypatch, c)
        c.get("/api/archive/pending")
        assert outlook.archive(fake.cfg, fake.item.EntryID, FAKE_STORE_ID) == outlook.EXIT_OK
        handed = c.get("/api/archive/pending").json()["mail"]
        first = handed["suggestions"][0]
        assert set(first["saved_as"]) == {"auto", "on", "off"} and "location" in first
        dest = first["folder_path"]
        for bad in ("sometimes", ["on"], 1):
            refused = c.post("/api/archive/file", json={"token": handed["token"], "folder": dest, "date_prefix": bad})
            assert refused.status_code == 400
        assert asked == []
        filed = c.post("/api/archive/file", json={"token": handed["token"], "folder": dest, "date_prefix": "on"}).json()
    assert "--date-prefix=on" in asked[-1]
    assert Path(filed["files"][0]).name == first["saved_as"]["on"]


def test_the_window_gets_the_explorer_folder_through_the_same_function(fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def window(doc: dict, on_file, find_folders, current_explorer=None, rank=None, progress=None) -> None:  # type: ignore[no-untyped-def]
        seen["answer"] = current_explorer()

    monkeypatch.setattr("src.archive_window.show_window", window)
    monkeypatch.setattr(archive_sheet, "get_current_explorer_folder", lambda: str(fake.root / "Project Alpha"))
    with TestClient(app, client=LOCAL) as c:
        _route_handoff(monkeypatch, c)
        outlook.archive(fake.cfg, fake.item.EntryID, FAKE_STORE_ID)
    assert seen["answer"] == {"ok": True, "folder_path": str((fake.root / "Project Alpha").resolve())}


def test_the_page_asks_the_server_for_the_explorer_folder_and_hears_each_failure(fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
                                                                                tmp_path: Path) -> None:
    (fake.root / "Project Alpha").mkdir(exist_ok=True)
    shown = {"path": str(fake.root / "Project Alpha")}

    def current() -> str:
        if shown["path"] == "unreachable":
            raise ExplorerUnavailableError("COM server unavailable")
        return shown["path"]

    monkeypatch.setattr(archive_sheet, "get_current_explorer_folder", current)
    with TestClient(app, client=LOCAL) as c:
        found = c.post("/api/archive/explorer", json={}).json()
        assert found["ok"] and found["folder_path"] == str((fake.root / "Project Alpha").resolve()) and "location" in found
        codes = {}
        for state, path in (("none", ""), ("cannot", "unreachable"), ("outside", str(tmp_path))):
            shown["path"] = path
            codes[state] = c.post("/api/archive/explorer", json={}).json()["error"]
    assert codes["none"]["code"] == "no_explorer_folder" and codes["cannot"]["code"] == "explorer_unavailable"
    assert codes["outside"]["code"] == "outside_email_folders"
    assert len({e["message"] for e in codes.values()}) == 3
    assert not (tmp_path / "001 - Kickoff agenda.msg").exists()


def test_the_explorer_verb_files_nothing_and_needs_pywin32(fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outlook.bridge, "pywin32_installed", lambda: False)
    assert _in_process(["explorer"], 1)["error"]["code"] == ERR_NO_COM


def test_the_page_gets_the_mail_first_and_its_folders_under_the_same_token(fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    later: list = []
    monkeypatch.setattr(archive_routes, "_rank_later", later.append)
    with TestClient(app, client=LOCAL) as c:
        _route_handoff(monkeypatch, c)
        c.get("/api/archive/pending")
        assert outlook.archive(fake.cfg, fake.item.EntryID, FAKE_STORE_ID) == outlook.EXIT_OK
        first = c.get("/api/archive/pending").json()["mail"]
        assert first["ranking"] is True and first["suggestions"] == [] and first["mail"]["subject"] == "Kickoff agenda"
        assert "rank_input" not in first and "ids" not in first
        assert c.get("/api/archive/pending").json() == {"mail": None}
        later.pop()()
        full = c.get("/api/archive/pending").json()["mail"]
    assert full["token"] == first["token"] and full["ranking"] is False
    assert full["suggestions"][0]["folder_path"] == str(fake.root / "Project Alpha") and "location" in full["suggestions"][0]


def test_a_newer_hand_off_keeps_an_older_mails_folders_off_the_page(fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    later: list = []
    monkeypatch.setattr(archive_routes, "_rank_later", later.append)
    with TestClient(app, client=LOCAL) as c:
        _route_handoff(monkeypatch, c)
        c.get("/api/archive/pending")
        outlook.archive(fake.cfg, fake.item.EntryID, FAKE_STORE_ID)
        older = c.get("/api/archive/pending").json()["mail"]["token"]
        outlook.archive(fake.cfg, fake.item.EntryID, FAKE_STORE_ID)
        newer = c.get("/api/archive/pending").json()["mail"]["token"]
        later[0]()
        assert c.get("/api/archive/pending").json() == {"mail": None}
        later[1]()
        ranked = c.get("/api/archive/pending").json()["mail"]
    assert older != newer and ranked["token"] == newer and ranked["suggestions"]


def test_a_waiting_poll_answers_the_moment_a_mail_is_handed_over(fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    with TestClient(app, client=LOCAL) as c:
        _route_handoff(monkeypatch, c)
        asked = time.monotonic()
        assert c.get("/api/archive/pending?wait=0.2").json() == {"mail": None}
        assert time.monotonic() - asked >= 0.2
        assert c.get("/api/archive/pending?wait=600").status_code == 422
        hand = threading.Timer(0.3, lambda: outlook.archive(fake.cfg, fake.item.EntryID, FAKE_STORE_ID))
        hand.start()
        asked = time.monotonic()
        handed = c.get("/api/archive/pending?wait=10").json()["mail"]
        waited = time.monotonic() - asked
        hand.join()
    assert handed["mail"]["subject"] == "Kickoff agenda" and 0.25 <= waited < 5


def test_the_sheets_own_open_reads_in_the_child_and_ranks_in_the_server(fake: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[list[str]] = []
    monkeypatch.setattr(outlook.bridge, "mail_at_hand", lambda _app: fake.item)
    monkeypatch.setattr(archive_routes, "bridge", lambda args, timeout: asked.append(args) or _in_process(args, timeout))
    with TestClient(app, client=LOCAL) as c:
        doc = c.post("/api/archive/current", json={}).json()
    assert asked == [["prepare", "--current", "--defer-ranking"]]
    assert doc["ok"] and doc["ranking"] is False and doc["token"] and "rank_input" not in doc
    assert doc["suggestions"][0]["folder_path"] == str(fake.root / "Project Alpha")
