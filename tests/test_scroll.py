"""Unit tests for pipeline.tui.scroll: pure wrap calc + Viewport follow rules.
Registered into the main suite by tests/test_suite.py."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.tui import scroll


class TestWrapRow(unittest.TestCase):

    def test_empty_line_still_produces_one_visual_row(self):
        self.assertEqual(scroll.wrap_row("", 10), [""])
        self.assertEqual(scroll.wrap_row(None, 10), [""])

    def test_short_line_is_one_row(self):
        self.assertEqual(scroll.wrap_row("hi", 10), ["hi"])

    def test_long_line_wraps_at_width(self):
        rows = scroll.wrap_row("hello world", 5)
        self.assertEqual(rows, ["hello", "world"])

    def test_long_word_breaks_rather_than_overflowing(self):
        rows = scroll.wrap_row("abcdefghij", 4)
        self.assertEqual("".join(rows), "abcdefghij")
        for r in rows:
            self.assertLessEqual(len(r), 4)

    def test_embedded_newline_produces_separate_wrapped_groups(self):
        rows = scroll.wrap_row("ab\ncd", 10)
        self.assertEqual(rows, ["ab", "cd"])

    def test_width_is_clamped_to_at_least_one(self):
        rows = scroll.wrap_row("abc", 0)
        self.assertTrue(all(len(r) <= 1 for r in rows))
        self.assertEqual("".join(rows), "abc")


class TestWrapLines(unittest.TestCase):

    def test_flattens_multiple_logical_lines_in_order(self):
        out = scroll.wrap_lines(["a", "", "bb ccc"], 3)
        self.assertEqual(out, ["a", "", "bb", "ccc"])

    def test_empty_list_is_empty(self):
        self.assertEqual(scroll.wrap_lines([], 10), [])


class TestWrapLinesWithIndex(unittest.TestCase):

    def test_index_maps_each_visual_row_to_its_logical_line(self):
        visual, index = scroll.wrap_lines_with_index(["hello world", "short"], 5)
        self.assertEqual(visual, ["hello", "world", "short"])
        self.assertEqual(index, [0, 0, 1])

    def test_empty_list_yields_empty_pair(self):
        visual, index = scroll.wrap_lines_with_index([], 10)
        self.assertEqual(visual, [])
        self.assertEqual(index, [])


class TestVisualBounds(unittest.TestCase):

    def test_bounds_of_first_line(self):
        lines = ["hello world", "short"]
        start, end = scroll.visual_bounds(lines, 5, 0)
        self.assertEqual((start, end), (0, 2))  # "hello","world"

    def test_bounds_of_second_line_accounts_for_first_lines_wrap(self):
        lines = ["hello world", "short"]
        start, end = scroll.visual_bounds(lines, 5, 1)
        self.assertEqual((start, end), (2, 3))

    def test_out_of_range_index_returns_tail(self):
        lines = ["a", "b"]
        start, end = scroll.visual_bounds(lines, 10, 5)
        self.assertEqual(start, end)
        self.assertEqual(start, 2)


class TestViewport(unittest.TestCase):

    def test_starts_following_by_default(self):
        vp = scroll.Viewport()
        self.assertTrue(vp.follow)
        self.assertEqual(vp.offset, 0)

    def test_max_offset_is_total_minus_height_floored_at_zero(self):
        vp = scroll.Viewport()
        self.assertEqual(vp.max_offset(100, 20), 80)
        self.assertEqual(vp.max_offset(5, 20), 0)

    def test_clamp_keeps_offset_in_range(self):
        vp = scroll.Viewport()
        vp.offset = 500
        vp.clamp(100, 20)
        self.assertEqual(vp.offset, 80)
        vp.offset = -5
        vp.clamp(100, 20)
        self.assertEqual(vp.offset, 0)

    def test_sync_follow_repins_to_tail_while_following(self):
        vp = scroll.Viewport(follow=True)
        vp.sync_follow(50, 10)
        self.assertEqual(vp.offset, 40)
        vp.sync_follow(100, 10)  # content grew; still following
        self.assertEqual(vp.offset, 90)

    def test_sync_follow_only_clamps_when_not_following(self):
        vp = scroll.Viewport(follow=False)
        vp.offset = 30
        vp.sync_follow(100, 10)  # not following: stays put (clamped)
        self.assertEqual(vp.offset, 30)
        vp.sync_follow(20, 10)  # list shrank: re-clamped into range
        self.assertEqual(vp.offset, 10)

    def test_scroll_up_leaves_follow(self):
        vp = scroll.Viewport(follow=True)
        vp.offset = 80
        vp.scroll(-10, 100, 20)
        self.assertEqual(vp.offset, 70)
        self.assertFalse(vp.follow)

    def test_scroll_to_bottom_edge_reenters_follow(self):
        vp = scroll.Viewport(follow=False)
        vp.offset = 70
        vp.scroll(10, 100, 20)
        self.assertEqual(vp.offset, 80)
        self.assertTrue(vp.follow)

    def test_scroll_clamped_at_bounds(self):
        vp = scroll.Viewport()
        vp.offset = 0
        vp.scroll(-100, 100, 20)
        self.assertEqual(vp.offset, 0)
        vp.scroll(1000, 100, 20)
        self.assertEqual(vp.offset, 80)

    def test_page_up_always_clears_follow(self):
        vp = scroll.Viewport(follow=True)
        vp.offset = 80
        vp.page_up(100, 20)
        self.assertFalse(vp.follow)
        self.assertEqual(vp.offset, 61)

    def test_page_down_can_reenter_follow_at_bottom(self):
        vp = scroll.Viewport(follow=False)
        vp.offset = 0
        vp.page_down(20, 20)
        self.assertTrue(vp.follow)

    def test_home_goes_to_top_and_leaves_follow(self):
        vp = scroll.Viewport(follow=True)
        vp.offset = 80
        vp.home(100, 20)
        self.assertEqual(vp.offset, 0)
        self.assertFalse(vp.follow)

    def test_end_goes_to_bottom_and_enters_follow(self):
        vp = scroll.Viewport(follow=False)
        vp.offset = 0
        vp.end(100, 20)
        self.assertEqual(vp.offset, 80)
        self.assertTrue(vp.follow)

    def test_visible_range_clamps_and_bounds_by_total(self):
        vp = scroll.Viewport()
        vp.offset = 5
        start, end = vp.visible_range(10, 20)
        self.assertEqual((start, end), (0, 10))  # max_offset(10,20)=0, so re-clamped

    def test_is_at_bottom(self):
        vp = scroll.Viewport()
        vp.offset = 80
        self.assertTrue(vp.is_at_bottom(100, 20))
        vp.offset = 79
        self.assertFalse(vp.is_at_bottom(100, 20))

    def test_ensure_visible_scrolls_up_when_range_starts_above_offset(self):
        vp = scroll.Viewport()
        vp.offset = 20
        vp.ensure_visible(5, 8, 10)
        self.assertEqual(vp.offset, 5)

    def test_ensure_visible_scrolls_down_when_range_ends_below_offset_plus_height(self):
        vp = scroll.Viewport()
        vp.offset = 0
        vp.ensure_visible(15, 18, 10)
        self.assertEqual(vp.offset, 8)  # end(18) - height(10)

    def test_ensure_visible_leaves_offset_alone_when_already_onscreen(self):
        vp = scroll.Viewport()
        vp.offset = 10
        vp.ensure_visible(12, 14, 10)
        self.assertEqual(vp.offset, 10)

    def test_ensure_visible_does_not_change_follow(self):
        vp = scroll.Viewport(follow=True)
        vp.offset = 20
        vp.ensure_visible(5, 8, 10)
        self.assertTrue(vp.follow)


if __name__ == "__main__":
    unittest.main()
