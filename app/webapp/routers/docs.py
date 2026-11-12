"""The Markdown viewer and editor: a document rendered, its source, a draft's preview and a save, each by root, scan id and item id.

Any signed-in device may view and edit. The path is rebuilt from the root's current
scan, never taken from the client, and every byte comes through
``src/docs/reader.py``. ``POST .../download`` is the one call that lets a
cloud-only file come down to this PC, one file per tap. Every write goes
through ``src/docs/writer.py``, after its version check; a draft's preview
renders the text the page sends and reads nothing from disk. Each refusal
answers with its own status and a ``reason`` the page keys on.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from app.webapp.state import POOL, ScanChanged, scanned_item_path
from src import config, store
from src.docs import reader, writer
from src.docs.locate import find_item, resolve_href
from src.docs.render import render
from src.roots import Root, resolve_roots

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/doc")

_STATUS = {"not_found": 404, "scan_changed": 409, "gone": 410, "too_large": 413, "not_document": 415,
           "unreadable": 422, "cloud_only": 423, "edit_too_large": 413, "not_utf8": 415, "not_writable": 423}
_MESSAGES = {**reader.REASON_MESSAGES, "not_found": "No such file in this scan",
             "scan_changed": "A newer scan renumbered this file: find it again in Explore"}


class _Refusal(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _refuse(reason: str) -> JSONResponse:
    return JSONResponse({"detail": _MESSAGES[reason], "reason": reason}, status_code=_STATUS[reason])


def _text(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str):
        raise HTTPException(422, "%s must be text" % key)
    return value


def _item(root_key: str, scan_id: object, item_id: object) -> tuple[Root, dict[str, Any], str, dict[str, Any]]:
    """The file a request names: its root, the root's current scan, its path rebuilt from the scan, and its index record."""
    if not isinstance(item_id, int) or isinstance(item_id, bool):
        raise _Refusal("not_found")
    try:
        root, scan, path = scanned_item_path(resolve_roots(config.load()["roots"]), root_key, scan_id, "file", item_id)
        index, lock = POOL.get(store.index_dir() / scan["index_file"])
        with lock:
            record = index.file_record(item_id)
            record["folder"] = [p["name"] for p in index.path(record["folder_id"])]
    except ScanChanged as exc:
        raise _Refusal("scan_changed") from exc
    except KeyError as exc:
        raise _Refusal("not_found") from exc
    return root, scan, path, record


def _lookup(root: Root, scan: dict[str, Any], doc_path: str, wanted: Callable[[str], bool], kind: str
            ) -> Callable[[str], tuple[Any, str]]:
    """A renderer lookup: a relative address in the document to the scanned file it names, or why it has none."""
    index, lock = POOL.get(store.index_dir() / scan["index_file"])

    def find(href: str) -> tuple[Any, str]:
        path = resolve_href(root.path, doc_path, href)
        if path is None:
            return None, "Points outside this folder tree"
        if not wanted(path):
            return None, "Only Markdown files open here" if kind == "link" else "Not an image this viewer shows"
        with lock:
            hit = find_item(root.path, index, path)
        if hit is None:
            return None, "Not in the last scan"
        return {"root": root.key, "scan_id": scan["id"], "id": hit["id"], "online": hit["online"]}, ""

    return find


def _document(root_key: str, scan_id: object, item_id: object, download: bool) -> Any:
    try:
        root, scan, path, record = _item(root_key, scan_id, item_id)
        data = reader.read_document(path, record["online"], download)
    except _Refusal as exc:
        return _refuse(exc.reason)
    except reader.Refused as exc:
        return _refuse(exc.reason)
    text = data.decode("utf-8-sig", errors="replace")
    body = render(text, _lookup(root, scan, path, reader.is_document, "link"), _lookup(root, scan, path, reader.is_image, "image"))
    return {"name": record["name"], "root": root.key, "scan_id": scan["id"], "id": record["id"], "size": len(data),
            "mtime": record["mtime"], "folder": record["folder"], **body}


def _image(root_key: str, scan_id: object, item_id: object, download: bool) -> Response:
    try:
        _root, _scan, path, record = _item(root_key, scan_id, item_id)
        data = reader.read_image(path, record["online"], download)
    except _Refusal as exc:
        return _refuse(exc.reason)
    except reader.Refused as exc:
        return _refuse(exc.reason)
    return Response(data, headers=reader.response_headers("." + str(record["ext"])))


@router.get("")
def document(root: str, scan: int, id: int = Query(...)) -> Any:  # noqa: A002 - the page's own query name
    """One Markdown file rendered: ``{name, folder, html, outline, ...}``, or a refusal with its ``reason``."""
    return _document(root, scan, id, download=False)


@router.post("/download")
def document_download(body: dict[str, Any] = Body(...)) -> Any:
    """The "Download and open" tap: the same answer, letting a cloud-only file come down to this PC first."""
    return _document(str(body.get("root")), body.get("scan_id"), body.get("id"), download=True)


@router.get("/image")
def image(root: str, scan: int, id: int = Query(...)) -> Response:  # noqa: A002
    """An image beside a document, by its own id."""
    return _image(root, scan, id, download=False)


@router.post("/image/download")
def image_download(body: dict[str, Any] = Body(...)) -> Response:
    """A cloud-only image's own Download tap."""
    return _image(str(body.get("root")), body.get("scan_id"), body.get("id"), download=True)


@router.get("/source")
def source(root: str, scan: int, id: int = Query(...)) -> Any:  # noqa: A002
    """The editor's text and the version a save is checked against, or why this file opens read-only."""
    try:
        _root, _scan, path, record = _item(root, scan, id)
        return writer.source(path, record["online"])
    except _Refusal as exc:
        return _refuse(exc.reason)
    except reader.Refused as exc:
        return _refuse(exc.reason)


@router.post("/render")
def render_draft(body: dict[str, Any] = Body(...)) -> Any:
    """A draft's preview: the page's own text rendered with the document's links and images; nothing is read from disk."""
    text = _text(body, "text")
    if len(text.encode("utf-8")) > reader.DOC_MAX_BYTES:
        return _refuse("too_large")
    try:
        root, scan, path, _record = _item(str(body.get("root")), body.get("scan_id"), body.get("id"))
    except _Refusal as exc:
        return _refuse(exc.reason)
    return render(text, _lookup(root, scan, path, reader.is_document, "link"), _lookup(root, scan, path, reader.is_image, "image"),
                  lines=True)


@router.post("/save")
def save(body: dict[str, Any] = Body(...)) -> Any:
    """Save the editor's text when the file is still at the version it opened; a changed file answers ``conflict``
    with its current text and version, and ``overwrite`` (the confirmed "Overwrite theirs") backs that copy up first."""
    text, version = _text(body, "text"), _text(body, "version")
    try:
        _root, _scan, path, record = _item(str(body.get("root")), body.get("scan_id"), body.get("id"))
        new_version = writer.save(path, text, version, record["online"], overwrite=body.get("overwrite") is True)
    except _Refusal as exc:
        return _refuse(exc.reason)
    except reader.Refused as exc:
        return _refuse(exc.reason)
    except writer.Conflict as exc:
        return JSONResponse({"detail": writer.CONFLICT_MESSAGE, "reason": "conflict", "text": exc.text, "version": exc.version},
                            status_code=409)
    return {"version": new_version}
