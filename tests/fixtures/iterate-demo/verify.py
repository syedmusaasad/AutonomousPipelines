#!/usr/bin/env python3
"""Verify every deterministic migration task has completed."""

from pathlib import Path
import sys


missing = [f"migration/item-{number}.done" for number in range(1, 5)
           if not Path(f"migration/item-{number}.done").is_file()]
if missing:
    print("missing: " + ", ".join(missing))
    sys.exit(1)

print("migration verified")
