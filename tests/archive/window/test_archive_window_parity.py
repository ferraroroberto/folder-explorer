"""The fallback window shows every field the Archive sheet does: the sheet's real document, and the page's own code, against the window's table."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

from src.archive_window import model
from src.archive_window.model import ModelView, Option
from src.email import archive_sheet
from tests.archive.batch_support import (  # noqa: F401
    FakeOutlookClient,
    _mail,
    _seed_index,
    archive_root,
    cfg,
)

ARCHIVE_JS = Path(__file__).resolve().parents[3] / "app" / "webapp" / "static" / "archive.js"
# A quote inside a single-quoted JS string.
ESCAPED_QUOTE = chr(92) + "'"
# Fields the server adds to the document for the page after the sheet's own step: the window has no server.
PAGE_ONLY = {"location": "the page opens a folder by its place in the scan; the window opens it by path", "token": "the page's handle on a prepared mail"}


@pytest.fixture()
def doc(cfg: dict, archive_root: Path) -> dict:  # noqa: F811
    _seed_index(cfg, archive_root, {"Project Alpha": ["Kickoff agenda", "Budget notes"], "Internal": ["Team agenda"]})
    (archive_root / "Project Alpha").mkdir()
    item = _mail("kick@example.invalid", "Kickoff agenda", sender="sender@example.invalid")
    return archive_sheet.suggest(FakeOutlookClient([item]), cfg, item, "Inbox")


def _placed(attr_path: str, view: ModelView) -> bool:
    kind, _, name = attr_path.partition(".")
    if not name:
        return True
    return name in {f.name for f in dataclasses.fields(Option)} if kind == "Option" else hasattr(view, name)


def test_every_field_of_the_sheets_document_has_a_place_in_the_window(doc: dict) -> None:
    assert doc["suggestions"], "the fixture must offer a folder, or the suggestion fields go unchecked"
    assert set(doc) <= set(model.DOC_SHOWN), "the sheet's document has a field the window does not place: " + str(set(doc) - set(model.DOC_SHOWN))
    assert set(doc["mail"]) <= set(model.MAIL_SHOWN) | set(model.MAIL_NOT_SHOWN)
    for s in doc["suggestions"]:
        assert set(s) <= set(model.SUGGESTION_SHOWN) | set(model.SUGGESTION_NOT_SHOWN), set(s)


def test_each_place_the_table_names_exists(doc: dict) -> None:
    view = ModelView(doc)
    for where in (*model.DOC_SHOWN.values(), *model.SUGGESTION_SHOWN.values()):
        assert _placed(where, view), where


def test_every_field_the_page_reads_is_placed_in_the_window() -> None:
    js = ARCHIVE_JS.read_text(encoding="utf-8")
    reads = {
        "suggestion": (set(re.findall(r"\bo\.(\w+)", js)) | set(re.findall(r"\bchoice\.(\w+)", js)),
                       set(model.SUGGESTION_SHOWN) | set(model.SUGGESTION_NOT_SHOWN) | set(PAGE_ONLY)),
        "mail": (set(re.findall(r"\bmail\.(\w+)", js)), set(model.MAIL_SHOWN) | set(model.MAIL_NOT_SHOWN)),
        "document": (set(re.findall(r"\bd\.(\w+)", js)), set(model.DOC_SHOWN) | set(PAGE_ONLY)),
    }
    for what, (read, placed) in reads.items():
        assert read, "the pattern found no %s fields in archive.js: the test needs updating" % what
        assert read <= placed, "archive.js reads %s fields the window does not place: %s" % (what, sorted(read - placed))


def test_the_window_shares_the_pages_constants() -> None:
    js = ARCHIVE_JS.read_text(encoding="utf-8")
    assert float(re.search(r"const WEAK_SCORE = ([\d.]+);", js).group(1)) == model.WEAK_SCORE  # type: ignore[union-attr]
    choices = re.search(r"const DATE_CHOICES = \[(.*?)\];", js).group(1)  # type: ignore[union-attr]
    assert re.findall(r"\['(\w+)', '(\w+)'\]", choices) == list(model.DATE_CHOICES)
    unescaped = js.replace(ESCAPED_QUOTE, "'")
    assert model.RANK_NOTE in unescaped
    assert model.NO_NAME in unescaped
    assert ">" + model.NO_CATEGORY + "</option>" in js
