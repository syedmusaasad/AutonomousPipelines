"""Tests for UsageAccumulator: usage accounting from event streams."""

import json
import unittest

from pipeline.usage import UsageAccumulator


class TestUsageAccumulator(unittest.TestCase):
    """Tests for UsageAccumulator."""

    def test_single_step_complete(self):
        """Test a single complete step_finish event."""
        acc = UsageAccumulator()
        line = json.dumps({
            "type": "step_finish",
            "part": {
                "id": "p1",
                "tokens": {
                    "total": 150,
                    "input": 100,
                    "output": 20,
                    "reasoning": 5,
                    "cache": {
                        "read": 25,
                        "write": 0
                    }
                },
                "cost": 0.1
            }
        })
        acc.feed_line(line)
        snap = acc.snapshot()
        
        self.assertEqual(snap["provider_total"], 150)
        self.assertEqual(snap["input"], 100)
        self.assertEqual(snap["output"], 20)
        self.assertEqual(snap["reasoning"], 5)
        self.assertEqual(snap["cache_read"], 25)
        self.assertEqual(snap["cache_write"], 0)
        self.assertEqual(snap["cost"], 0.1)
        self.assertEqual(snap["steps"], 1)

    def test_multiple_steps(self):
        """Test accumulation across multiple step_finish events."""
        acc = UsageAccumulator()
        
        line1 = json.dumps({
            "type": "step_finish",
            "part": {
                "id": "p1",
                "tokens": {
                    "total": 100,
                    "input": 50,
                    "output": 30,
                    "reasoning": 10,
                    "cache": {"read": 10, "write": 0}
                },
                "cost": 0.05
            }
        })
        
        line2 = json.dumps({
            "type": "step_finish",
            "part": {
                "id": "p2",
                "tokens": {
                    "total": 200,
                    "input": 100,
                    "output": 50,
                    "reasoning": 20,
                    "cache": {"read": 30, "write": 5}
                },
                "cost": 0.1
            }
        })
        
        acc.feed_line(line1)
        acc.feed_line(line2)
        snap = acc.snapshot()
        
        self.assertEqual(snap["provider_total"], 300)
        self.assertEqual(snap["input"], 150)
        self.assertEqual(snap["output"], 80)
        self.assertEqual(snap["reasoning"], 30)
        self.assertEqual(snap["cache_read"], 40)
        self.assertEqual(snap["cache_write"], 5)
        self.assertAlmostEqual(snap["cost"], 0.15, places=10)
        self.assertEqual(snap["steps"], 2)

    def test_deduplication_by_id(self):
        """Test that duplicate step IDs are deduplicated."""
        acc = UsageAccumulator()
        
        line = json.dumps({
            "type": "step_finish",
            "part": {
                "id": "p1",
                "tokens": {"total": 100, "input": 50, "output": 30, "reasoning": 10, "cache": {"read": 10, "write": 0}},
                "cost": 0.05
            }
        })
        
        # Feed the same line twice
        acc.feed_line(line)
        acc.feed_line(line)
        snap = acc.snapshot()
        
        # Should only count once
        self.assertEqual(snap["steps"], 1)
        self.assertEqual(snap["provider_total"], 100)
        self.assertEqual(snap["cost"], 0.05)

    def test_steps_without_stable_id(self):
        """Test that steps without stable IDs are retained (no duplicate detection)."""
        acc = UsageAccumulator()
        
        line = json.dumps({
            "type": "step_finish",
            "part": {
                "tokens": {"total": 100, "input": 50, "output": 30, "reasoning": 10, "cache": {"read": 10, "write": 0}},
                "cost": 0.05
            }
        })
        
        # Feed the same line twice
        acc.feed_line(line)
        acc.feed_line(line)
        snap = acc.snapshot()
        
        # Should count both (no stable ID for deduplication)
        self.assertEqual(snap["steps"], 2)
        self.assertEqual(snap["provider_total"], 200)
        self.assertEqual(snap["cost"], 0.1)

    def test_missing_cache_fields(self):
        """Test handling of missing cache fields."""
        acc = UsageAccumulator()
        
        line = json.dumps({
            "type": "step_finish",
            "part": {
                "id": "p1",
                "tokens": {
                    "total": 100,
                    "input": 50,
                    "output": 30,
                    "reasoning": 10
                    # No cache object
                },
                "cost": 0.05
            }
        })
        
        acc.feed_line(line)
        snap = acc.snapshot()
        
        self.assertEqual(snap["cache_read"], 0)
        self.assertEqual(snap["cache_write"], 0)
        self.assertEqual(snap["steps"], 1)

    def test_zero_vs_missing_fields(self):
        """Test distinction between zero and missing fields."""
        acc = UsageAccumulator()
        
        # Step with explicit zero cache_write
        line1 = json.dumps({
            "type": "step_finish",
            "part": {
                "id": "p1",
                "tokens": {
                    "total": 100,
                    "input": 50,
                    "output": 30,
                    "reasoning": 10,
                    "cache": {"read": 10, "write": 0}
                },
                "cost": 0.05
            }
        })
        
        acc.feed_line(line1)
        snap = acc.snapshot()
        
        # cache_write seen as 0
        self.assertEqual(snap["cache_write"], 0)
        
        # missing_fields should not count cache_write since it was seen
        # Expected fields: provider_total, input, output, reasoning, cache_read, cache_write, cost (7 total)
        # Seen: provider_total, input, output, reasoning, cache_read, cache_write, cost (7)
        self.assertEqual(snap["missing_fields"], 0)

    def test_absent_cost_field(self):
        """Test handling when cost field is absent."""
        acc = UsageAccumulator()
        
        line = json.dumps({
            "type": "step_finish",
            "part": {
                "id": "p1",
                "tokens": {
                    "total": 100,
                    "input": 50,
                    "output": 30,
                    "reasoning": 10,
                    "cache": {"read": 10, "write": 0}
                }
                # No cost
            }
        })
        
        acc.feed_line(line)
        snap = acc.snapshot()
        
        self.assertEqual(snap["cost"], 0.0)
        self.assertEqual(snap["steps"], 1)
        # missing_fields should include cost
        self.assertGreater(snap["missing_fields"], 0)

    def test_malformed_json_line(self):
        """Test tolerance of malformed JSON lines."""
        acc = UsageAccumulator()
        
        # Malformed lines should be ignored
        acc.feed_line("{invalid json")
        acc.feed_line("not json at all")
        acc.feed_line("")
        
        snap = acc.snapshot()
        self.assertEqual(snap["steps"], 0)
        self.assertEqual(snap["provider_total"], 0)

    def test_non_step_event_ignored(self):
        """Test that non-step_finish events are ignored."""
        acc = UsageAccumulator()
        
        lines = [
            json.dumps({"type": "step_start", "part": {"id": "p1"}}),
            json.dumps({"type": "text", "part": {"text": "hello"}}),
            json.dumps({"type": "error", "error": "something"}),
        ]
        
        for line in lines:
            acc.feed_line(line)
        
        snap = acc.snapshot()
        self.assertEqual(snap["steps"], 0)
        self.assertEqual(snap["provider_total"], 0)

    def test_empty_stream(self):
        """Test handling of empty stream (no events fed)."""
        acc = UsageAccumulator()
        snap = acc.snapshot()
        
        # Empty stream should have all zeros but indicate unknown/incomplete
        self.assertEqual(snap["provider_total"], 0)
        self.assertEqual(snap["input"], 0)
        self.assertEqual(snap["output"], 0)
        self.assertEqual(snap["cost"], 0.0)
        self.assertEqual(snap["steps"], 0)
        # All fields are missing when no events are fed
        self.assertEqual(snap["missing_fields"], 7)
        self.assertFalse(snap["telemetry_complete"])

    def test_snapshot_immutability(self):
        """Test that snapshot() returns independent values."""
        acc = UsageAccumulator()
        
        line = json.dumps({
            "type": "step_finish",
            "part": {
                "id": "p1",
                "tokens": {"total": 100, "input": 50, "output": 30, "reasoning": 10, "cache": {"read": 10, "write": 0}},
                "cost": 0.05
            }
        })
        
        acc.feed_line(line)
        snap1 = acc.snapshot()
        snap1["provider_total"] = 999  # Mutate the snapshot
        
        snap2 = acc.snapshot()
        # Second snapshot should not be affected
        self.assertEqual(snap2["provider_total"], 100)

    def test_snapshot_json_serializable(self):
        """Test that snapshot can be JSON-serialized."""
        acc = UsageAccumulator()
        
        line = json.dumps({
            "type": "step_finish",
            "part": {
                "id": "p1",
                "tokens": {"total": 100, "input": 50, "output": 30, "reasoning": 10, "cache": {"read": 10, "write": 0}},
                "cost": 0.05
            }
        })
        
        acc.feed_line(line)
        snap = acc.snapshot()
        
        # Should be JSON-serializable
        serialized = json.dumps(snap)
        deserialized = json.loads(serialized)
        
        self.assertEqual(deserialized["provider_total"], 100)
        self.assertEqual(deserialized["cost"], 0.05)
        self.assertEqual(deserialized["steps"], 1)

    def test_partial_step_fields(self):
        """Test step with some token fields missing."""
        acc = UsageAccumulator()
        
        line = json.dumps({
            "type": "step_finish",
            "part": {
                "id": "p1",
                "tokens": {
                    "total": 100,
                    "input": 50
                    # missing output, reasoning, cache
                },
                "cost": 0.05
            }
        })
        
        acc.feed_line(line)
        snap = acc.snapshot()
        
        self.assertEqual(snap["provider_total"], 100)
        self.assertEqual(snap["input"], 50)
        self.assertEqual(snap["output"], 0)
        self.assertEqual(snap["reasoning"], 0)
        self.assertEqual(snap["cache_read"], 0)
        self.assertEqual(snap["steps"], 1)
        self.assertFalse(snap["telemetry_complete"])

    def test_null_vs_zero_cost(self):
        """Test handling of null vs zero vs missing cost."""
        acc1 = UsageAccumulator()
        acc2 = UsageAccumulator()
        acc3 = UsageAccumulator()
        
        # Case 1: cost = 0
        line1 = json.dumps({
            "type": "step_finish",
            "part": {
                "id": "p1",
                "tokens": {"total": 100, "input": 50, "output": 30, "reasoning": 10, "cache": {"read": 10, "write": 0}},
                "cost": 0
            }
        })
        
        # Case 2: cost = null
        line2 = json.dumps({
            "type": "step_finish",
            "part": {
                "id": "p2",
                "tokens": {"total": 100, "input": 50, "output": 30, "reasoning": 10, "cache": {"read": 10, "write": 0}},
                "cost": None
            }
        })
        
        # Case 3: cost missing
        line3 = json.dumps({
            "type": "step_finish",
            "part": {
                "id": "p3",
                "tokens": {"total": 100, "input": 50, "output": 30, "reasoning": 10, "cache": {"read": 10, "write": 0}}
            }
        })
        
        acc1.feed_line(line1)
        snap1 = acc1.snapshot()
        self.assertEqual(snap1["cost"], 0)
        
        acc2.feed_line(line2)
        snap2 = acc2.snapshot()
        self.assertEqual(snap2["cost"], 0)
        
        acc3.feed_line(line3)
        snap3 = acc3.snapshot()
        self.assertEqual(snap3["cost"], 0)

    def test_non_dict_part(self):
        """Test event with non-dict part."""
        acc = UsageAccumulator()
        
        line = json.dumps({
            "type": "step_finish",
            "part": "invalid_part"
        })
        
        acc.feed_line(line)
        snap = acc.snapshot()
        
        # Should be ignored
        self.assertEqual(snap["steps"], 0)

    def test_non_string_line(self):
        """Test that non-string input is ignored."""
        acc = UsageAccumulator()
        
        # These should not crash and should be ignored
        acc.feed_line(None)
        acc.feed_line(123)
        acc.feed_line([])
        
        snap = acc.snapshot()
        self.assertEqual(snap["steps"], 0)

    def test_incomplete_line_not_consumed(self):
        """Test that incomplete partial lines are not marked as consumed.
        
        The caller may retry once completed. This is implicit in the API:
        we should tolerate malformed lines without affecting state.
        """
        acc = UsageAccumulator()
        
        # Incomplete JSON line
        partial = '{"type":"step_finish","part":{'
        acc.feed_line(partial)
        
        snap = acc.snapshot()
        # Should not have counted anything
        self.assertEqual(snap["steps"], 0)
        
        # Now feed a complete line - state should not be affected by the partial
        complete = json.dumps({
            "type": "step_finish",
            "part": {
                "id": "p1",
                "tokens": {"total": 100, "input": 50, "output": 30, "reasoning": 10, "cache": {"read": 10, "write": 0}},
                "cost": 0.05
            }
        })
        acc.feed_line(complete)
        snap = acc.snapshot()
        self.assertEqual(snap["steps"], 1)

    def test_telemetry_complete_flag(self):
        """Test telemetry_complete flag behavior."""
        # Completely documented step
        acc1 = UsageAccumulator()
        line_complete = json.dumps({
            "type": "step_finish",
            "part": {
                "id": "p1",
                "tokens": {"total": 100, "input": 50, "output": 30, "reasoning": 10, "cache": {"read": 10, "write": 0}},
                "cost": 0.05
            }
        })
        acc1.feed_line(line_complete)
        snap1 = acc1.snapshot()
        self.assertTrue(snap1["telemetry_complete"])
        
        # Incomplete documentation (missing cost)
        acc2 = UsageAccumulator()
        line_incomplete = json.dumps({
            "type": "step_finish",
            "part": {
                "id": "p2",
                "tokens": {"total": 100, "input": 50, "output": 30, "reasoning": 10, "cache": {"read": 10, "write": 0}}
            }
        })
        acc2.feed_line(line_incomplete)
        snap2 = acc2.snapshot()
        self.assertFalse(snap2["telemetry_complete"])

    def test_multiple_cache_fields_present(self):
        """Test accumulated cache statistics."""
        acc = UsageAccumulator()
        
        line1 = json.dumps({
            "type": "step_finish",
            "part": {
                "id": "p1",
                "tokens": {"total": 100, "input": 50, "output": 30, "reasoning": 10, "cache": {"read": 100, "write": 50}},
                "cost": 0.05
            }
        })
        
        line2 = json.dumps({
            "type": "step_finish",
            "part": {
                "id": "p2",
                "tokens": {"total": 150, "input": 75, "output": 40, "reasoning": 20, "cache": {"read": 75, "write": 25}},
                "cost": 0.08
            }
        })
        
        acc.feed_line(line1)
        acc.feed_line(line2)
        snap = acc.snapshot()
        
        self.assertEqual(snap["cache_read"], 175)
        self.assertEqual(snap["cache_write"], 75)


if __name__ == "__main__":
    unittest.main()
