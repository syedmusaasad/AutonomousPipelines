"""UsageAccumulator: standalone accounting for token and cost usage from event streams.

Accumulates usage metrics from step_finish events in JSON event streams.
Maintains separate tracking for provider-reported totals, component breakdowns,
cache operations, and identifies missing or incomplete fields.
"""

import json
from typing import Any, Dict, Optional


class UsageAccumulator:
    """Accumulates usage from JSON event lines.
    
    API:
    - feed_line(line: str): consume one JSON event line (step_finish events only)
    - snapshot(): return independent cumulative values dict
    
    Deduplicates step_finish by part.id when available.
    Retains zero vs missing distinction.
    Tolerates malformed lines.
    """

    def __init__(self):
        # Track provider-reported totals (never reconstructed)
        self._provider_total = 0  # total from part.tokens.total if present
        
        # Component breakdowns
        self._input = 0
        self._output = 0
        self._reasoning = 0
        
        # Cache operations
        self._cache_read = 0
        self._cache_write = 0
        
        # Cost
        self._cost = 0.0
        
        # Step counting
        self._steps = 0
        
        # Track which fields have been seen (to distinguish missing from zero)
        self._seen_fields = set()
        
        # Deduplication by part.id
        self._seen_ids = set()
        
        # Track if all completed steps had complete telemetry
        self._all_telemetry_complete = True

    def feed_line(self, line: str) -> None:
        """Consume one JSON event line.
        
        Only step_finish events contribute usage.
        Deduplicates by part.id if present.
        Tolerates malformed lines and non-step events.
        Does not mark incomplete partial line as consumed.
        """
        if not line or not isinstance(line, str):
            return
        
        line = line.strip()
        if not line or not line.startswith("{"):
            return
        
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            # Tolerate malformed lines
            return
        
        if not isinstance(event, dict):
            return
        
        # Only process step_finish events
        if event.get("type") != "step_finish":
            return
        
        part = event.get("part")
        if not isinstance(part, dict):
            return
        
        # Check for deduplication
        part_id = part.get("id")
        if part_id is not None:
            if part_id in self._seen_ids:
                # Already processed this step
                return
            self._seen_ids.add(part_id)
        
        # Extract token information
        tokens = part.get("tokens")
        if not isinstance(tokens, dict):
            tokens = {}
        
        # Track provider_total (sum reported by provider)
        provider_total = tokens.get("total")
        if provider_total is not None:
            try:
                provider_total = int(provider_total)
                self._provider_total += provider_total
                self._seen_fields.add("provider_total")
            except (TypeError, ValueError):
                pass
        
        # Track input, output, reasoning
        for field_name in ["input", "output", "reasoning"]:
            value = tokens.get(field_name)
            if value is not None:
                try:
                    value = int(value)
                    if field_name == "input":
                        self._input += value
                    elif field_name == "output":
                        self._output += value
                    elif field_name == "reasoning":
                        self._reasoning += value
                    self._seen_fields.add(field_name)
                except (TypeError, ValueError):
                    pass
        
        # Track cache operations
        cache = tokens.get("cache")
        if isinstance(cache, dict):
            for cache_field in ["read", "write"]:
                value = cache.get(cache_field)
                if value is not None:
                    try:
                        value = int(value)
                        if cache_field == "read":
                            self._cache_read += value
                        elif cache_field == "write":
                            self._cache_write += value
                        self._seen_fields.add(f"cache_{cache_field}")
                    except (TypeError, ValueError):
                        pass
        
        # Track cost
        cost = part.get("cost")
        if cost is not None:
            try:
                cost = float(cost)
                self._cost += cost
                self._seen_fields.add("cost")
            except (TypeError, ValueError):
                pass
        
        # Increment step count for each completed step_finish
        self._steps += 1
        
        # Check telemetry completeness: do we have all key fields?
        # A complete telemetry report should have: total, input, output, cost
        has_provider_total = "provider_total" in self._seen_fields and tokens.get("total") is not None
        has_input = "input" in self._seen_fields and tokens.get("input") is not None
        has_output = "output" in self._seen_fields and tokens.get("output") is not None
        has_cost = "cost" in self._seen_fields and part.get("cost") is not None
        
        if not (has_provider_total and has_input and has_output and has_cost):
            self._all_telemetry_complete = False

    def snapshot(self) -> Dict[str, Any]:
        """Return independent cumulative values.
        
        The returned dict is independent; mutations do not affect accumulator state.
        
        Returns:
            Dictionary with keys:
            - provider_total: sum of reported provider totals
            - input: sum of input tokens
            - output: sum of output tokens
            - reasoning: sum of reasoning tokens
            - cache_read: sum of cache read tokens
            - cache_write: sum of cache write tokens
            - cost: sum of costs
            - steps: count of completed step_finish events
            - missing_fields: count of fields not yet seen (to detect incomplete measurement)
            - telemetry_complete: bool, true if all steps had complete telemetry
        """
        # Calculate which fields are missing
        expected_fields = {
            "provider_total", "input", "output", "reasoning",
            "cache_read", "cache_write", "cost"
        }
        missing_fields = len(expected_fields - self._seen_fields)
        
        # telemetry_complete is only true if we processed steps and all had complete telemetry
        telemetry_complete = self._steps > 0 and self._all_telemetry_complete
        
        return {
            "provider_total": self._provider_total,
            "input": self._input,
            "output": self._output,
            "reasoning": self._reasoning,
            "cache_read": self._cache_read,
            "cache_write": self._cache_write,
            "cost": self._cost,
            "steps": self._steps,
            "missing_fields": missing_fields,
            "telemetry_complete": telemetry_complete,
        }
