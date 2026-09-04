"""SURFACE scoring: prose artifacts scored against a writing-register standard.

A register is registers/<surface>.json:
    {"rules": "...prose for the writer...",
     "max_avg_sentence_words": 22, "max_sentence_words": 45,
     "max_passive_ratio": 0.15, "max_hedge_per_100_words": 1.0,
     "max_exclamations": 0, "forbidden": ["...phrases..."],
     "preamble_openers": ["In this document", ...],
     "min_words": 30}

score() returns metrics with pass/fail per metric and the quoted offending lines,
so a rewrite dispatch receives specifics, never just "try again"."""

import json
import re
from pathlib import Path

from . import paths

PASSIVE = re.compile(r"\b(is|are|was|were|be|been|being)\s+(\w+ed|\w+en)\b", re.I)
HEDGES = ("perhaps", "maybe", "somewhat", "arguably", "it seems", "it appears", "sort of", "kind of", "in some sense", "generally speaking")


class SurfaceError(ValueError):
    pass


def load_register(surface: str) -> dict:
    p = paths.registers_dir() / f"{surface}.json"
    if not p.exists():
        raise SurfaceError(f"unknown surface {surface!r}: {p} missing")
    return json.loads(p.read_text())


def _sentences(text: str) -> list:
    # drop code blocks and headings/bullets markers; split on sentence enders
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`[^`]*`", "x", text)
    lines = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or s.startswith("|") or s.startswith("---"):
            continue
        s = re.sub(r"^[-*+]\s+|^\d+\.\s+", "", s)
        lines.append(s)
    body = " ".join(lines)
    parts = re.split(r"(?<=[.!?])\s+", body)
    return [p.strip() for p in parts if len(p.split()) >= 2]


def score(text: str, register: dict) -> dict:
    sents = _sentences(text)
    words = sum(len(s.split()) for s in sents)
    lines = text.splitlines()
    metrics = {}
    offending = []

    def line_of(fragment: str) -> str:
        frag = fragment[:40].lower()
        for i, ln in enumerate(lines, 1):
            if frag and frag in ln.lower():
                return f"L{i}: {ln.strip()}"
        return f"?: {fragment[:120]}"

    def m(name, value, limit, ok, unit=""):
        metrics[name] = {"value": value, "limit": limit, "pass": ok, "unit": unit}

    m("words", words, register.get("min_words", 0), words >= register.get("min_words", 0), ">=")

    avg = (words / len(sents)) if sents else 0
    lim = register.get("max_avg_sentence_words", 25)
    m("avg_sentence_words", round(avg, 1), lim, avg <= lim, "<=")

    lim = register.get("max_sentence_words", 50)
    longest = [s for s in sents if len(s.split()) > lim]
    m("long_sentences", len(longest), 0, not longest, "==")
    offending += [f"long sentence ({len(s.split())} words) {line_of(s)}" for s in longest[:5]]

    passive = [s for s in sents if PASSIVE.search(s)]
    ratio = (len(passive) / len(sents)) if sents else 0
    lim = register.get("max_passive_ratio", 0.2)
    m("passive_ratio", round(ratio, 3), lim, ratio <= lim, "<=")
    if ratio > lim:
        offending += [f"passive voice {line_of(s)}" for s in passive[:5]]

    hedges = []
    low = text.lower()
    for h in HEDGES + tuple(register.get("hedges", [])):
        for mt in re.finditer(r"\b" + re.escape(h) + r"\b", low):
            hedges.append(text[max(0, mt.start() - 30): mt.end() + 30].replace("\n", " "))
    per100 = (len(hedges) / words * 100) if words else 0
    lim = register.get("max_hedge_per_100_words", 1.0)
    m("hedges_per_100_words", round(per100, 2), lim, per100 <= lim, "<=")
    if per100 > lim:
        offending += [f"hedge {line_of(h)}" for h in hedges[:5]]

    excl = text.count("!")
    lim = register.get("max_exclamations", 0)
    m("exclamations", excl, lim, excl <= lim, "<=")
    if excl > lim:
        offending += [f"exclamation {line_of(ln)}" for ln in lines if "!" in ln][:3]

    forb = []
    for phrase in register.get("forbidden", []):
        for i, ln in enumerate(lines, 1):
            if re.search(r"\b" + re.escape(phrase.lower()) + r"\b", ln.lower()):
                forb.append(f"forbidden phrase {phrase!r} L{i}: {ln.strip()}")
    m("forbidden_phrases", len(forb), 0, not forb, "==")
    offending += forb[:8]

    first = sents[0] if sents else ""
    openers = register.get("preamble_openers", [])
    bad_open = any(first.lower().startswith(o.lower()) for o in openers)
    m("first_sentence_states_outcome", 0 if bad_open else 1, 1, not bad_open, "==")
    if bad_open:
        offending.append(f"preamble opener {line_of(first)}")

    passed = all(v["pass"] for v in metrics.values())
    return {"pass": passed, "metrics": metrics, "offending": offending, "sentences": len(sents)}


def score_file(path: Path, surface: str) -> dict:
    reg = load_register(surface)
    res = score(path.read_text(errors="replace"), reg)
    res["file"] = str(path)
    res["surface"] = surface
    return res


def failure_report(results: list) -> str:
    """The text a rewrite dispatch receives: failing metrics with values vs limits and
    quoted offending lines, per file."""
    out = []
    for r in results:
        if r["pass"]:
            continue
        out.append(f"### {r['file']} (surface: {r['surface']})")
        out.append("Failing metrics:")
        for k, v in r["metrics"].items():
            if not v["pass"]:
                out.append(f"- {k}: {v['value']} (limit {v['unit']} {v['limit']})")
        if r["offending"]:
            out.append("Offending lines:")
            for o in r["offending"]:
                out.append(f"- {o}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"
