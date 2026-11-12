"""The Markdown viewer: a document rendered, and the images beside it, each by root, scan id and item id.

Any signed-in device may view. The path is rebuilt from the root's current
scan, never taken from the client, and every byte comes through
``src/docs/reader.py``. ``POST .../download`` is the one call that lets a
cloud-only file come down to this PC, one file per tap. Each refusal answers
with its own status and a ``reason`` the page keys on.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse, Response

from app.webapp.state import POOL, ScanChanged, scanned_item_path
from src import config, store
from src.docs import reader
from src.docs.locate import find_item, resolve_href
from src.docs.render import render
from src.roots import Root, resolve_roots

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/doc")

_STATUS = {"not_found": 404, "scan_changed": 409, "gone": 410, "too_large": 413, "not_document": 415,
           "unreadable": 422, "cloud_only": 423}
_MESSAGES = {**reader.REASON_MESSAGES, "not_found": "No such file in this scan",
             "scan_changed": "A newer scan renumbered this file: find it again in Explore"}


class _Refusal(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _refuse(reason: str) -> JSONResponse:
    return JSONResponse({"detail": _MESSAGES[reason], "reason": reason}, status_code=_STATUS[reason])


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
