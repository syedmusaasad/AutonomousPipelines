"""Roblox agent skills: roblox/skills/<name>/SKILL.md in the repo is the source of
truth; install_skills() syncs the whole tree to the global skills dir
(~/.config/devpass-code/skills/) and check_sync() is the diff guard — same pattern
as pipeline.roles' generated-agent-file drift guard."""

import os
from pathlib import Path

from . import paths


def source_dir() -> Path:
    return paths.repo_root() / "roblox" / "skills"


def skills_dir() -> Path:
    """Global skills directory workers read from. Override with
    PIPELINE_ROBLOX_SKILLS_DIR (tests); otherwise ~/.config/devpass-code/skills."""
    override = os.environ.get("PIPELINE_ROBLOX_SKILLS_DIR")
    return Path(override) if override else Path.home() / ".config" / "devpass-code" / "skills"


def source_files() -> dict:
    """{relative_path (posix str, e.g. 'luau-conventions/SKILL.md'): text} for every
    file under roblox/skills/. Skipped entirely (empty dict) if the source dir is
    missing so a fresh checkout without roblox/ doesn't explode."""
    src = source_dir()
    if not src.is_dir():
        return {}
    out = {}
    for p in sorted(src.rglob("*")):
        if p.is_file():
            rel = p.relative_to(src).as_posix()
            out[rel] = p.read_text()
    return out


def install_skills(dest: Path = None) -> list:
    """Sync roblox/skills -> dest (default: skills_dir()). Writes every source file;
    returns the list of paths written."""
    dest = dest or skills_dir()
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for rel, text in source_files().items():
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        written.append(p)
    return written


def drift(dest: Path = None) -> list:
    """Files whose on-disk content differs from the repo source (or are missing).
    Does not report extra files present in dest but absent from source — install is
    additive/overwrite, not a mirror-delete."""
    dest = dest or skills_dir()
    bad = []
    for rel, text in source_files().items():
        p = dest / rel
        if not p.exists() or p.read_text() != text:
            bad.append(str(p))
    return bad


def check_sync(dest: Path = None) -> bool:
    """True if the global skills dir matches roblox/skills/ exactly (no drift)."""
    return drift(dest) == []
