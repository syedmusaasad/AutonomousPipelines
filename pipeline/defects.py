"""Defect log: sessions append faults to DEFECTS.md under flock, fixers move entries
to # Fixed with the landing hash. The interactive agent and workers say nothing unless
blocked, then one line: blocked; logged to DEFECTS.md."""

import os
import re
import time
from pathlib import Path

from .util import append_jsonl, now_iso

SECTIONS = ("# Open", "# Fixed")


def defects_path(workdir: Path) -> Path:
    return Path(workdir) / "DEFECTS.md"


def append_defect(workdir: Path, session_id: str, scope: str, fault: str, mitigation: str, fix: str = "") -> None:
    p = defects_path(workdir)
    entry = f"## {now_iso()} {session_id} {scope}\nfault: {fault}\nmitigation: {mitigation}\nfix: {fix or '-'}\n"
    import fcntl
    p.parent.mkdir(parents=True, exist_ok=True)
    import os as _os
    created = False
    if not p.exists():
        try:
            fd = _os.open(p, _os.O_CREAT | _os.O_EXCL | _os.O_WRONLY, 0o644)
            _os.close(fd)
            created = True
        except FileExistsError:
            pass
    with open(p, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            text = f.read()
            if not text.strip():
                text = "# Open\n\n# Fixed\n"
            idx = text.find("# Fixed")
            head = text[:idx] if idx >= 0 else text
            tail = text[idx:] if idx >= 0 else "\n# Fixed\n"
            new = head.rstrip("\n") + "\n\n" + entry + "\n" + tail
            f.seek(0)
            f.write(new)
            f.truncate()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def open_count(workdir: Path) -> int:
    p = defects_path(workdir)
    if not p.exists():
        return 0
    text = p.read_text()
    if "# Open" not in text:
        return 0
    open_section = text.split("# Open", 1)[1].split("# Fixed", 1)[0]
    return len(re.findall(r"^## ", open_section, flags=re.M))


def entries(workdir: Path) -> list:
    p = defects_path(workdir)
    if not p.exists():
        return []
    text = p.read_text()
    out = []
    section = "Open"
    for line in text.splitlines():
        if line.strip() == "# Fixed":
            section = "Fixed"
        if line.startswith("## "):
            out.append({"section": section, "header": line[3:].strip()})
    return out


def fix_entry(workdir: Path, header_prefix: str, landing_hash: str) -> bool:
    p = defects_path(workdir)
    if not p.exists():
        return False
    text = p.read_text()
    open_text = text.split("# Fixed", 1)[0] if "# Fixed" in text else text
    fixed_text = text.split("# Fixed", 1)[1] if "# Fixed" in text else ""
    open_blocks = [b for b in re.split(r"(?=^## )", open_text, flags=re.M) if b.strip()]
    target = None
    for b in open_blocks:
        header_line = b.splitlines()[0] if b.splitlines() else ""
        if header_prefix in header_line:
            target = b.rstrip() + f"\nfixed-by: {landing_hash}\n"
            break
    if target is None:
        return False
    kept_open = [b for b in open_blocks if header_prefix not in (b.splitlines()[0] if b.splitlines() else "")]
    out = "# Open\n"
    for b in kept_open:
        out += "\n" + b.rstrip() + "\n"
    out += "\n# Fixed\n" + fixed_text.rstrip() + "\n\n" + target
    p.write_text(out)
    return True
