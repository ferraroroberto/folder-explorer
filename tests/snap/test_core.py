"""Offline tests for the region screenshot's naming, crop and loupe maths, hotkey parsing and prefix rules (no display, no Win32)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from src.snap.core import (
    ARROW_KEYS,
    LOUPE_CELLS,
    LOUPE_CURSOR_GAP,
    LOUPE_RADIUS,
    MAX_PREFIX_LENGTH,
    MIN_SELECTION_SIDE,
    MOD_CONTROL,
    MOD_SHIFT,
    NUDGE_STEP_FAST,
    ConfigError,
    Rect,
    loupe_label,
    loupe_source_box,
    monitor_for,
    nudge_point,
    parse_hotkey,
    place_loupe,
    screen_to_image,
    selection_rect,
    selection_size,
    unique_path,
    validate_prefix,
    window_geometry,
)


class NamingTests(unittest.TestCase):
    def test_name_carries_date_time_and_milliseconds(self):
        moment = datetime(2026, 3, 7, 9, 5, 2, 42_000)
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(unique_path(Path(folder), moment).name, "snap_2026-03-07_09-05-02-042.jpg")

    def test_two_captures_in_the_same_millisecond_never_collide(self):
        moment = datetime(2026, 3, 7, 9, 5, 2, 42_000)
        with tempfile.TemporaryDirectory() as folder:
            names = []
            for _ in range(3):
                path = unique_path(Path(folder), moment)
                path.write_bytes(b"x")
                names.append(path.name)
        self.assertEqual(len(set(names)), 3)
        self.assertEqual(names[1], "snap_2026-03-07_09-05-02-042_2.jpg")

    def test_captures_in_the_same_second_get_different_names(self):
        with tempfile.TemporaryDirectory() as folder:
            first = unique_path(Path(folder), datetime(2026, 3, 7, 9, 5, 2, 100_000))
            second = unique_path(Path(folder), datetime(2026, 3, 7, 9, 5, 2, 600_000))
        self.assertNotEqual(first, second)


    def test_prefix_replaces_snap_in_the_name(self):
        moment = datetime(2026, 3, 7, 9, 5, 2, 42_000)
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(
                unique_path(Path(folder), moment, "invoice").name, "invoice_2026-03-07_09-05-02-042.jpg"
            )


class CropMathsTests(unittest.TestCase):
    def test_drag_in_any_direction_gives_the_same_rect(self):
        size = (1920, 1080)
        forward = selection_rect((100, 200), (400, 500), size)
        backward = selection_rect((400, 500), (100, 200), size)
        self.assertEqual(forward, Rect(100, 200, 400, 500))
        self.assertEqual(backward, forward)

    def test_selection_is_clamped_to_the_image(self):
        self.assertEqual(selection_rect((-50, -50), (3000, 2000), (1920, 1080)), Rect(0, 0, 1920, 1080))

    def test_a_click_or_tiny_drag_is_not_a_capture(self):
        self.assertIsNone(selection_rect((10, 10), (10, 10), (100, 100)))
        edge = MIN_SELECTION_SIDE - 1
        self.assertIsNone(selection_rect((10, 10), (10 + edge, 80), (100, 100)))
        self.assertIsNotNone(selection_rect((10, 10), (10 + MIN_SELECTION_SIDE, 80), (100, 100)))

    def test_secondary_monitor_left_of_primary_has_a_negative_origin(self):
        # Virtual screen starts at x=-1920: a point on the left monitor maps to a small image x.
        self.assertEqual(screen_to_image(-1820, 40, (-1920, 0)), (100, 40))
        # The same maths puts the primary monitor's origin at image x=1920.
        self.assertEqual(screen_to_image(0, 0, (-1920, 0)), (1920, 0))

    def test_monitor_above_the_primary_offsets_y(self):
        self.assertEqual(screen_to_image(500, -300, (0, -1080)), (500, 780))

    def test_drag_across_monitors_crops_in_image_space(self):
        origin = (-1920, 0)
        start = screen_to_image(-100, 100, origin)
        end = screen_to_image(300, 400, origin)
        self.assertEqual(selection_rect(start, end, (3840, 1080)), Rect(1820, 100, 2220, 400))

    def test_scaled_display_uses_physical_pixels_unchanged(self):
        # At 150% the process is DPI aware, so screen and image units are both physical pixels.
        self.assertEqual(screen_to_image(2400, 1350, (0, 0)), (2400, 1350))
        self.assertEqual(selection_rect((0, 0), (2400, 1350), (2880, 1620)), Rect(0, 0, 2400, 1350))

    def test_window_geometry_signs_negative_and_positive_origins(self):
        self.assertEqual(window_geometry(-1920, 0, 3840, 1080), "3840x1080-1920+0")
        self.assertEqual(window_geometry(0, -1080, 1920, 2160), "1920x2160+0-1080")


class LoupeGeometryTests(unittest.TestCase):
    SCREEN = Rect(0, 0, 1920, 1080)
    BOX = (200, 222)

    def test_source_box_is_a_square_centred_on_the_cursor_pixel(self):
        box = loupe_source_box((500, 300))
        self.assertEqual(
            box, Rect(500 - LOUPE_RADIUS, 300 - LOUPE_RADIUS, 500 + LOUPE_RADIUS + 1, 300 + LOUPE_RADIUS + 1)
        )
        self.assertEqual(box.right - box.left, LOUPE_CELLS)
        self.assertEqual(box.bottom - box.top, LOUPE_CELLS)

    def test_source_box_may_reach_past_the_image_edge(self):
        self.assertEqual(loupe_source_box((0, 0)).left, -LOUPE_RADIUS)

    def test_box_sits_below_right_of_the_cursor_in_open_space(self):
        self.assertEqual(
            place_loupe((500, 300), self.BOX, self.SCREEN), (500 + LOUPE_CURSOR_GAP, 300 + LOUPE_CURSOR_GAP)
        )

    def test_box_flips_left_at_the_right_edge(self):
        x, y = place_loupe((1900, 300), self.BOX, self.SCREEN)
        self.assertEqual(x, 1900 - LOUPE_CURSOR_GAP - self.BOX[0])
        self.assertEqual(y, 300 + LOUPE_CURSOR_GAP)

    def test_box_flips_up_at_the_bottom_edge(self):
        x, y = place_loupe((500, 1070), self.BOX, self.SCREEN)
        self.assertEqual(x, 500 + LOUPE_CURSOR_GAP)
        self.assertEqual(y, 1070 - LOUPE_CURSOR_GAP - self.BOX[1])

    def test_box_flips_both_ways_in_the_bottom_right_corner_and_stays_on_screen(self):
        x, y = place_loupe((1919, 1079), self.BOX, self.SCREEN)
        self.assertLessEqual(x + self.BOX[0], self.SCREEN.right)
        self.assertLessEqual(y + self.BOX[1], self.SCREEN.bottom)
        self.assertLess(x + self.BOX[0], 1919)
        self.assertLess(y + self.BOX[1], 1079)

    def test_box_is_clamped_when_flipping_would_leave_the_left_or_top_edge(self):
        narrow = Rect(0, 0, 260, 300)
        x, y = place_loupe((250, 290), self.BOX, narrow)
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(x + self.BOX[0], narrow.right)
        self.assertLessEqual(y + self.BOX[1], narrow.bottom)

    def test_second_monitor_borders_use_that_monitors_bounds(self):
        # Two 1920-wide monitors side by side in image space: a cursor near the seam flips off the left monitor's right edge.
        left, right = Rect(0, 0, 1920, 1080), Rect(1920, 0, 3840, 1080)
        monitors = [left, right]
        cursor = (1900, 400)
        self.assertEqual(monitor_for(cursor, monitors), left)
        x, _ = place_loupe(cursor, self.BOX, monitor_for(cursor, monitors))
        self.assertLessEqual(x + self.BOX[0], left.right)
        cursor = (1930, 400)
        self.assertEqual(monitor_for(cursor, monitors), right)
        x, _ = place_loupe(cursor, self.BOX, monitor_for(cursor, monitors))
        self.assertGreaterEqual(x, right.left)

    def test_monitor_for_a_point_outside_every_monitor_is_the_nearest(self):
        monitors = [Rect(0, 0, 1920, 1080), Rect(1920, 0, 3840, 1080)]
        self.assertEqual(monitor_for((4000, 500), monitors), monitors[1])
        self.assertEqual(monitor_for((-50, 500), monitors), monitors[0])

    def test_dpi_scaled_screen_is_laid_out_in_physical_pixels(self):
        # At 150% the capture and the cursor are both physical pixels (2880x1620), so the same maths applies unscaled.
        screen = Rect(0, 0, 2880, 1620)
        cursor = screen_to_image(2870, 1610, (0, 0))
        self.assertEqual(
            place_loupe(cursor, self.BOX, screen),
            (2870 - LOUPE_CURSOR_GAP - self.BOX[0], 1610 - LOUPE_CURSOR_GAP - self.BOX[1]),
        )

    def test_label_shows_coordinates_and_the_drag_size_only_while_dragging(self):
        self.assertEqual(loupe_label((120, -40), None), "120,-40")
        self.assertEqual(loupe_label((120, 40), (300, 200)), "120,40 (300×200)")

    def test_selection_size_follows_the_drag_and_clamps_to_the_image(self):
        self.assertEqual(selection_size((100, 100), (40, 250), (1920, 1080)), (60, 150))
        self.assertEqual(selection_size((10, 10), (5000, 5000), (1920, 1080)), (1910, 1070))
        self.assertEqual(selection_size((10, 10), (10, 10), (1920, 1080)), (0, 0))


class NudgeTests(unittest.TestCase):
    BOUNDS = Rect(-1920, 0, 1920, 1080)

    def test_arrows_move_one_pixel(self):
        self.assertEqual(nudge_point((0, 500), "Left", False, self.BOUNDS), (-1, 500))
        self.assertEqual(nudge_point((0, 500), "Right", False, self.BOUNDS), (1, 500))
        self.assertEqual(nudge_point((0, 500), "Up", False, self.BOUNDS), (0, 499))
        self.assertEqual(nudge_point((0, 500), "Down", False, self.BOUNDS), (0, 501))

    def test_shift_moves_ten_pixels(self):
        for key, expected in (("Left", (-10, 500)), ("Right", (10, 500)), ("Up", (0, 490)), ("Down", (0, 510))):
            with self.subTest(key=key):
                self.assertEqual(nudge_point((0, 500), key, True, self.BOUNDS), expected)
        self.assertEqual(NUDGE_STEP_FAST, 10)

    def test_the_cursor_stops_on_the_last_pixel_of_the_screen(self):
        self.assertEqual(nudge_point((1919, 500), "Right", False, self.BOUNDS), (1919, 500))
        self.assertEqual(nudge_point((1915, 1075), "Down", True, self.BOUNDS), (1915, 1079))
        self.assertEqual(nudge_point((-1920, 3), "Up", True, self.BOUNDS), (-1920, 0))
        self.assertEqual(nudge_point((-1915, 3), "Left", True, self.BOUNDS), (-1920, 3))

    def test_every_bound_arrow_key_is_handled(self):
        for key in ARROW_KEYS:
            self.assertNotEqual(nudge_point((0, 500), key, False, self.BOUNDS), (0, 500))


class HotkeyTests(unittest.TestCase):
    def test_default_hotkey(self):
        self.assertEqual(parse_hotkey("ctrl+shift+a"), (MOD_CONTROL | MOD_SHIFT, ord("A")))

    def test_function_key_and_case_insensitivity(self):
        self.assertEqual(parse_hotkey("Ctrl+F12"), (MOD_CONTROL, 0x7B))

    def test_a_bare_key_is_rejected(self):
        with self.assertRaises(ConfigError):
            parse_hotkey("a")

    def test_unknown_modifier_and_key_are_rejected_by_name(self):
        with self.assertRaisesRegex(ConfigError, "'hyper'"):
            parse_hotkey("hyper+a")
        with self.assertRaisesRegex(ConfigError, "'enter'"):
            parse_hotkey("ctrl+enter")


class PrefixTests(unittest.TestCase):
    def test_plain_prefixes_are_accepted_and_stripped(self):
        self.assertEqual(validate_prefix("snap"), "snap")
        self.assertEqual(validate_prefix("  my shot-2 "), "my shot-2")
        self.assertEqual(validate_prefix("a" * MAX_PREFIX_LENGTH), "a" * MAX_PREFIX_LENGTH)

    def test_every_windows_filename_metacharacter_is_refused(self):
        for char in '<>:"/\\|?*':
            with self.subTest(char=char):
                with self.assertRaisesRegex(ConfigError, "cannot contain"):
                    validate_prefix(f"a{char}b")

    def test_control_characters_are_refused(self):
        with self.assertRaises(ConfigError):
            validate_prefix("a\tb")

    def test_empty_blank_and_too_long_are_refused(self):
        for text in ("", "   "):
            with self.subTest(text=text):
                with self.assertRaisesRegex(ConfigError, "empty"):
                    validate_prefix(text)
        with self.assertRaisesRegex(ConfigError, "limited"):
            validate_prefix("a" * (MAX_PREFIX_LENGTH + 1))
