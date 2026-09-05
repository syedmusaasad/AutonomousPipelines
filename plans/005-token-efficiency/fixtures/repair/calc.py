"""Tiny fixture module for the token-efficiency reference workload. Deliberately
carries one bug in `normalize`. `clamp` is already correct."""


def clamp(x, lo, hi):
    """Clamp x into [lo, hi]."""
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def normalize(values):
    """Scale a list of numbers into [0, 1] using min/max: the minimum value maps to
    0.0 and the maximum value maps to 1.0.

    BUG: divides by len(values) (the count of items) instead of the value span
    (hi - lo). This is only correct by accident when the span happens to equal
    len(values) - 1, which is not true for either test case below."""
    lo = min(values)
    hi = max(values)
    span = len(values)
    if span == 0:
        return [0.0 for _ in values]
    return [(v - lo) / span for v in values]
