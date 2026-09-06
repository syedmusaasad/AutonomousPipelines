"""Unit tests for pipeline.tui.mouse: OSC52 payload shape and the click-drag
Selection buffer. Registered into the main suite by tests/test_suite.py."""

import base64
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.tui import mouse


class TestOsc52Payload(unittest.TestCase):

    def test_payload_shape_is_escape_c_base64_bel(self):
        payload = mouse.osc52_payload("hello")
        self.assertTrue(payload.startswith(mouse.OSC52_START))
        self.assertTrue(payload.endswith(mouse.OSC52_END))
        encoded = payload[len(mouse.OSC52_START):-len(mouse.OSC52_END)]
        self.assertEqual(base64.b64decode(encoded).decode("utf-8"), "hello")

    def test_empty_text_is_still_a_well_formed_payload(self):
        payload = mouse.osc52_payload("")
        self.assertEqual(payload, mouse.OSC52_START + mouse.OSC52_END)

    def test_none_text_treated_as_empty(self):
        payload = mouse.osc52_payload(None)
        self.assertEqual(payload, mouse.OSC52_START + mouse.OSC52_END)

    def test_unicode_text_roundtrips_through_base64(self):
        text = "héllo \u2603 world"
        payload = mouse.osc52_payload(text)
        encoded = payload[len(mouse.OSC52_START):-len(mouse.OSC52_END)]
        self.assertEqual(base64.b64decode(encoded).decode("utf-8"), text)

    def test_huge_selection_is_truncated_not_unbounded(self):
        text = "x" * (mouse.MAX_OSC52_BYTES * 2)
        payload = mouse.osc52_payload(text)
        encoded = payload[len(mouse.OSC52_START):-len(mouse.OSC52_END)]
        self.assertLessEqual(len(encoded), mouse.MAX_OSC52_BYTES)
        # framing survives truncation: still starts/ends correctly
        self.assertTrue(payload.startswith(mouse.OSC52_START))
        self.assertTrue(payload.endswith(mouse.OSC52_END))


class TestSelection(unittest.TestCase):

    def test_starts_empty_and_inactive(self):
        sel = mouse.Selection()
        self.assertFalse(sel.active)
        self.assertTrue(sel.is_empty())

    def test_begin_then_extend_tracks_anchor_and_cursor(self):
        sel = mouse.Selection()
        sel.begin(2, 3)
        self.assertTrue(sel.active)
        self.assertEqual(sel.anchor, (2, 3))
        sel.extend(2, 8)
        self.assertEqual(sel.cursor, (2, 8))
        self.assertEqual(sel.anchor, (2, 3))

    def test_extend_without_begin_implicitly_begins(self):
        sel = mouse.Selection()
        sel.extend(1, 1)
        self.assertTrue(sel.active)
        self.assertEqual(sel.anchor, (1, 1))

    def test_end_deactivates_but_keeps_the_range_for_copy(self):
        sel = mouse.Selection()
        sel.begin(0, 0)
        sel.extend(0, 5)
        sel.end()
        self.assertFalse(sel.active)
        self.assertEqual(sel.anchor, (0, 0))
        self.assertEqual(sel.cursor, (0, 5))

    def test_clear_wipes_the_range(self):
        sel = mouse.Selection()
        sel.begin(0, 0)
        sel.extend(0, 5)
        sel.clear()
        self.assertIsNone(sel.anchor)
        self.assertIsNone(sel.cursor)
        self.assertTrue(sel.is_empty())

    def test_ordered_normalizes_backward_drag(self):
        sel = mouse.Selection()
        sel.begin(3, 5)
        sel.extend(1, 2)
        start, end = sel.ordered()
        self.assertEqual(start, (1, 2))
        self.assertEqual(end, (3, 5))

    def test_single_row_selection_is_a_column_slice(self):
        sel = mouse.Selection()
        sel.begin(0, 1)
        sel.extend(0, 3)
        rows = ["abcdef"]
        self.assertEqual(sel.text(rows), "bcd")

    def test_multi_row_selection_joins_with_newlines(self):
        sel = mouse.Selection()
        sel.begin(0, 3)
        sel.extend(2, 1)
        rows = ["hello world", "middle row", "end line"]
        text = sel.text(rows)
        self.assertEqual(text, "lo world\nmiddle row\nen")

    def test_backward_drag_selects_the_same_text_as_forward(self):
        rows = ["hello world", "middle row", "end line"]
        fwd = mouse.Selection()
        fwd.begin(0, 3)
        fwd.extend(2, 1)
        bwd = mouse.Selection()
        bwd.begin(2, 1)
        bwd.extend(0, 3)
        self.assertEqual(fwd.text(rows), bwd.text(rows))

    def test_empty_selection_yields_empty_text(self):
        sel = mouse.Selection()
        self.assertEqual(sel.text(["abc"]), "")

    def test_selection_clamped_to_available_rows(self):
        sel = mouse.Selection()
        sel.begin(0, 0)
        sel.extend(5, 2)  # far past the end of `rows`
        rows = ["abc", "def"]
        text = sel.text(rows)
        self.assertIn("abc", text)
        self.assertIn("def", text)


class TestMouseMode(unittest.TestCase):

    def test_defaults_to_captured(self):
        mm = mouse.MouseMode()
        self.assertTrue(mm.captured)
        self.assertIn("pipeline", mm.hint())

    def test_toggle_flips_and_returns_new_state(self):
        mm = mouse.MouseMode()
        new_state = mm.toggle()
        self.assertFalse(mm.captured)
        self.assertFalse(new_state)
        self.assertIn("native-terminal", mm.hint())
        mm.toggle()
        self.assertTrue(mm.captured)

    def test_hint_names_both_states(self):
        mm = mouse.MouseMode(captured=True)
        self.assertIn("m:", mm.hint())
        mm.toggle()
        self.assertIn("m:", mm.hint())


if __name__ == "__main__":
    unittest.main()
