"""The Markdown editor: one writer, the version check, the overwrite backup, and a byte-exact round trip.

Writes go through ``src.docs.writer``, which reads through ``src.docs.reader``
and writes through ``src.atomic_write``. The tests use invented files in a
temp root; the cloud-only refusal replaces the reader's listing seam, so no
test touches a real OneDrive placeholder.
"""

from __future__ import annotations

import ast
import codecs
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.webapp.server import app
from src import store, winfs
from src.docs import reader, writer
from tests.fixture_tree import DOCS_FOLDER, JOURNAL_MD

LOCAL = ("127.0.0.1", 50000)
ROOT = Path(__file__).resolve().parents[2]
WRITER = ROOT / "src" / "docs" / "writer.py"

# Calls that create, change or remove a file: the repo's writers by name, the os and shutil file calls, and an
# open() or fdopen() whose mode writes. A string's .replace() or a read-only open is not one.
_WRITERS = frozenset({"atomic_write_text", "atomic_write_bytes", "swap_in", "write_bytes", "write_text", "open_file",
                      "make_dirs", "mkstemp", "connect", "write"})
_OS_WRITES = frozenset({"replace", "rename", "renames", "remove", "unlink", "rmdir", "makedirs", "mkdir", "truncate"})


def _writes(call: ast.Call) -> str:
    fn = call.func
    name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
    owner = fn.value.id if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) else ""
    if name in _WRITERS or owner == "shutil" or (owner == "os" and name in _OS_WRITES):
        return name
    if name in {"open", "fdopen"}:
        modes = [a.value for a in call.args[1:2] if isinstance(a, ast.Constant)]
        modes += [k.value.value for k in call.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)]
        if any(isinstance(m, str) and set(m) & set("wax+") for m in modes):
            return name
    return ""


def _calls(path: Path) -> set[str]:
    return {w for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))) if isinstance(node, ast.Call) and (w := _writes(node))}


def _imports_writer(path: Path) -> bool:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module == "src.docs" and any(a.name == "writer" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "src.docs.writer":
            return True
    return False


def test_only_the_docs_writer_writes_a_document() -> None:
    viewer = [*(ROOT / "src" / "docs").rglob("*.py"), ROOT / "app" / "webapp" / "routers" / "docs.py"]
    writers = [p.relative_to(ROOT).as_posix() for p in viewer if p != WRITER and _calls(p)]
    assert writers == []
    # The writer writes only through the atomic writer (and makes the backup folder); it opens nothing itself.
    assert _calls(WRITER) == {"atomic_write_bytes", "make_dirs"}
    allowed = {ROOT / "app" / "webapp" / "routers" / "docs.py"}
    importers = [p.relative_to(ROOT).as_posix() for folder in ("src", "app", "scripts") for p in (ROOT / folder).rglob("*.py")
                 if _imports_writer(p) and p not in allowed]
    assert importers == []


def _version(path: Path) -> str:
    return writer.source(str(path), index_online=False)["version"]


def _names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def test_the_writer_refuses_a_file_that_is_not_markdown(tmp_path: Path) -> None:
    other = tmp_path / "notes.txt"
    other.write_bytes(b"plain")
    with pytest.raises(reader.Refused) as refused:
        writer.save(str(other), "changed", "any", index_online=False)
    assert refused.value.reason == "not_document"
    assert other.read_bytes() == b"plain" and _names(tmp_path) == ["notes.txt"]


def test_the_writer_never_creates_a_file(tmp_path: Path) -> None:
    with pytest.raises(reader.Refused) as refused:
        writer.save(str(tmp_path / "new.md"), "# New", "any", index_online=False)
    assert refused.value.reason == "gone" and _names(tmp_path) == []


def test_a_save_changes_the_opened_file_and_nothing_beside_it(tmp_path: Path) -> None:
    doc = tmp_path / "page.md"
    doc.write_bytes(b"# Page\n")
    (tmp_path / "other.md").write_bytes(b"# Other\n")
    version = writer.save(str(doc), "# Page\n\nMore.\n", _version(doc), index_online=False)
    assert doc.read_bytes() == b"# Page\n\nMore.\n"
    assert (tmp_path / "other.md").read_bytes() == b"# Other\n"
    assert _names(tmp_path) == ["other.md", "page.md"]  # no temp file left behind
    assert version == _version(doc)


def test_a_stale_version_is_refused_and_the_newer_copy_is_kept(tmp_path: Path) -> None:
    doc = tmp_path / "page.md"
    doc.write_bytes(b"# Page\n")
    opened = _version(doc)
    doc.write_bytes(b"# Page\n\nTheirs.\n")
    with pytest.raises(writer.Conflict) as conflict:
        writer.save(str(doc), "# Page\n\nMine.\n", opened, index_online=False)
    assert doc.read_bytes() == b"# Page\n\nTheirs.\n"
    assert conflict.value.text == "# Page\n\nTheirs.\n" and conflict.value.version == _version(doc)


def test_the_version_changes_with_any_byte(tmp_path: Path) -> None:
    doc = tmp_path / "page.md"
    doc.write_bytes(b"# Page A\n")
    first = _version(doc)
    doc.write_bytes(b"# Page B\n")  # same size, possibly the same second
    assert _version(doc) != first


def test_overwrite_backs_up_the_replaced_copy(isolated: Path) -> None:
    doc = isolated / "page.md"
    doc.write_bytes(b"# Page\n\nTheirs.\n")
    theirs = _version(doc)
    writer.save(str(doc), "# Page\n\nMine.\n", theirs, index_online=False, overwrite=True)
    assert doc.read_bytes() == b"# Page\n\nMine.\n"
    backups = list((store.data_dir() / "doc-backups").iterdir())
    assert len(backups) == 1 and backups[0].name.endswith("page.md") and backups[0].read_bytes() == b"# Page\n\nTheirs.\n"


def test_a_plain_save_writes_no_backup(isolated: Path) -> None:
    doc = isolated / "page.md"
    doc.write_bytes(b"# Page\n")
    writer.save(str(doc), "# Page\n\nMine.\n", _version(doc), index_online=False)
    assert not (store.data_dir() / "doc-backups").exists()


@pytest.mark.parametrize("original", [
    b"# Plain\n\nText.\n",
    codecs.BOM_UTF8 + b"# BOM\r\n\r\nText.\r\n",
    b"# No trailing newline\r\n\r\nText.",
    b"# Mixed\r\nline\nline\r\n",
    "# Ünïcode ✓\n".encode("utf-8"),
])
def test_an_unchanged_text_round_trips_byte_for_byte(tmp_path: Path, original: bytes) -> None:
    doc = tmp_path / "page.md"
    doc.write_bytes(original)
    opened = writer.source(str(doc), index_online=False)
    # The browser's editor hands back \n line ends whatever the file had.
    writer.save(str(doc), opened["text"].replace("\r\n", "\n"), opened["version"], index_online=False)
    assert doc.read_bytes() == original


@pytest.mark.parametrize(("original", "expected"), [
    (codecs.BOM_UTF8 + b"# A\r\n\r\nOld.\r\n", codecs.BOM_UTF8 + b"# A\r\n\r\nNew.\r\n"),
    (b"# A\n\nOld.", b"# A\n\nNew."),
    (b"# A\r\n\r\nOld.", b"# A\r\n\r\nNew."),
    (b"# A\n\nOld.\n", b"# A\n\nNew.\n"),
])
def test_an_edit_keeps_the_bom_line_ends_and_trailing_newline(tmp_path: Path, original: bytes, expected: bytes) -> None:
    doc = tmp_path / "page.md"
    doc.write_bytes(original)
    opened = writer.source(str(doc), index_online=False)
    edited = opened["text"].replace("\r\n", "\n").replace("Old.", "New.")
    writer.save(str(doc), edited.rstrip("\n") + "\n", opened["version"], index_online=False)
    assert doc.read_bytes() == expected


def test_a_file_that_is_not_utf8_opens_read_only(tmp_path: Path) -> None:
    doc = tmp_path / "latin.md"
    doc.write_bytes("# Café\n".encode("cp1252"))
    opened = writer.source(str(doc), index_online=False)
    assert opened["editable"] is False and opened["reason"] == "not_utf8" and "text" not in opened
    with pytest.raises(reader.Refused) as refused:
        writer.save(str(doc), "# Cafe\n", opened["version"], index_online=False)
    assert refused.value.reason == "not_utf8" and doc.read_bytes() == "# Café\n".encode("cp1252")


def test_edit_is_capped_at_one_megabyte(tmp_path: Path) -> None:
    doc = tmp_path / "long.md"
    doc.write_bytes(b"#" * (writer.EDIT_MAX_BYTES + 1))
    opened = writer.source(str(doc), index_online=False)
    assert opened["editable"] is False and opened["reason"] == "edit_too_large"
    small = tmp_path / "small.md"
    small.write_bytes(b"# Small\n")
    with pytest.raises(reader.Refused) as refused:
        writer.save(str(small), "#" * (writer.EDIT_MAX_BYTES + 1), _version(small), index_online=False)
    assert refused.value.reason == "edit_too_large" and small.read_bytes() == b"# Small\n"


def test_a_cloud_only_file_is_never_saved_or_downloaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = tmp_path / "page.md"
    doc.write_bytes(b"# Page\n")
    opened = _version(doc)
    listed = reader._listed

    def cloud(path: str) -> Any:
        e = listed(path)
        return winfs.Entry(e.name, e.is_dir, e.size, 0, e.attrs | winfs.FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS, e.mtime, e.reparse_tag)

    def never(*_a: Any, **_k: Any) -> bytes:
        raise AssertionError("a cloud-only file was opened")

    monkeypatch.setattr(reader, "_listed", cloud)
    monkeypatch.setattr(reader, "_open_read", never)
    with pytest.raises(reader.Refused) as refused:
        writer.save(str(doc), "# Mine\n", opened, index_online=False)
    assert refused.value.reason == "cloud_only" and doc.read_bytes() == b"# Page\n"


# ------------------------------------------------------------------ the routes

def _loc(d: dict[str, Any], name: str) -> dict[str, Any]:
    return {"root": d["root"], "scan_id": d["scan"], "id": d["ids"][name]}


def test_the_routes_edit_save_and_report_a_conflict(docs: dict[str, Any]) -> None:
    journal = Path(docs["path"]) / DOCS_FOLDER / "journal.md"
    with TestClient(app, client=LOCAL) as c:
        src = c.get("/api/doc/source", params={"root": docs["root"], "scan": docs["scan"], "id": docs["ids"]["journal.md"]})
        assert src.status_code == 200, src.text
        opened = src.json()
        assert opened["editable"] is True and opened["text"] == JOURNAL_MD.decode()
        saved = c.post("/api/doc/save", json={**_loc(docs, "journal.md"), "text": "# Journal\n\nSecond.\n", "version": opened["version"]})
        assert saved.status_code == 200, saved.text
        assert journal.read_bytes() == b"# Journal\n\nSecond.\n"
        stale = c.post("/api/doc/save", json={**_loc(docs, "journal.md"), "text": "# Journal\n\nThird.\n", "version": opened["version"]})
        not_md = c.post("/api/doc/save", json={**_loc(docs, "notes.txt"), "text": "x", "version": "v"})
        bad = c.post("/api/doc/save", json={**_loc(docs, "journal.md"), "text": 5, "version": opened["version"]})
    assert stale.status_code == 409 and stale.json()["reason"] == "conflict"
    assert stale.json()["text"] == "# Journal\n\nSecond.\n" and stale.json()["version"] == saved.json()["version"]
    assert stale.json()["detail"] == "Changed on disk since you opened it"
    assert journal.read_bytes() == b"# Journal\n\nSecond.\n"
    assert not_md.status_code == 415 and not_md.json()["reason"] == "not_document"
    assert bad.status_code == 422


def test_the_draft_preview_renders_without_reading_the_disk(docs: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def never(*_a: Any, **_k: Any) -> bytes:
        raise AssertionError("the draft preview read a file")

    monkeypatch.setattr(reader, "_open_read", never)
    with TestClient(app, client=LOCAL) as c:
        r = c.post("/api/doc/render", json={**_loc(docs, "guide.md"), "text": "# Draft\n\nSee [watering](sub/watering.md)."})
    assert r.status_code == 200, r.text
    assert r.json()["outline"][0]["text"] == "Draft" and 'data-doc-id="%d"' % docs["ids"]["watering.md"] in r.json()["html"]
