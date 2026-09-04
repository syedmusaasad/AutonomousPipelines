"""Estate layout. Sticky to one local disk: bootstrap records the estate root in
~/.system/estate and every process reads it from there. Nothing else may repoint it."""

import os
from pathlib import Path

ESTATE_POINTER = Path(os.environ.get("PIPELINE_HOME_POINTER", str(Path.home() / ".system" / "estate")))


def estate_root() -> Path:
    """Estate root. Order: PIPELINE_ESTATE env (tests/sentry), then the sticky pointer,
    then ~/.system. The pointer is written only by bootstrap."""
    env = os.environ.get("PIPELINE_ESTATE")
    if env:
        return Path(env)
    if ESTATE_POINTER.exists():
        p = ESTATE_POINTER.read_text().strip()
        if p:
            return Path(p)
    return Path.home() / ".system"


def system_dir() -> Path:
    return Path.home() / ".system"


def runs_dir() -> Path:
    return estate_root() / "runs"


def run_dir(run_id: str) -> Path:
    return runs_dir() / run_id


def journal_path(run_id: str) -> Path:
    return run_dir(run_id) / "journal.jsonl"


def runs_registry_path() -> Path:
    # Spec names ~/.system/runs.jsonl explicitly. If the estate is elsewhere we
    # still keep the registry inside the estate and symlink from ~/.system.
    return estate_root() / "runs.jsonl"


def quick_dir() -> Path:
    return estate_root() / "quick"


def state_dir() -> Path:
    return estate_root() / "state"


def logs_dir() -> Path:
    return estate_root() / "logs"


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def roles_dir() -> Path:
    return repo_root() / "roles"


def registers_dir() -> Path:
    return repo_root() / "registers"


def ensure_layout() -> None:
    for d in (estate_root(), runs_dir(), quick_dir(), state_dir(), logs_dir()):
        d.mkdir(parents=True, exist_ok=True)
