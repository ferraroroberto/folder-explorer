"""The region screenshot's selection state machine: which mouse events anchor a corner and which one saves.

No Tk and no Win32, so both modes are tested offline. The overlay feeds it every left press and release (image
pixels) and does what the answer says. Arrow-key nudging moves the real cursor, so it works in both modes
without reaching this class.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional, Tuple

from src.snap.core import Rect, selection_rect

Point = Tuple[int, int]


class Outcome(Enum):
    """What a release asks the overlay to do."""

    NOTHING = "nothing"
    SAVE = "save"
    TOO_SMALL = "too small"


class Selection:
    """One capture's selection.

    Click mode (``hold_to_capture`` off): the first click anchors a corner and releasing it changes nothing; the
    second click saves, on its release, so the overlay is not withdrawn under a button still held. A second
    click that would crop under the minimum size keeps the overlay and the anchor, so an accidental double
    click does not throw the selection away. Hold mode: press anchors, release saves, as the tool always did.
    """

    def __init__(self, hold_to_capture: bool) -> None:
        self.hold_to_capture = hold_to_capture
        self.anchor: Optional[Point] = None
        self._second_press = False

    def press(self, point: Point) -> None:
        if self.anchor is None:
            self.anchor = point
            self._second_press = False
        else:
            self._second_press = True

    def release(self, point: Point, image_size: Tuple[int, int]) -> Tuple[Outcome, Optional[Rect]]:
        """The outcome of a left release at ``point`` and, for ``SAVE``, the rectangle to crop."""
        if self.anchor is None:
            return Outcome.NOTHING, None
        if not self.hold_to_capture and not self._second_press:
            return Outcome.NOTHING, None
        rect = selection_rect(self.anchor, point, image_size)
        if rect is not None:
            return Outcome.SAVE, rect
        if self.hold_to_capture:
            return Outcome.TOO_SMALL, None
        self._second_press = False
        return Outcome.NOTHING, None
