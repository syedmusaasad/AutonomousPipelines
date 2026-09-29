#!/usr/bin/env python3
"""Hygiene gates for the suite: README claims, drift, verbosity, leaks. Each gate
returns a list of failure strings; empty means pass."""

import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent

VERBOSITY_CEILINGS = {"interactive": 2000}
DEFAULT_CEILING = 4000


def readme_claims_failures() -> list:
    failures = []
    readme = (HERE / "README.md")
    if not readme.exists():
        return ["README.md missing"]
    ledger = json.loads((HERE / "tests" / "ratchet.json").read_text())
    known = " ".join(ledger.get("required", []))
    text = readme.read_text()
    claims = re.findall(r"^#+\s+(.*)$", text, flags=re.M)
    behavioral = [c for c in claims if any(w in c.lower() for w in ("runs", "refuses", "enforces", "serializes", "records", "shows", "kills", "stops"))]
    for c in behavioral[:40]:
        words = [w for w in re.findall(r"[a-z_]{5,}", c.lower()) if w not in ("which", "every", "under", "where", "their")]
        if words and not any(w in known for w in words):
            failures.append(f"README heading has no ratchet test mapping: {c!r}")
    return failures


def drift_failures() -> list:
    from pipeline import roles as roles_mod
    bad = roles_mod.drift()
    return [f"generated agent file drifts from registry: {p}" for p in bad]


def verbosity_failures() -> list:
    failures = []
    for p in sorted((HERE / "roles").glob("*.md")):
        if p.name == "CONTRACT.md":
            continue
        words = len(p.read_text().split())
        role = p.stem
        ceiling = VERBOSITY_CEILINGS.get(role, DEFAULT_CEILING)
        if words > ceiling:
            failures.append(f"role prompt {role} is {words} words, over ceiling {ceiling}")
    ceilings = json.loads((HERE / "tests" / "ratchet.json").read_text()).get("verbosity_ceilings", {})
    for role, ceiling in ceilings.items():
        if ceiling > VERBOSITY_CEILINGS.get(role, DEFAULT_CEILING):
            failures.append(f"verbosity ceiling for {role} rose: {ceiling}")
    return failures


def leak_failures() -> list:
    failures = []
    out = subprocess.run(["git", "-C", str(HERE), "ls-files"], capture_output=True, text=True).stdout
    home = str(Path.home())
    hostname = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
    username = Path.home().name
    for f in out.splitlines():
        if f.startswith(("plans/", "docs/")):
            continue
        p = HERE / f
        if not p.is_file():
            continue
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        if home in text:
            failures.append(f"absolute home path leak in {f}")
        if hostname and hostname in text and not f.endswith(".lock"):
            failures.append(f"hostname leak in {f}")
        if re.search(r"/home/" + re.escape(username) + r"\b", text):
            failures.append(f"operator username path leak in {f}")
    return failures


ALL_GATES = {
    "readme-claims": readme_claims_failures,
    "drift": drift_failures,
    "verbosity": verbosity_failures,
    "leaks": leak_failures,
}
