"""Estate GC: journal-aware sweeper for closed run/quick artifacts.

The journal is the authority (see plans/009-gc/plan.md DECISION gc-policy): a run
or quick is eligible for sweeping only when it is BOTH closed (journal's last row
is run.close) AND older than a buffer (default 72h) past its closed_at. Running,
gate-waiting, and dead-engine (open but process gone -- the sentry may relight it)
runs are never swept. Runs in the CURRENT conversation's lineage (per the runs
registry) are never swept either, however old.

Sweeping deletes only the ARTIFACTS: everything in a run directory except
journal.jsonl, STOPPED, and engine.lock, plus (for quick runs) the companion
scratch directory under quick_dir(). The journal itself, STOPPED receipts, the
runs registry, and anything under plans/ are never touched here -- this module
has no code path that opens those for writing.

Every sweep writes a manifest of what it is about to delete to
<estate>/logs/gc-manifest-<ts>.jsonl BEFORE deleting anything: a recoverable
record survives even a sweep that dies partway through."""

import os
import shutil
import time
from pathlib import Path

from . import paths, registry
from .journal import Journal, derive_state
from .util import append_jsonl, now_iso, now_ts, with_storm_armor

DEFAULT_BUFFER_S = 72 * 3600

# eligibility() status enum
ELIGIBLE_CLOSED = "eligible_closed"
KEEP_RUNNING = "keep_running"
KEEP_RECENT = "keep_recent"

_PROTECTED_NAMES = {"journal.jsonl", "STOPPED", "engine.lock"}


def eligibility(run_state: dict, *, now: float, buffer_s: float) -> str:
    """Pure function. run_state is a journal-derived state dict (or any dict with
    the same `closed` / `closed_at` shape): closed is the outcome string or None."""
    closed = run_state.get("closed")
    if closed is None:
        return KEEP_RUNNING
    closed_at = run_state.get("closed_at")
    if closed_at is None:
        # closed but no timestamp is not enough evidence to trust: keep it.
        return KEEP_RUNNING
    age = now - closed_at
    if age < buffer_s:
        return KEEP_RECENT
    return ELIGIBLE_CLOSED


def _artifact_paths(rdir: Path) -> list:
    if not rdir.exists():
        return []
    return sorted(p for p in rdir.iterdir() if p.name not in _PROTECTED_NAMES)


def _dir_size(path: Path) -> int:
    total = 0
    try:
        if path.is_symlink():
            return 0
        if path.is_file():
            return path.stat().st_size
        if path.is_dir():
            for root, _dirs, files in os.walk(path):
                for f in files:
                    fp = Path(root) / f
                    try:
                        if not fp.is_symlink():
                            total += fp.stat().st_size
                    except OSError:
                        continue
    except OSError:
        return total
    return total


def _artifact_bytes(rdir: Path) -> int:
    return sum(_dir_size(p) for p in _artifact_paths(rdir))


def _remove_path(p: Path) -> None:
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    else:
        p.unlink(missing_ok=True)


def plan(estate: Path = None, buffer_s: float = DEFAULT_BUFFER_S, *, quick_only: bool = False,
         now: float = None) -> tuple:
    """Walk runs_dir() (every run/quick lives there) and quick_dir() (the companion
    scratch workdir a quick run used). Returns (sweep_list, keep_list,
    saved_bytes_estimate, reasons). NEVER touches journals, receipts, or the
    registry -- read-only over all of it."""
    now = now_ts() if now is None else now
    estate = Path(estate) if estate else paths.estate_root()
    runs_d = estate / "runs"
    quick_d = estate / "quick"
    lineage = {r.get("run") for r in registry.for_conversation(registry.current_conversation())}
    sweep_list, keep_list, reasons = [], [], {}
    if not runs_d.exists():
        return sweep_list, keep_list, 0, reasons
    for rdir in sorted(p for p in runs_d.iterdir() if p.is_dir()):
        rid = rdir.name
        jpath = rdir / "journal.jsonl"
        if not jpath.exists():
            continue
        qdir = quick_d / rid
        has_qdir = qdir.exists()
        kind = "quick" if has_qdir else "plan"
        if quick_only and kind != "quick":
            continue
        rows = Journal(rid, path=jpath).rows()
        st = derive_state(rows)
        elig = eligibility(st, now=now, buffer_s=buffer_s)
        closed_at = st.get("closed_at")
        age_s = (now - closed_at) if closed_at is not None else None
        base = {"run": rid, "kind": kind, "rdir": str(rdir), "qdir": str(qdir) if has_qdir else None,
                "closed_at": closed_at, "age_s": age_s}

        def keep(reason: str):
            keep_list.append({**base, "reason": reason})
            reasons[rid] = reason

        if elig == KEEP_RUNNING:
            keep("open/running (or dead-engine awaiting relight)")
            continue
        if elig == KEEP_RECENT:
            keep(f"closed but within the {buffer_s / 3600:.1f}h buffer")
            continue
        # ELIGIBLE_CLOSED so far -- the journal-authority guards decide the rest.
        if not rows or rows[-1].get("event") != "run.close":
            keep("journal does not end with run.close (torn tail or post-close event)")
            continue
        if rid in lineage:
            keep("current conversation lineage")
            continue
        artifact_bytes = _artifact_bytes(rdir)
        if has_qdir:
            artifact_bytes += _dir_size(qdir)
        reason = "closed + past buffer; eligible for sweep"
        sweep_list.append({**base, "bytes": artifact_bytes, "reason": reason})
        reasons[rid] = reason
    saved_bytes = sum(i["bytes"] for i in sweep_list)
    return sweep_list, keep_list, saved_bytes, reasons


def sweep(sweep_items: list, *, estate: Path = None) -> dict:
    """Delete artifacts for every item in sweep_items (as produced by plan()).
    Writes the manifest BEFORE deleting anything: even a sweep that dies partway
    through leaves a complete record of what it was about to remove."""
    estate = Path(estate) if estate else paths.estate_root()
    logs = estate / "logs"

    def _mkdir():
        logs.mkdir(parents=True, exist_ok=True)

    with_storm_armor(_mkdir, what=f"gc mkdir {logs}")
    ts_str = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    manifest_path = logs / f"gc-manifest-{ts_str}.jsonl"

    to_delete = []  # (run, path) pairs, in manifest order
    for item in sweep_items:
        rdir = Path(item["rdir"])
        for p in _artifact_paths(rdir):
            to_delete.append((item["run"], item.get("kind"), p))
        qdir = item.get("qdir")
        if qdir and Path(qdir).exists():
            to_delete.append((item["run"], item.get("kind"), Path(qdir)))

    for run, kind, p in to_delete:
        row = {"ts": now_iso(), "run": run, "kind": kind, "path": str(p), "bytes": _dir_size(p), "is_dir": p.is_dir()}
        with_storm_armor(lambda row=row: append_jsonl(manifest_path, row), what=f"gc manifest {manifest_path}")

    deleted, errors = 0, []
    for run, kind, p in to_delete:
        try:
            with_storm_armor(lambda p=p: _remove_path(p), what=f"gc sweep {p}")
            deleted += 1
        except Exception as e:  # never let one bad path abort the sweep
            errors.append({"run": run, "path": str(p), "error": str(e)})
    return {"manifest": str(manifest_path), "deleted": deleted, "errors": errors, "runs": len(sweep_items)}
