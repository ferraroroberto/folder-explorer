"""The page's own writer of a dropped file: one module, no new server route, and nothing it sends is a path.

A file dropped from a folder no scan covers is saved by the browser through its
file handle (``app/webapp/static/localdoc/local-doc.js``). The server never receives or
builds a path for it, and gains no endpoint that takes or returns one.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.webapp.server import app

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "app" / "webapp" / "static"
LOCAL_DOC = STATIC / "localdoc" / "local-doc.js"
# What gives a page write access to a user's file: the handle's writer, or a file the reader picks to save to.
WRITES = re.compile(r"\b(createWritable|showSaveFilePicker|showOpenFilePicker|showDirectoryPicker|requestPermission)\b")
# The page's side of the drop: the only requests that carry the dropped file's name or text.
DROP_ROUTES = {"/api/doc/dropped/match", "/api/doc/dropped/render", "/api/doc/find"}


def test_only_local_doc_writes_a_file_from_the_page() -> None:
    others = sorted(p.relative_to(ROOT).as_posix() for p in STATIC.rglob("*.js") if p != LOCAL_DOC and WRITES.search(p.read_text(encoding="utf-8")))
    assert others == []
    # It writes through the handle it was given and asks for no other file.
    used = set(WRITES.findall(LOCAL_DOC.read_text(encoding="utf-8")))
    assert used == {"createWritable", "requestPermission"}


def test_the_page_asks_for_write_access_on_save_not_on_the_drop() -> None:
    source = LOCAL_DOC.read_text(encoding="utf-8")
    # The permission request is one helper, and only the save path calls it.
    assert source.count("requestPermission") == 1
    assert source.count("mayWrite(") == 2
    assert source.index("mayWrite(handle))") > source.index("async save(")


def test_the_server_has_no_route_that_takes_or_returns_a_client_path() -> None:
    routes = {path for path in app.openapi()["paths"] if path.startswith("/api/doc")}
    assert DROP_ROUTES <= routes
    assert not [p for p in routes if re.search(r"path|handle|local", p)], routes
    local = LOCAL_DOC.read_text(encoding="utf-8")
    assert re.findall(r"'/api/[^']*'", local) == ["'/api/doc/dropped/render'"]
