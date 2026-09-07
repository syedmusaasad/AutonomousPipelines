"""Cloud tier for GC: export closed run artifacts to local cold storage and
Drive, verify byte-identical, then delete the run artifacts (see
plans/009-gc/plan.md DECISION cloud-tier). This EXTENDS gc, it does not replace
it: gc.plan() still decides what is eligible (journal-closed, past buffer, not
current lineage); this module adds an export -> upload -> VERIFY CHECKSUM ->
local-delete stage in front of gc.sweep().

Per run, export_plan():
  1. tar the artifact files (everything gc.sweep() would otherwise delete --
     NEVER journal.jsonl / STOPPED / engine.lock, those stay local forever)
      to the local cold volume at LOCAL_COLD/<run>.tar.gz
  2. `rclone copy` that tar to remote:pipeline-cold/<run>.tar.gz
  3. verify_upload(): compare the LOCAL sha256 of the tar against the REMOTE
     object's sha256 (via `rclone hashsum sha256`) -- byte-identical proof,
     not upload-success-only
  4. only if that verification passes: gc.sweep() the run's local artifacts,
      while keeping the local cold tar for fast retrieval

The manifest row (run id, tar sha256, local cold path, remote URL, artifact
byte count) is written to <estate>/logs/gc-cloud-manifest-<ts>.jsonl BEFORE
artifact deletion -- same discipline as gc.sweep()'s manifest-before-delete.

If rclone or the remote is unreachable at all: nothing is deleted, not one
local byte, for ANY run in the batch (checked up front via probe_remote()).
A per-run checksum mismatch keeps that run's tar and artifacts but does not
abort the rest of the batch."""

import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

from . import gc as gcmod, paths
from .util import append_jsonl, fs_probe, log, now_iso, with_storm_armor

RCLONE_BIN = "rclone"
REMOTE_DIR_NAME = "pipeline-cold"
LOCAL_COLD = Path("/mnt/HC_Volume_106815039/pipeline-cold")


class CloudTierError(Exception):
    """rclone unreachable, the remote unreachable, or an rclone invocation itself
    failed (not a checksum mismatch -- that is reported per-run instead). Callers
    must treat this as: delete nothing for the affected run(s)."""


def rclone_bin() -> str:
    return os.environ.get("PIPELINE_RCLONE_BIN", RCLONE_BIN)


def _run_rclone(args: list, timeout: float = 120) -> subprocess.CompletedProcess:
    argv = [rclone_bin(), *args]
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise CloudTierError(f"rclone unreachable running {' '.join(argv)}: {e}") from e


def local_cold_dir() -> Path:
    """Configured local cold archive directory.

    The environment override keeps the test harness and recovery operators off
    the production volume. It is resolved per call so a long-lived process can
    use a temporary override without reimporting this module.
    """
    return Path(os.environ.get("PIPELINE_LOCAL_COLD", str(LOCAL_COLD)))


def probe_local_cold() -> bool:
    """Return whether the local cold volume can create, read, and remove data."""
    return fs_probe(local_cold_dir())


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def probe_remote(remote: str) -> bool:
    """Cheap reachability probe run BEFORE any export work: `rclone lsd remote:`.
    Never raises; False on any failure (missing binary, auth failure, network)."""
    try:
        r = _run_rclone(["lsd", f"{remote}:"], timeout=30)
        return r.returncode == 0
    except CloudTierError:
        return False


def build_tar(item: dict, *, estate: Path = None, tar_dir: Path = None) -> Path:
    """Tar the artifact files for one gc-eligible run item (as produced by
    gc.plan()'s sweep_list) to tar_dir/<run>.tar.gz, defaulting to the local
    cold tier. Excludes
    journal.jsonl / STOPPED / engine.lock (gc._artifact_paths already filters
    those out) and includes the companion quick scratch dir if present.
    Written via a tmp file + atomic rename so a crash mid-tar never leaves a
    half-written archive at the real path."""
    estate = Path(estate) if estate else paths.estate_root()
    rid = item["run"]
    rdir = Path(item["rdir"])
    cdir = Path(tar_dir) if tar_dir is not None else local_cold_dir()

    def _mkdir():
        cdir.mkdir(parents=True, exist_ok=True)

    with_storm_armor(_mkdir, what=f"cloudtier mkdir {cdir}")
    tar_path = cdir / f"{rid}.tar.gz"
    tmp_path = tar_path.with_name(tar_path.name + f".tmp.{os.getpid()}")

    def _build():
        with tarfile.open(tmp_path, "w:gz") as tf:
            for p in gcmod._artifact_paths(rdir):
                tf.add(p, arcname=f"{rid}/{p.name}")
            qdir = item.get("qdir")
            if qdir and Path(qdir).exists():
                tf.add(Path(qdir), arcname=f"{rid}/quick")
        os.replace(tmp_path, tar_path)

    with_storm_armor(_build, what=f"cloudtier tar {tar_path}")
    return tar_path


def upload(tar_path: Path, remote: str) -> str:
    """`rclone copy` tar_path to remote:pipeline-cold/. Returns the remote URL
    string (remote:pipeline-cold/<name>) recorded in the manifest."""
    remote_dir = f"{remote}:{REMOTE_DIR_NAME}"
    r = _run_rclone(["copy", str(tar_path), remote_dir])
    if r.returncode != 0:
        raise CloudTierError(f"rclone copy to {remote_dir} failed (rc={r.returncode}): {(r.stderr or r.stdout).strip()}")
    return f"{remote_dir}/{tar_path.name}"


def remote_sha256(remote_url: str) -> str:
    """`rclone hashsum sha256` of exactly one remote object. Raises
    CloudTierError if rclone itself fails or returns nothing (object missing) --
    that is "unreachable", not "mismatch"."""
    r = _run_rclone(["hashsum", "sha256", remote_url])
    if r.returncode != 0:
        raise CloudTierError(f"rclone hashsum failed for {remote_url} (rc={r.returncode}): {(r.stderr or r.stdout).strip()}")
    out = r.stdout.strip()
    if not out:
        raise CloudTierError(f"rclone hashsum returned nothing for {remote_url}")
    # rclone hashsum output: "<hex-hash>  <name-or-path>"
    return out.splitlines()[0].split()[0].lower()


def verify_upload(tar_path: Path, remote_url: str) -> dict:
    """Byte-identical proof: local sha256 of tar_path vs the remote object's
    sha256 per rclone. Returns {"ok", "local_sha256", "remote_sha256"}. A
    MISMATCH is a normal reportable result (ok=False), not an exception -- but
    an unreachable rclone/remote during the hashsum call raises CloudTierError,
    since the caller must not confuse "couldn't check" with "checked and it
    doesn't match"."""
    local = sha256_file(tar_path)
    remote = remote_sha256(remote_url)
    return {"ok": local == remote, "local_sha256": local, "remote_sha256": remote}


def cold_get(run_id: str, *, estate: Path = None) -> tuple[str, int]:
    """Return the fast local archive path, or the recorded remote retrieval hint.

    The caller prints the returned text. A missing local archive is deliberately
    exit 4 even when the manifest identifies its Drive copy: restoration then
    requires an explicit rclone operation rather than pretending it is local.
    """
    tar_path = local_cold_dir() / f"{run_id}.tar.gz"
    if tar_path.is_file():
        return str(tar_path), 0

    estate = Path(estate) if estate else paths.estate_root()
    remote_hint = None
    for manifest_path in sorted((estate / "logs").glob("gc-cloud-manifest-*.jsonl")):
        try:
            for line in manifest_path.read_text().splitlines():
                row = json.loads(line)
                if row.get("run") == run_id and row.get("remote_url"):
                    remote_hint = row["remote_url"]
        except (OSError, json.JSONDecodeError):
            continue
    return remote_hint or f"remote:{REMOTE_DIR_NAME}/{run_id}.tar.gz", 4


def export_plan(gc_items: list, remote: str, *, estate: Path = None) -> dict:
    """Compose the cold-tier pipeline over a batch of gc-eligible items (as
    produced by gc.plan()'s sweep_list): for each item, tar to local cold ->
    upload -> verify -> gc.sweep() the run's local artifacts. If the cold
    volume is unavailable, /tmp is temporary staging and Drive remains the
    system of record.

    Raises CloudTierError up front (before touching any run) if the remote is
    unreachable -- nothing is deleted for anyone in that case. A per-run
    checksum mismatch is NOT raised: that run keeps its tar and its artifacts,
    is reported in `errors`, and the batch continues with the remaining runs.

    Returns {"uploaded": [{"run", "remote_url", "sha256"}...], "errors":
    [{"run", "error"}...], "manifest": <path str>}."""
    estate = Path(estate) if estate else paths.estate_root()
    logs = estate / "logs"

    if probe_local_cold():
        tar_dir = local_cold_dir()
        local_cold_available = True
    else:
        tar_dir = Path(tempfile.gettempdir()) / "pipeline-cold"
        local_cold_available = False
        log(f"cloudtier: local cold tier unreachable at {local_cold_dir()}; staging tar in {tar_dir}")

    if not probe_remote(remote):
        raise CloudTierError(f"remote '{remote}' unreachable via rclone; nothing exported, nothing deleted")

    def _mkdir():
        logs.mkdir(parents=True, exist_ok=True)

    with_storm_armor(_mkdir, what=f"cloudtier mkdir {logs}")
    ts_str = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    manifest_path = logs / f"gc-cloud-manifest-{ts_str}.jsonl"

    uploaded, errors = [], []
    for item in gc_items:
        rid = item["run"]
        try:
            tar_path = build_tar(item, estate=estate, tar_dir=tar_dir)
            remote_url = upload(tar_path, remote)
            result = verify_upload(tar_path, remote_url)
            if not result["ok"]:
                errors.append({"run": rid, "error": f"checksum mismatch: local={result['local_sha256']} remote={result['remote_sha256']}"})
                continue  # keep the tar AND the artifacts; nothing deleted for this run

            # Manifest row written BEFORE any local delete -- a recoverable
            # record survives even a death partway through the rest of this loop.
            row = {
                "ts": now_iso(), "run": rid, "tar_sha256": result["local_sha256"],
                "local_cold": str(tar_path), "remote_url": remote_url,
                "artifact_bytes": item.get("bytes", 0),
            }
            with_storm_armor(lambda row=row: append_jsonl(manifest_path, row), what=f"cloudtier manifest {manifest_path}")

            gcmod.sweep([item], estate=estate)
            if not local_cold_available:
                with_storm_armor(lambda p=tar_path: p.unlink(missing_ok=True), what=f"cloudtier staging tar delete {tar_path}")
            uploaded.append({"run": rid, "local_cold": str(tar_path), "remote_url": remote_url,
                             "sha256": result["local_sha256"]})
        except CloudTierError as e:
            errors.append({"run": rid, "error": str(e)})

    return {"uploaded": uploaded, "errors": errors, "manifest": str(manifest_path)}
