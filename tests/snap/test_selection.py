"""The selection state machine of both modes, offline: which press and release anchors a corner and which one saves."""

from __future__ import annotations

import pytest

from src.snap.core import MIN_SELECTION_SIDE, Rect
from src.snap.selection import Outcome, Selection

IMAGE = (400, 300)


def _click(selection: Selection, at: tuple[int, int]):
    selection.press(at)
    return selection.release(at, IMAGE)


def test_click_mode_first_click_anchors_and_saves_nothing() -> None:
    selection = Selection(hold_to_capture=False)
    assert _click(selection, (10, 20)) == (Outcome.NOTHING, None)
    assert selection.anchor == (10, 20)


def test_click_mode_second_click_saves_the_area_between_the_clicks() -> None:
    selection = Selection(hold_to_capture=False)
    _click(selection, (10, 20))
    assert _click(selection, (110, 90)) == (Outcome.SAVE, Rect(10, 20, 110, 90))


def test_click_mode_orders_corners_dragged_up_and_left() -> None:
    selection = Selection(hold_to_capture=False)
    _click(selection, (110, 90))
    assert _click(selection, (10, 20)) == (Outcome.SAVE, Rect(10, 20, 110, 90))


def test_click_mode_second_click_saves_where_it_is_released_not_pressed() -> None:
    selection = Selection(hold_to_capture=False)
    _click(selection, (10, 20))
    selection.press((100, 80))
    assert selection.release((120, 95), IMAGE) == (Outcome.SAVE, Rect(10, 20, 120, 95))


def test_click_mode_a_second_click_on_the_anchor_keeps_waiting() -> None:
    selection = Selection(hold_to_capture=False)
    _click(selection, (10, 20))
    assert _click(selection, (10, 20)) == (Outcome.NOTHING, None)
    assert selection.anchor == (10, 20)
    assert _click(selection, (60, 70))[0] is Outcome.SAVE


@pytest.mark.parametrize("end", [(10 + MIN_SELECTION_SIDE - 1, 200), (200, 20 + MIN_SELECTION_SIDE - 1)])
def test_click_mode_a_second_click_under_the_minimum_side_keeps_waiting(end: tuple[int, int]) -> None:
    selection = Selection(hold_to_capture=False)
    _click(selection, (10, 20))
    assert _click(selection, end) == (Outcome.NOTHING, None)
    assert selection.anchor == (10, 20)


def test_click_mode_the_selection_clamps_to_the_image() -> None:
    selection = Selection(hold_to_capture=False)
    _click(selection, (300, 200))
    assert _click(selection, (900, 900)) == (Outcome.SAVE, Rect(300, 200, 400, 300))


def test_a_release_before_any_press_does_nothing_in_either_mode() -> None:
    for hold in (False, True):
        assert Selection(hold).release((50, 50), IMAGE) == (Outcome.NOTHING, None)


def test_hold_mode_press_drag_release_saves() -> None:
    selection = Selection(hold_to_capture=True)
    selection.press((10, 20))
    assert selection.anchor == (10, 20)
    assert selection.release((110, 90), IMAGE) == (Outcome.SAVE, Rect(10, 20, 110, 90))


def test_hold_mode_a_release_on_the_press_point_is_too_small() -> None:
    selection = Selection(hold_to_capture=True)
    selection.press((10, 20))
    assert selection.release((10, 20), IMAGE) == (Outcome.TOO_SMALL, None)


def test_hold_mode_saves_on_the_first_release_not_the_second_click() -> None:
    selection = Selection(hold_to_capture=True)
    selection.press((10, 20))
    outcome, _ = selection.release((200, 150), IMAGE)
    assert outcome is Outcome.SAVE
