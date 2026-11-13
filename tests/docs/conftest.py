"""The viewer's and editor's shared fixture: the invented Markdown notes, scanned, with each file's id."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.webapp.server import app
from tests.conftest import write_config
from tests.fixture_tree import DOCS_FOLDER, PAGES_FOLDER, add_docs, build_tree

LOCAL = ("127.0.0.1", 50000)


@pytest.fixture()
def docs(isolated: Path) -> dict[str, Any]:
    tree = add_docs(build_tree(isolated / "Invented root"))
    write_config(isolated, roots=[str(tree)])
    with TestClient(app, client=LOCAL) as c:
        assert c.post("/api/scan", json={}).status_code == 202
        for _ in range(200):
            if not c.get("/api/scan").json()["scanning"]:
                break
            time.sleep(0.05)
        root = c.get("/api/roots").json()["roots"][0]
        top = c.get("/api/folder", params={"root": root["key"]}).json()
        notes = next(it["id"] for it in top["items"] if it["name"] == DOCS_FOLDER)
        body = c.get("/api/folder", params={"root": root["key"], "folder": notes}).json()
        sub = next(it["id"] for it in body["items"] if it["name"] == "sub")
        deeper = c.get("/api/folder", params={"root": root["key"], "folder": sub}).json()
        pages_id = next(it["id"] for it in top["items"] if it["name"] == PAGES_FOLDER)
        pages = c.get("/api/folder", params={"root": root["key"], "folder": pages_id}).json()
    ids = {it["name"]: it["id"] for it in body["items"] + deeper["items"] + pages["items"] if it["kind"] == "file"}
    ids["notes.txt"] = next(it["id"] for it in top["items"] if it["name"] == "notes.txt")
    return {"root": root["key"], "path": root["path"], "scan": body["scan_id"], "ids": ids}
