"""The Archive window stays inside the screen's work area however many folders "Find a folder" lists: the buttons stay on screen."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest

from src.archive_window import theme
from src.archive_window.finder import ROWS_SHOWN
from src.archive_window.view import ArchiveView
from tests.archive.window.test_archive_window import _doc, _hit, root  # noqa: F401 - the invented mail

# A laptop-height screen: 1080 px of work area.
WORK = (0, 0, 1920, 1080)
LONG_SUBJECT = " ".join("Quarterly planning review for the invented regional programme, part %d" % n for n in range(1, 9))


class _Many:
    """An invented folder source: "many" finds more folders than the window lists."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def folders(self, query: str) -> dict[str, Any]:
        hits = [_hit(i, self.root / "Clients" / ("Programme %02d" % i)) for i in range(1, ROWS_SHOWN + 5)] if query == "many" else []
        return {"results": hits, "total": len(hits)}

    def children(self, path: str) -> list[dict[str, Any]]:
        return []


def _crowded_doc(root: Path) -> dict[str, Any]:
    doc = _doc(root)
    doc["mail"]["subject"] = LONG_SUBJECT
    doc["suggestions"].append({**doc["suggestions"][1], "folder_path": str(root / "Clients"), "score": 0.2})
    return doc


def _hidden(master: tk.Tk) -> tk.Toplevel:
    """A top-level that is never mapped: Tk still lays it out, and nothing opens on the desktop."""
    top = tk.Toplevel(master)
    top.withdraw()
    return top


@pytest.fixture()
def crowded(root: Path, tk_root: tk.Tk) -> Iterator[ArchiveView]:  # noqa: F811
    """A window over three suggestions and a long subject, with eight folders found."""
    top = _hidden(tk_root)
    view = ArchiveView(_crowded_doc(root), lambda *_a: {}, _Many(root), theme.palette(False), top, work_area=lambda: WORK)  # type: ignore[arg-type]
    view.close = lambda: None  # type: ignore[method-assign]
    view.build()
    view._clear_placeholder(None)
    view.query.set("many")
    if view.search_job:
        top.after_cancel(view.search_job)
    view._search()
    top.update()
    yield view
    top.destroy()


def _bottom(widget: tk.Misc) -> int:
    return widget.winfo_rooty() + widget.winfo_height()


def test_the_window_and_its_buttons_stay_inside_the_work_area(crowded: ArchiveView) -> None:
    top = crowded.root
    assert top.winfo_rooty() >= WORK[1] and _bottom(top) <= WORK[3]
    for button in (crowded.here_button, crowded.go):
        assert button.winfo_manager() and _bottom(button) <= min(_bottom(top), WORK[3]), button.cget("text")


def test_every_folder_is_reached_by_the_keys_and_stays_in_view(crowded: ArchiveView) -> None:
    assert crowded.list_canvas is not None and int(crowded.list_canvas.cget("height")) < crowded.list_inner.winfo_reqheight(), "the list scrolls"
    for step in range(ROWS_SHOWN):
        canvas, row = crowded.list_canvas, crowded.list_rows[step]  # a move repaints the list, so ask again each time
        assert canvas is not None
        shown_top = canvas.canvasy(0)
        assert crowded.finder.selected == step
        assert shown_top <= row.winfo_y() and row.winfo_y() + row.winfo_height() <= shown_top + canvas.winfo_height() + 1, step
        crowded._move(1)
        crowded.root.update()
    assert crowded.finder.selected == ROWS_SHOWN - 1


def test_the_wheel_scrolls_the_list_and_back(crowded: ArchiveView) -> None:
    canvas = crowded.list_canvas
    assert canvas is not None and canvas.canvasy(0) == 0
    assert crowded._wheel(SimpleNamespace(delta=-120)) == "break"
    crowded.root.update()
    assert canvas.canvasy(0) > 0
    crowded._wheel(SimpleNamespace(delta=120))
    crowded.root.update()
    assert canvas.canvasy(0) == 0


def test_a_roomy_screen_lists_every_row_without_scrolling(root: Path, tk_root: tk.Tk) -> None:  # noqa: F811
    top = _hidden(tk_root)
    tall = (0, 0, 1920, 4000)
    view = ArchiveView(_crowded_doc(root), lambda *_a: {}, _Many(root), theme.palette(False), top, work_area=lambda: tall)  # type: ignore[arg-type]
    view.close = lambda: None  # type: ignore[method-assign]
    view.build()
    view._clear_placeholder(None)
    view.query.set("many")
    if view.search_job:
        top.after_cancel(view.search_job)
    view._search()
    top.update()
    assert view.list_canvas is not None and int(view.list_canvas.cget("height")) == view.list_inner.winfo_reqheight()
    assert not view.list_bar.winfo_ismapped()
    top.destroy()
