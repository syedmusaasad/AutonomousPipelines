# Review Fix Summary

## Confirmed Cause
Bug: `if res.outcome != "ok" and verdict is None` allowed PASS artifacts from timed-out/failed reviewers.
- If a reviewer dispatch timed out, failed, or was killed but a review.md file existed (leftover or partial), the verdict would be read from that file instead of being marked UNAVAILABLE.
- The condition only set verdict to UNAVAILABLE when outcome was bad AND the file was missing, creating a false trust in stale files.

## Changed Paths
- `pipeline/engine.py` lines 405-417: Unconditional UNAVAILABLE on non-"ok" dispatch outcomes
- `tests/test_review_provenance.py`: New test file with 10 comprehensive tests
- `tests/harness.py`: Already had proper environment scrubbing (REVIEW_OUT isolation confirmed working)
- `tests/ratchet.json`: Updated test name references

## Core Fix
Changed logic from:
```python
verdict = read_verdict(out / "review.md")
if res.outcome != "ok" and verdict is None:
    verdict = "UNAVAILABLE"
```

To:
```python
if res.outcome != "ok":
    verdict = "UNAVAILABLE"
else:
    verdict = read_verdict(out / "review.md")
    if verdict is None:
        verdict = "UNAVAILABLE"
```

This makes failed/timed-out/killed reviewer dispatches unconditionally ineligible regardless of leftover files.

## Verification Results
- `python3 -m unittest discover -s tests -p test_review_provenance.py`: 10/10 PASS
- `python3 tests/run.py`: 104/104 PASS (including review_provenance suite integration)
- `bin/pipeline check-agents`: PASS

## Limitations
- This fix handles the current single-attempt review lifecycle (attempt=0 hardcoded).
- Future multi-attempt review cycles will need separate attempt directories (out-of-scope per brief).
- The fix does not implement OS-level sandboxing for reviewer processes.
