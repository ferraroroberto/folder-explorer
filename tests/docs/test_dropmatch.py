"""A dropped Markdown or HTML file that the scan has not seen exactly: edited since, or new, found by its bytes.

The page sends a SHA-256 of the dropped bytes beside the name, size and modified
time, never a path. The server compares it with candidates it takes from the
index and from a listing of scanned folders, reads each through the reader, and
opens only exactly one equal file; every other outcome renders read-only with its
reason. The tests write fixture files in a temp root and count the folders listed.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src import winfs
from src.docs import dropmatch, reader
from tests.docs.test_dropped import STAMP, _scan, client, tree  # noqa: F401 - the invented, scanned notes tree and its client
from tests.fixture_tree import DOCS_FOLDER

LOCAL = ("127.0.0.1", 50000)
MATCH = "/api/doc/dropped/match"


def _drop(data: bytes, name: str, modified: int = STAMP + 90, **over: Any) -> dict[str, Any]:
    """What the page sends for bytes it was dropped: name, size, modified time (ms) and their SHA-256."""
    return {"name": name, "size": len(data), "modified": modified * 1000, "sha256": hashlib.sha256(data).hexdigest(), **over}


def _indexed_id(c: TestClient, name: str) -> dict[str, Any]:
    root = c.get("/api/roots").json()["roots"][0]
    top = c.get("/api/folder", params={"root": root["key"]}).json()
    notes = next(it["id"] for it in top["items"] if it["name"] == DOCS_FOLDER)
    body = c.get("/api/folder", params={"root": root["key"], "folder": notes}).json()
    return {"root": root["key"], "scan_id": body["scan_id"], "id": next(it["id"] for it in body["items"] if it["name"] == name)}


def test_a_file_edited_since_the_scan_opens_as_its_indexed_copy(client: TestClient, tree: Path) -> None:
    guide = tree / DOCS_FOLDER / "guide.md"
    data = guide.read_bytes() + b"\nAdded after the scan.\n"
    guide.write_bytes(data)
    assert client.post(MATCH, json=_drop(data, "guide.md")).json() == {"match": _indexed_id(client, "guide.md")}


def test_a_new_file_in_a_scanned_folder_opens_with_a_lasting_number(client: TestClient, tree: Path) -> None:
    data = b"# Fresh idea\n\nWritten after the scan.\n"
    (tree / DOCS_FOLDER / "sub" / "fresh idea.md").write_bytes(data)
    first = client.post(MATCH, json=_drop(data, "fresh idea.md")).json()
    assert first["match"]["id"] < 0
    assert client.post(MATCH, json=_drop(data, "fresh idea.md")).json() == first
    doc = client.get("/api/doc", params={"root": first["match"]["root"], "scan": first["match"]["scan_id"], "id": first["match"]["id"]})
    assert doc.status_code == 200 and "Written after the scan" in doc.json()["html"]
    assert doc.json()["folder"][-2:] == [DOCS_FOLDER, "sub"]


def test_a_new_html_page_opens_too(client: TestClient, tree: Path) -> None:
    data = b"<html><body><p>New page</p></body></html>"
    (tree / DOCS_FOLDER / "newpage.html").write_bytes(data)
    assert client.post(MATCH, json=_drop(data, "newpage.html")).json()["match"]["id"] < 0


def test_a_file_outside_every_root_stays_read_only(client: TestClient, tree: Path) -> None:
    data = b"# Elsewhere\n"
    outside = tree.parent / "elsewhere"
    outside.mkdir()
    (outside / "elsewhere.md").write_bytes(data)
    assert client.post(MATCH, json=_drop(data, "elsewhere.md")).json() == {"match": None, "why": "not_found"}


def test_a_name_with_other_bytes_is_not_that_file(client: TestClient, tree: Path) -> None:
    guide = tree / DOCS_FOLDER / "guide.md"
    other = b"# Not the guide at all\n"
    assert client.post(MATCH, json=_drop(other, "guide.md")).json() == {"match": None, "why": "not_found"}
    assert guide.read_bytes() != other


def test_several_identical_new_copies_stay_read_only(client: TestClient, tree: Path) -> None:
    data = b"# Twin\n"
    (tree / DOCS_FOLDER / "twin.md").write_bytes(data)
    (tree / DOCS_FOLDER / "sub" / "twin.md").write_bytes(data)
    # Both are named in no index: the lookup finds them in two folders and refuses to pick one.
    assert client.post(MATCH, json=_drop(data, "twin.md")).json() == {"match": None, "why": "several"}


def test_several_identical_edited_copies_stay_read_only(client: TestClient, tree: Path) -> None:
    (tree / DOCS_FOLDER / "sub" / "guide.md").write_bytes(b"# A second guide\n")
    _scan(client)
    data = b"# Same edit\n"
    (tree / DOCS_FOLDER / "guide.md").write_bytes(data)
    (tree / DOCS_FOLDER / "sub" / "guide.md").write_bytes(data)
    assert client.post(MATCH, json=_drop(data, "guide.md")).json() == {"match": None, "why": "several"}


def test_one_of_two_same_named_files_is_picked_by_its_bytes(client: TestClient, tree: Path) -> None:
    (tree / DOCS_FOLDER / "sub" / "guide.md").write_bytes(b"# A second guide\n")
    _scan(client)
    edited = (tree / DOCS_FOLDER / "guide.md").read_bytes() + b"more\n"
    (tree / DOCS_FOLDER / "guide.md").write_bytes(edited)
    assert client.post(MATCH, json=_drop(edited, "guide.md")).json() == {"match": _indexed_id(client, "guide.md")}


def test_a_cloud_only_candidate_is_never_read(client: TestClient, tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = b"# In OneDrive only\n"
    (tree / DOCS_FOLDER / "cloud.md").write_bytes(data)

    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("a cloud-only file was opened")

    monkeypatch.setattr(winfs, "is_online_only", lambda _attrs: True)
    monkeypatch.setattr(reader, "_open_read", boom)
    assert client.post(MATCH, json=_drop(data, "cloud.md")).json() == {"match": None, "why": "not_confirmed"}


def test_a_drop_with_no_hash_only_matches_the_index(client: TestClient, tree: Path) -> None:
    guide = tree / DOCS_FOLDER / "guide.md"
    data = guide.read_bytes() + b"edit\n"
    guide.write_bytes(data)
    body = _drop(data, "guide.md")
    del body["sha256"]
    assert client.post(MATCH, json=body).json() == {"match": None, "why": "not_confirmed"}


def test_a_caller_off_this_pc_never_reaches_the_disk(client: TestClient, tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = b"# Fresh\n"
    (tree / DOCS_FOLDER / "fresh.md").write_bytes(data)

    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("a caller off this PC made the server read")

    monkeypatch.setattr("app.webapp.routers.docs.is_pc_itself", lambda _request: False)
    monkeypatch.setattr(winfs, "list_dir", boom)
    assert client.post(MATCH, json=_drop(data, "fresh.md")).json() == {"match": None, "why": "not_confirmed"}


@pytest.mark.parametrize("sha", ["", "abc", "z" * 64, "A" * 63, 5, ["a" * 64]])
def test_a_malformed_hash_is_refused(client: TestClient, sha: Any) -> None:
    assert client.post(MATCH, json={"name": "a.md", "size": 3, "modified": STAMP * 1000, "sha256": sha}).status_code == 422


def test_a_path_the_page_sends_is_never_used(client: TestClient, tree: Path) -> None:
    data = b"# Fresh\n"
    (tree / DOCS_FOLDER / "fresh.md").write_bytes(data)
    elsewhere = tree.parent / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "fresh.md").write_bytes(data)
    res = client.post(MATCH, json=_drop(data, "fresh.md", path=str(elsewhere / "fresh.md"), folder=str(elsewhere)))
    assert res.json()["match"]["id"] < 0
    assert client.get("/api/doc", params={**{"root": res.json()["match"]["root"], "scan": res.json()["match"]["scan_id"],
                                              "id": res.json()["match"]["id"]}}).json()["folder"][-1] == DOCS_FOLDER


def test_the_lookup_lists_documents_folders_first_and_stops_at_its_folder_budget(client: TestClient, tree: Path,
                                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    listed: list[str] = []
    real = winfs.list_dir

    def counting(path: str) -> Any:
        listed.append(os.path.basename(path))
        return real(path)

    monkeypatch.setattr(winfs, "list_dir", counting)
    here = b"# In a notes folder\n"
    (tree / DOCS_FOLDER / "here.md").write_bytes(here)
    far = b"# In an empty folder\n"
    (tree / "Empty folder" / "far.md").write_bytes(far)
    # Three folders hold documents; a budget of three reaches the notes folder and not the empty one.
    monkeypatch.setattr(dropmatch, "LOOKUP_FOLDERS", 3)
    assert client.post(MATCH, json=_drop(here, "here.md")).json()["match"] is not None
    listed.clear()
    assert client.post(MATCH, json=_drop(far, "far.md")).json() == {"match": None, "why": "not_found"}
    assert len(listed) <= 3


def test_the_lookup_stops_at_its_time_budget(client: TestClient, tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = b"# Late\n"
    (tree / "Empty folder" / "late.md").write_bytes(data)
    ticks = iter(range(0, 1000))
    monkeypatch.setattr(dropmatch, "_clock", lambda: float(next(ticks)))
    monkeypatch.setattr(dropmatch, "LOOKUP_SECONDS", 3.0)
    assert client.post(MATCH, json=_drop(data, "late.md")).json() == {"match": None, "why": "not_found"}


def test_every_lookup_leaves_one_log_line(client: TestClient, tree: Path, caplog: pytest.LogCaptureFixture) -> None:
    data = b"# Logged\n"
    (tree / DOCS_FOLDER / "logged.md").write_bytes(data)
    with caplog.at_level("INFO"):
        client.post(MATCH, json=_drop(data, "logged.md"))
    lines = [r.getMessage() for r in caplog.records if "drop lookup" in r.getMessage()]
    assert len(lines) == 1 and "folders" in lines[0] and "logged" not in lines[0]
