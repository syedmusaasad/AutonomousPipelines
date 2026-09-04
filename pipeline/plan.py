"""Plan grammar. Line-based, parsed, enforced: a capability not written into a plan
does not run.

    ## Phase N: Name (role)        one phase, one role. role=gate -> operator sentinel
    EXIT: <shell predicate>        engine runs after worker; phase cannot complete while it fails
    AFTER: 1, 2                    explicit deps; phases whose deps are met run concurrently
    TIMEOUT: <seconds>             per phase (per dispatch attempt)
    LANES: <items-file>            fan out one dispatch per non-empty line of the file
    CEILING: <n>                   max concurrent lanes
    REVIEW: cross                  two reviewers on different model families after EXIT
    SURFACE: <glob> <surface>      score prose artifacts against a register standard
    GATE: <sentinel-path>          (gate phases) the file the operator must write
    ATTEMPTS: <n>                  worker attempts before the phase burns (default 2)
    MODEL: <provider/model>        pin the model (trials only; seats otherwise come from the registry)
    EFFORT: low|medium|high        override the seat's effort/reasoning variant for this phase

Everything else under a phase heading is the brief: task facts handed to the worker.
Free text before the first heading is the plan preamble (shared context). The
preamble may carry `WORKDIR: <path>` (worker cwd; default is the plan's directory)
and `DECISION <name>: ...` lines recording named operator decisions."""

import re
from dataclasses import dataclass, field
from pathlib import Path

HEADING = re.compile(r"^##\s*Phase\s+(\d+)\s*:\s*(.+?)\s*\((\w[\w-]*)\)\s*$")
DIRECTIVE = re.compile(r"^([A-Z]+):\s*(.*?)\s*$")
KNOWN = {"EXIT", "AFTER", "TIMEOUT", "LANES", "CEILING", "REVIEW", "SURFACE", "GATE", "ATTEMPTS", "MODEL", "EFFORT"}

DEFAULT_TIMEOUT = 1800
DEFAULT_ATTEMPTS = 2
DEFAULT_CEILING = 2
GATE_ROLE = "gate"


class PlanError(ValueError):
    pass


@dataclass
class Phase:
    number: int
    name: str
    role: str
    brief: str = ""
    exits: list = field(default_factory=list)
    after: list = field(default_factory=list)
    after_explicit: bool = False
    timeout: int = DEFAULT_TIMEOUT
    lanes: str = None
    ceiling: int = DEFAULT_CEILING
    review: str = None
    surfaces: list = field(default_factory=list)  # [(glob, surface)]
    gate: str = None
    attempts: int = DEFAULT_ATTEMPTS
    model: str = None
    effort: str = None
    line: int = 0

    @property
    def key(self) -> str:
        return str(self.number)

    @property
    def is_gate(self) -> bool:
        return self.role == GATE_ROLE

    def default_gate_sentinel(self, plan_dir: Path) -> Path:
        return plan_dir / f".gate-{self.number}"


@dataclass
class Plan:
    path: Path
    preamble: str
    phases: list
    title: str = ""

    def by_number(self, n) -> Phase:
        for p in self.phases:
            if p.number == int(n):
                return p
        raise KeyError(n)

    @property
    def dir(self) -> Path:
        return self.path.parent

    @property
    def workdir(self) -> Path:
        for ln in self.preamble.splitlines():
            m = re.match(r"^WORKDIR:\s*(\S+)\s*$", ln.strip())
            if m:
                p = Path(m.group(1)).expanduser()
                return p if p.is_absolute() else (self.dir / p).resolve()
        return self.dir

    @property
    def decisions(self) -> list:
        return [ln.strip() for ln in self.preamble.splitlines() if ln.strip().startswith("DECISION ")]


def parse_text(text: str, path: Path = Path("plan.md")) -> Plan:
    phases: list = []
    preamble: list = []
    title = ""
    cur: Phase = None
    brief_lines: list = []

    def flush():
        if cur is not None:
            cur.brief = "\n".join(brief_lines).strip()

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip("\n")
        m = HEADING.match(line)
        if m:
            flush()
            n, name, role = int(m.group(1)), m.group(2), m.group(3)
            if any(p.number == n for p in phases):
                raise PlanError(f"line {lineno}: duplicate phase number {n}")
            cur = Phase(number=n, name=name, role=role, line=lineno)
            phases.append(cur)
            brief_lines = []
            continue
        if cur is None:
            if line.startswith("# ") and not title:
                title = line[2:].strip()
            preamble.append(line)
            continue
        if line.startswith("## "):
            raise PlanError(f"line {lineno}: malformed phase heading {line!r}; expected '## Phase N: Name (role)'")
        d = DIRECTIVE.match(line)
        if d and d.group(1) in KNOWN:
            key, val = d.group(1), d.group(2)
            _apply(cur, key, val, lineno)
            continue
        brief_lines.append(line)
    flush()

    plan = Plan(path=path, preamble="\n".join(preamble).strip(), phases=phases, title=title)
    _resolve(plan)
    return plan


def _apply(ph: Phase, key: str, val: str, lineno: int) -> None:
    if key == "EXIT":
        if not val:
            raise PlanError(f"line {lineno}: empty EXIT predicate")
        ph.exits.append(val)
    elif key == "AFTER":
        nums = [x.strip() for x in re.split(r"[,\s]+", val) if x.strip()]
        try:
            ph.after = [int(x) for x in nums]
        except ValueError:
            raise PlanError(f"line {lineno}: AFTER wants phase numbers, got {val!r}")
        ph.after_explicit = True
    elif key == "TIMEOUT":
        ph.timeout = _int(val, lineno, "TIMEOUT", minimum=1)
    elif key == "LANES":
        if not val:
            raise PlanError(f"line {lineno}: LANES wants an items file")
        ph.lanes = val
    elif key == "CEILING":
        ph.ceiling = _int(val, lineno, "CEILING", minimum=1)
    elif key == "REVIEW":
        if val != "cross":
            raise PlanError(f"line {lineno}: REVIEW supports only 'cross', got {val!r}")
        ph.review = val
    elif key == "SURFACE":
        parts = val.split()
        if len(parts) != 2:
            raise PlanError(f"line {lineno}: SURFACE wants '<glob> <surface>'")
        ph.surfaces.append((parts[0], parts[1]))
    elif key == "GATE":
        ph.gate = val
    elif key == "ATTEMPTS":
        ph.attempts = _int(val, lineno, "ATTEMPTS", minimum=1)
    elif key == "MODEL":
        if not val:
            raise PlanError(f"line {lineno}: MODEL wants provider/model")
        ph.model = val
    elif key == "EFFORT":
        if val not in ("low", "medium", "high"):
            raise PlanError(f"line {lineno}: EFFORT must be low|medium|high, got {val!r}")
        ph.effort = val


def _int(val, lineno, key, minimum):
    try:
        n = int(val)
    except ValueError:
        raise PlanError(f"line {lineno}: {key} wants an integer, got {val!r}")
    if n < minimum:
        raise PlanError(f"line {lineno}: {key} must be >= {minimum}")
    return n


def _resolve(plan: Plan) -> None:
    """Default deps are strict sequence (each phase after the previous one in file order).
    Validate deps exist, no self/forward cycles, gate phases have no worker directives."""
    prev = None
    numbers = {p.number for p in plan.phases}
    for p in plan.phases:
        if not p.after_explicit:
            p.after = [prev] if prev is not None else []
        for a in p.after:
            if a not in numbers:
                raise PlanError(f"phase {p.number}: AFTER references unknown phase {a}")
            if a == p.number:
                raise PlanError(f"phase {p.number}: AFTER references itself")
        if p.is_gate:
            if p.exits or p.lanes or p.review or p.surfaces:
                raise PlanError(f"phase {p.number}: gate phases take no EXIT/LANES/REVIEW/SURFACE")
            if not p.gate:
                p.gate = str(p.default_gate_sentinel(plan.dir))
        else:
            if p.gate:
                raise PlanError(f"phase {p.number}: GATE only applies to (gate) phases")
        prev = p.number
    # cycle check
    order = topo_order(plan)
    if len(order) != len(plan.phases):
        raise PlanError("plan has a dependency cycle")


def topo_order(plan: Plan) -> list:
    done, order = set(), []
    remaining = {p.number: set(p.after) for p in plan.phases}
    while remaining:
        ready = sorted(n for n, deps in remaining.items() if deps <= done)
        if not ready:
            break
        for n in ready:
            order.append(n)
            done.add(n)
            del remaining[n]
    return order


def parse_file(path) -> Plan:
    path = Path(path).resolve()
    if not path.exists():
        raise PlanError(f"plan not found: {path}")
    plan = parse_text(path.read_text(), path)
    if not plan.phases:
        raise PlanError(f"{path}: no phases found")
    return plan


def validate_roles(plan: Plan, known_roles: set) -> None:
    for p in plan.phases:
        if p.is_gate:
            continue
        if p.role not in known_roles:
            raise PlanError(f"phase {p.number}: unknown role {p.role!r}; known: {sorted(known_roles)}")
