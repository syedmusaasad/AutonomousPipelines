"""Deterministic functional tests for the reference repair fixture. Run with:
    python3 plans/005-token-efficiency/fixtures/repair/tests/test_calc.py
Exits 0 iff every assertion passes; prints one line per test."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import calc  # noqa: E402


def test_clamp():
    assert calc.clamp(5, 0, 10) == 5
    assert calc.clamp(-5, 0, 10) == 0
    assert calc.clamp(15, 0, 10) == 10


def test_normalize_min_max():
    out = calc.normalize([10, 20, 30])
    assert out[0] == 0.0, out
    assert out[-1] == 1.0, out


def test_normalize_values_between():
    out = calc.normalize([0, 5, 10])
    assert abs(out[1] - 0.5) < 1e-9, out


def main():
    tests = [test_clamp, test_normalize_min_max, test_normalize_values_between]
    failed = []
    for t in tests:
        try:
            t()
            print(f"ok    {t.__name__}")
        except Exception as e:
            failed.append(t.__name__)
            print(f"FAIL  {t.__name__}: {e!r}")
    print(f"{len(tests) - len(failed)}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
