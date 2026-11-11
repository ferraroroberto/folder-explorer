"""A gate run opens no window on the desktop: the shared Tk root records every top-level mapped, and a test that maps one fails."""

from __future__ import annotations

import tkinter as tk

from src.archive_window import theme
from src.search_window.client import SearchClient
from src.search_window.model import QuickSearchModel
from src.search_window.view import QuickSearchView


def test_the_shared_root_records_a_window_that_maps(tk_root: tk.Tk, mapped_windows: list[str]) -> None:
    top = tk.Toplevel(tk_root)
    top.withdraw()
    top.update_idletasks()
    top.event_generate("<Map>", when="now")  # the event Tk sends when a window maps, without mapping this one
    assert mapped_windows == [str(top)]
    mapped_windows.clear()
    top.destroy()


def test_building_a_window_on_the_withdrawn_root_maps_nothing(tk_root: tk.Tk, mapped_windows: list[str]) -> None:
    view = QuickSearchView(QuickSearchModel(SearchClient(lambda: "", send=lambda *_a, **_k: None)), theme.palette(False), tk_root)
    view.build()
    tk_root.update()
    assert tk_root.state() == "withdrawn" and mapped_windows == []
    view.frame.destroy()
