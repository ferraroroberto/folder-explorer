"""The document viewer and editor (Markdown, and HTML in a sandboxed frame): a document rendered, its source, a draft's preview and a save, each by root, scan id and item id.

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
import os
from typing import Any, Callable, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from app.webapp import state
from app.webapp.auth import is_pc_itself
from app.webapp.state import POOL, ScanChanged, scanned_item_path
from src import config, store, winfs
from src.docs import reader, writer
from src.docs.html_frame import frame
from src.docs.locate import find_item, resolve_href
from src.docs.render import render
from src.roots import Root, resolve_roots

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/doc")

_STATUS = {"not_found": 404, "scan_changed": 409, "gone": 410, "too_large": 413, "not_document": 415,
           "unreadable": 422, "cloud_only": 423, "edit_too_large": 413, "not_utf8": 415, "not_writable": 423,
           "pc_only": 403}
_MESSAGES = {**reader.REASON_MESSAGES, "not_found": "No such file in this scan",
             "scan_changed": "A newer scan renumbered this file: find it again in Explore",
             "pc_only": "Only the launcher on this PC can ask this"}
# Recent documents the sidebar lists.
RECENT_SHOWN = 8


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
    """The file a request names: its root, the root's current scan, its path rebuilt from the scan, and its index record.

    A negative id is a file the scan missed that the launcher found in its scanned folder (``POST /api/doc/find``).
    """
    if not isinstance(item_id, int) or isinstance(item_id, bool):
        raise _Refusal("not_found")
    if item_id < 0:
        return _found_item(root_key, scan_id, item_id)
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


def _folder(root_key: str, scan_id: object, folder_id: int) -> tuple[Root, dict[str, Any], str, list[str]]:
    """A scanned folder by id: its root, the current scan, its path and its names from the root down."""
    try:
        root, scan, path = scanned_item_path(resolve_roots(config.load()["roots"]), root_key, scan_id, "folder", folder_id)
        index, lock = POOL.get(store.index_dir() / scan["index_file"])
        with lock:
            names = [p["name"] for p in index.path(folder_id)]
    except ScanChanged as exc:
        raise _Refusal("scan_changed") from exc
    except KeyError as exc:
        raise _Refusal("not_found") from exc
    return root, scan, path, names


def _found_item(root_key: str, scan_id: object, item_id: int) -> tuple[Root, dict[str, Any], str, dict[str, Any]]:
    """A found file's root, scan, path and a record like an indexed one, from a fresh listing of its scanned folder."""
    found = store.found_doc(root_key, scan_id, -item_id) if isinstance(scan_id, int) else None
    if found is None:
        raise _Refusal("not_found")
    root, scan, folder_path, names = _folder(root_key, scan_id, found[0])
    entry = reader.find_document(folder_path, found[1])
    if entry is None:
        raise _Refusal("gone")
    record = {"id": item_id, "name": entry.name, "ext": os.path.splitext(entry.name)[1].lower().lstrip("."),
              "size": entry.size, "mtime": entry.mtime, "online": winfs.is_online_only(entry.attrs), "folder": names}
    return root, scan, os.path.join(folder_path, entry.name), record


_UNWANTED = {"link": "Only Markdown and HTML files open here", "image": "Not an image this viewer shows",
             "css": "Not a stylesheet", "js": "Not a script"}
_WANTED: dict[str, Callable[[str], bool]] = {
    "link": reader.is_document, "image": reader.is_image,
    "css": lambda p: p.lower().endswith(".css"), "js": lambda p: p.lower().endswith(".js")}


def _scanned_file(root: Root, scan: dict[str, Any], doc_path: str, href: str, kind: str
                  ) -> tuple[Optional[tuple[str, dict[str, Any]]], str]:
    """The scanned file a relative address in the document names (its path and index row), or why it has none."""
    path = resolve_href(root.path, doc_path, href)
    if path is None:
        return None, "Points outside this folder tree"
    if not _WANTED[kind](path):
        return None, _UNWANTED[kind]
    index, lock = POOL.get(store.index_dir() / scan["index_file"])
    with lock:
        hit = find_item(root.path, index, path)
    if hit is None:
        return None, "Not in the last scan"
    return (path, hit), ""


def _lookup(root: Root, scan: dict[str, Any], doc_path: str, kind: str) -> Callable[[str], tuple[Any, str]]:
    """A renderer lookup: a relative address in the document to the scanned file it names by id, or why it has none."""

    def find(href: str) -> tuple[Any, str]:
        found, why = _scanned_file(root, scan, doc_path, href, kind)
        if found is None:
            return None, why
        return {"root": root.key, "scan_id": scan["id"], "id": found[1]["id"], "online": found[1]["online"]}, ""

    return find


def _assets(root: Root, scan: dict[str, Any], doc_path: str) -> Callable[[str, str], tuple[Optional[bytes], str]]:
    """The HTML frame's asset lookup: a stylesheet, script or image beside the page, its bytes and type, never downloaded."""

    def asset(href: str, kind: str) -> tuple[Optional[bytes], str]:
        found, why = _scanned_file(root, scan, doc_path, href, kind)
        if found is None:
            return None, why
        path, hit = found
        ext = os.path.splitext(path)[1].lower()
        try:
            if kind == "image":
                return reader.read_image(path, hit["online"]), reader.IMAGE_TYPES[ext]
            return reader.read_asset(path, hit["online"]), reader.ASSET_TYPES[ext]
        except reader.Refused as exc:
            return None, exc.message

    return asset


def _shown(root: Root, scan: dict[str, Any], path: str, text: str, lines: bool = False) -> dict[str, Any]:
    """A document's text as the page draws it: an HTML page's frame, or Markdown rendered with its outline."""
    if reader.is_html(path):
        return frame(text, _lookup(root, scan, path, "link"), _assets(root, scan, path),
                     config.load()["docs"]["block_remote_html"])
    return {**render(text, _lookup(root, scan, path, "link"), _lookup(root, scan, path, "image"), lines=lines), "kind": "markdown"}


def _document(root_key: str, scan_id: object, item_id: object, download: bool) -> Any:
    try:
        root, scan, path, record = _item(root_key, scan_id, item_id)
        data = reader.read_document(path, record["online"], download)
    except _Refusal as exc:
        return _refuse(exc.reason)
    except reader.Refused as exc:
        return _refuse(exc.reason)
    store.touch_recent(root.key, record["folder"][1:] + [record["name"]])
    text = data.decode("utf-8-sig", errors="replace")
    body = _shown(root, scan, path, text)
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
    """One document drawn: ``{name, folder, html, outline, kind, ...}``, or a refusal with its ``reason``."""
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
        return {**writer.source(path, record["online"]), "kind": "html" if reader.is_html(path) else "markdown"}
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
    return _shown(root, scan, path, text, lines=True)


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


def _name(value: object) -> Optional[str]:
    """A bare file name from a request: one path part, never a path, a drive or ``..``."""
    if not isinstance(value, str) or not value or value in (".", ".."):
        return None
    return value if not any(c in value for c in "\\/:") else None


def _count(body: dict[str, Any], key: str) -> int:
    """A whole number from a request, never a bool and never negative."""
    value = body.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise HTTPException(422, "%s must be a whole number" % key)
    return value


def _dropped_name(body: dict[str, Any]) -> str:
    """The name of a file the page was handed by a drop: a bare name, never a path, and a document's (Markdown or HTML)."""
    name = _name(body.get("name"))
    if name is None:
        raise HTTPException(422, "name must be a file name, not a path")
    if not reader.is_document(name):
        raise _Refusal("not_document")
    return name


@router.post("/dropped/match")
def dropped_match(body: dict[str, Any] = Body(...)) -> Any:
    """Is a file dropped on the window one the scan has: ``{match: {root, scan_id, id} | null}``.

    The page sends the file's name, size and modified time (milliseconds), never a path.
    Exactly one indexed Markdown file of that name, size and time is a match; none or
    several is ``null`` and the page renders the dropped text read-only instead.
    """
    try:
        name = _dropped_name(body)
        size, modified = _count(body, "size"), _count(body, "modified")
    except _Refusal as exc:
        return _refuse(exc.reason)
    if size > reader.DOC_MAX_BYTES:
        return _refuse("too_large")
    return {"match": state.dropped_doc(config.load(), name, size, modified)}


@router.post("/dropped/render")
def dropped_render(body: dict[str, Any] = Body(...)) -> Any:
    """A dropped file no scan has, rendered read-only from the text the page read: nothing is read from disk.

    It has no folder here, so a relative link or image renders inert.
    """
    try:
        name = _dropped_name(body)
    except _Refusal as exc:
        return _refuse(exc.reason)
    text = _text(body, "text")
    if len(text.encode("utf-8")) > reader.DOC_MAX_BYTES:
        return _refuse("too_large")
    no_link = "Open the file from its folder to follow relative links"
    if reader.is_html(name):
        rendered = frame(text, lambda _href: (None, no_link), lambda _src, _kind: (None, "Open the file from its folder to load it"),
                         config.load()["docs"]["block_remote_html"])
    else:
        rendered = {**render(text, lambda _href: (None, no_link), lambda _src: (None, "Open the file from its folder to show its images")),
                    "kind": "markdown"}
    logger.info("ℹ️ a dropped %s file rendered read-only (no indexed copy)", os.path.splitext(name)[1].lower())
    return {"name": name, "dropped": True, "folder": [], "size": len(text.encode("utf-8")), **rendered}


@router.post("/find")
def find(request: Request, body: dict[str, Any] = Body(...)) -> Any:
    """The "Open with" launcher's question: the id of a Markdown file named by root, scan id, scanned folder id and name.

    Only for a caller on this PC. A file the scan has is answered from the index; one it missed is
    found in a fresh listing of that one folder and given a lasting negative id. No path is taken.
    """
    if not is_pc_itself(request):
        return _refuse("pc_only")
    name, folder_id = _name(body.get("name")), body.get("folder")
    if name is None or not isinstance(folder_id, int) or isinstance(folder_id, bool):
        return _refuse("not_found")
    if not reader.is_document(name):
        return _refuse("not_document")
    try:
        root, scan, folder_path, _names = _folder(str(body.get("root")), body.get("scan_id"), folder_id)
    except _Refusal as exc:
        return _refuse(exc.reason)
    index, lock = POOL.get(store.index_dir() / scan["index_file"])
    with lock:
        hit = index.find_file(folder_id, name)
    if hit is not None:
        logger.info("ℹ️ launcher: an indexed %s file", os.path.splitext(name)[1].lower())
        return {"root": root.key, "scan_id": scan["id"], "id": hit["id"]}
    entry = reader.find_document(folder_path, name)
    if entry is None:
        return _refuse("gone")
    number = store.remember_found(root.key, scan["id"], folder_id, entry.name)
    logger.info("ℹ️ launcher: a %s file newer than the scan, found in its folder's listing", os.path.splitext(name)[1].lower())
    return {"root": root.key, "scan_id": scan["id"], "id": -number}


def _recent_item(roots: dict[str, Root], root_key: str, rel: list[str]) -> Optional[dict[str, Any]]:
    """A recent document by its names below its root, as the root's current scan numbers it; None when that scan has no such file."""
    root, scan = roots.get(root_key), store.current_index(root_key)
    if root is None or scan is None or not rel:
        return None
    index, lock = POOL.get(store.index_dir() / scan["index_file"])
    with lock:
        folder = index.find_folder(rel[:-1])
        hit = index.find_file(folder, rel[-1]) if folder is not None else None
        names = [p["name"] for p in index.path(folder)] if folder is not None else []
    if hit is not None:
        item_id = hit["id"]
    else:
        number = store.found_number(root_key, scan["id"], folder, rel[-1]) if folder is not None else None
        if number is None:
            return None
        item_id = -number
    return {"root": root_key, "scan_id": scan["id"], "id": item_id, "name": rel[-1], "folder": names}


@router.get("/recent")
def recent() -> dict[str, Any]:
    """The documents opened last, newest first, each by its id in its root's current scan."""
    roots = {r.key: r for r in resolve_roots(config.load()["roots"])}
    items = []
    for root_key, rel in store.recent_docs():
        item = _recent_item(roots, root_key, rel)
        if item is not None:
            items.append(item)
        if len(items) == RECENT_SHOWN:
            break
    return {"recent": items}
