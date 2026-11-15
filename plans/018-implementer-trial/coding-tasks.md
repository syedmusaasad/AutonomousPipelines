## Build a free-time finder for a day's calendar

Write a small Python module that finds the free gaps in a day, given the busy periods. Python standard library only. Write two files, directly in the output directory named by your brief: `slots.py` (the module) and `test_slots.py` (your own tests, runnable with `python3 -m unittest` from that directory, importing the module as `import slots` or `from slots import free_slots`). Write nothing else there, and do not touch the repository.

Interface: one public function.

    free_slots(busy, start, end, min_minutes=1) -> list[tuple[str, str]]

- Times are 24-hour `"HH:MM"` strings with exactly two digits for the hour (`00`-`23`) and two for the minute (`00`-`59`). Anything else (`"9:00"`, `"24:00"`, `"09:60"`, `"9am"`, a non-string) raises `ValueError`.
- `start` and `end` give the window to search, `start` strictly before `end`, otherwise `ValueError`.
- `busy` is any iterable of `(busy_start, busy_end)` pairs (tuples or two-item lists), in any order, possibly overlapping or touching. A pair whose end is before its start raises `ValueError`. A pair whose start equals its end is empty and is ignored.
- Busy time outside the window is ignored; a busy pair that crosses the window edge counts only for the part inside the window.
- Return the free gaps inside the window, earliest first, as `(gap_start, gap_end)` tuples of `"HH:MM"` strings. Overlapping or touching busy pairs act as one busy block, so two back-to-back meetings leave no zero-length gap.
- Only gaps lasting at least `min_minutes` whole minutes are returned (a gap of exactly `min_minutes` is returned). `min_minutes` must be an integer of at least 1 (a `bool` does not count), otherwise `ValueError`.
- No busy pairs at all returns the whole window as one gap (if it is long enough). A window fully covered by busy time returns `[]`.
- The function must not modify the `busy` argument.

Example: `free_slots([("09:00", "10:00"), ("09:30", "11:00"), ("13:00", "13:30")], "08:00", "17:00", 60)` returns `[("08:00", "09:00"), ("11:00", "13:00"), ("13:30", "17:00")]`.

Your tests should cover the edge cases above, not only the example.

## Fix two bugs in a request-log summariser

Below is `report.py`, a program that summarises a web-server request log. Two users have complained:

1. "I have a log with 12 good requests and 3 garbage lines. The average latency it prints is lower than the real average of the 12 requests. With no garbage lines the number is right."
2. "I have a log of exactly 20 requests whose latencies are 1 ms, 2 ms, ... 20 ms. The report shows a p95 latency of 20 ms. The 95th percentile by the nearest-rank rule is 19 ms. Other log sizes seem off in the same way when 95% of the requests is a whole number."

There are two real bugs, one behind each complaint. Find them and fix them with the smallest change that makes the program match its own docstrings. Do not change anything that already works: the function names, signatures, return keys, output format and the CLI stay exactly as they are. Python standard library only.

Write two files, directly in the output directory named by your brief: `report.py` (the complete corrected program) and `test_report.py` (your regression tests, runnable with `python3 -m unittest` from that directory, importing the program as `import report` or `from report import summarize`). The tests must fail on the original program below and pass on your corrected one. Write nothing else there, and do not touch the repository.

```python
"""Summarise a web-server request log.

Each log line looks like:

    2026-11-01T10:00:03 GET /api/users 200 123ms

Blank lines and malformed lines are ignored.
"""

import sys

METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"}


def parse_line(line):
    """Return a dict with method, path, status and ms, or None if the line is malformed."""
    parts = line.split()
    if len(parts) != 5:
        return None
    _stamp, method, path, status, latency = parts
    if method not in METHODS or not path.startswith("/"):
        return None
    if not status.isdigit() or len(status) != 3:
        return None
    if not latency.endswith("ms") or not latency[:-2].isdigit():
        return None
    return {"method": method, "path": path, "status": int(status), "ms": int(latency[:-2])}


def percentile(values, pct):
    """Nearest-rank percentile: the smallest value such that at least pct% of values are <= it."""
    if not values:
        return 0
    ordered = sorted(values)
    index = int(len(ordered) * pct / 100)
    return ordered[min(index, len(ordered) - 1)]


def summarize(lines):
    """Return a dict summarising the log lines.

    Keys: requests (parsed lines), skipped (non-blank lines that did not parse),
    by_class (count per status class such as "2xx"), avg_ms (mean latency, rounded to
    1 decimal place), p95_ms, and slowest (the path with the highest mean latency, ties
    broken alphabetically, or None for an empty log).
    """
    entries = []
    skipped = 0
    for line in lines:
        if not line.strip():
            continue
        entry = parse_line(line)
        if entry is None:
            skipped += 1
        else:
            entries.append(entry)

    by_class = {}
    for e in entries:
        key = f"{e['status'] // 100}xx"
        by_class[key] = by_class.get(key, 0) + 1

    times = [e["ms"] for e in entries]
    total = sum(times)
    avg = round(total / (len(entries) + skipped), 1) if entries else 0.0

    per_path = {}
    for e in entries:
        per_path.setdefault(e["path"], []).append(e["ms"])
    slowest = None
    best = -1.0
    for path in sorted(per_path):
        mean = sum(per_path[path]) / len(per_path[path])
        if mean > best:
            best = mean
            slowest = path

    return {
        "requests": len(entries),
        "skipped": skipped,
        "by_class": by_class,
        "avg_ms": avg,
        "p95_ms": percentile(times, 95),
        "slowest": slowest,
    }


def format_report(summary):
    """Render a summary as text, one fact per line."""
    lines = [
        f"requests: {summary['requests']}",
        f"skipped: {summary['skipped']}",
        f"average latency: {summary['avg_ms']} ms",
        f"p95 latency: {summary['p95_ms']} ms",
        f"slowest path: {summary['slowest'] or '-'}",
    ]
    for key in sorted(summary["by_class"]):
        lines.append(f"{key}: {summary['by_class'][key]}")
    return "\n".join(lines)


def main(argv):
    if len(argv) != 2:
        print("usage: report.py LOGFILE", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        print(format_report(summarize(fh)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

## Add expiry to an LRU cache without breaking it

Below is a working module `lru.py` and its tests `test_lru.py`. Add time-based expiry to the cache. Python standard library only.

Write two files, directly in the output directory named by your brief: `lru.py` (the extended module) and `test_lru.py` (the tests below, kept and still passing unchanged, plus your own tests for the new feature added to the same file). Runnable with `python3 -m unittest` from that directory. Write nothing else there, and do not touch the repository. Keep the existing behaviour, names and style; add only the feature below.

The feature:

- `LRUCache(capacity, ttl=None, clock=time.monotonic)`. `ttl` is the default lifetime in seconds of every entry; `None` means entries never expire (the cache then behaves exactly as it does today). `clock` is a zero-argument function returning the current time as a number; it is how tests control time.
- `put(key, value, ttl=None)` gains an optional per-entry `ttl` that overrides the cache default for that entry when it is not `None`.
- A `ttl`, whether given to the constructor or to `put`, must be `None` or a positive `int` or `float` (a `bool` does not count); anything else raises `ValueError`. An invalid `put` ttl must raise before the cache changes in any way.
- An entry stored at time `t` with lifetime `T` is expired from the moment `clock()` reaches `t + T` (at exactly `t + T` it is already expired).
- Every `put` of a key, new or replacing, restarts that entry's lifetime. `get` does not extend it.
- Expired entries are invisible: `get` returns the default, `in` is `False`, `len()` and `keys()` leave them out, and `delete` of an expired key returns `False`. `get` of an expired key also removes it, and must not change the order of the other entries.
- When a new key is put into a full cache, expired entries are removed first; an unexpired entry is evicted (least recently used first) only if the cache is still full afterwards.
- New method `purge()` removes every expired entry and returns how many it removed.

`lru.py`:

```python
"""A small least-recently-used cache."""

from collections import OrderedDict

_MISSING = object()


class LRUCache:
    """Hold at most `capacity` items; the least recently used item is evicted first.

    Both get() and put() count as a use. Looking at a key with `in` or len() does not.
    """

    def __init__(self, capacity):
        if not isinstance(capacity, int) or capacity < 1:
            raise ValueError("capacity must be a positive integer")
        self.capacity = capacity
        self._data = OrderedDict()

    def get(self, key, default=None):
        """Return the value for key and mark it most recently used, or `default` if absent."""
        if key not in self._data:
            return default
        self._data.move_to_end(key)
        return self._data[key]

    def put(self, key, value):
        """Store value under key (replacing any old value) and mark it most recently used.

        If the cache is full, the least recently used other item is evicted first.
        """
        if key in self._data:
            self._data.move_to_end(key)
        elif len(self._data) >= self.capacity:
            self._data.popitem(last=False)
        self._data[key] = value

    def delete(self, key):
        """Remove key; return True if it was present, False otherwise."""
        return self._data.pop(key, _MISSING) is not _MISSING

    def keys(self):
        """Return the keys from least to most recently used."""
        return list(self._data)

    def __contains__(self, key):
        return key in self._data

    def __len__(self):
        return len(self._data)
```

`test_lru.py`:

```python
import unittest

from lru import LRUCache


class LRUCacheTests(unittest.TestCase):
    def test_put_and_get(self):
        c = LRUCache(2)
        c.put("a", 1)
        self.assertEqual(c.get("a"), 1)
        self.assertIsNone(c.get("zzz"))
        self.assertEqual(c.get("zzz", 7), 7)

    def test_evicts_least_recently_used(self):
        c = LRUCache(2)
        c.put("a", 1)
        c.put("b", 2)
        c.get("a")
        c.put("c", 3)
        self.assertEqual(c.keys(), ["a", "c"])
        self.assertNotIn("b", c)

    def test_put_existing_key_replaces_without_evicting(self):
        c = LRUCache(2)
        c.put("a", 1)
        c.put("b", 2)
        c.put("a", 10)
        self.assertEqual(len(c), 2)
        self.assertEqual(c.get("a"), 10)
        self.assertEqual(c.keys(), ["b", "a"])

    def test_delete(self):
        c = LRUCache(2)
        c.put("a", 1)
        self.assertTrue(c.delete("a"))
        self.assertFalse(c.delete("a"))
        self.assertEqual(len(c), 0)

    def test_contains_and_len_do_not_count_as_use(self):
        c = LRUCache(2)
        c.put("a", 1)
        c.put("b", 2)
        self.assertIn("a", c)
        self.assertEqual(len(c), 2)
        c.put("c", 3)
        self.assertEqual(c.keys(), ["b", "c"])

    def test_bad_capacity(self):
        for bad in (0, -1, 1.5, "2", None):
            with self.assertRaises(ValueError):
                LRUCache(bad)


if __name__ == "__main__":
    unittest.main()
```
