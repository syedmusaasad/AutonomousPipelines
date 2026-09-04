"""Role registry: role -> model seat, effort, tool grants, prompt. Renders devpass-code
agent files (generated; hand-edits fail the drift guard)."""

import json
import os
import re
from pathlib import Path

from . import paths

REGISTRY = paths.roles_dir() / "registry.json"
REVIEWERS = ("reviewer-a", "reviewer-b", "reviewer-c")
DISPATCHABLE = ("implementer", "fast-worker", "lane-worker", "researcher", "document-writer", "frontend-worker") + REVIEWERS
AGENT_PREFIX = "pl-"

EXTERNAL_CHANNEL_PATTERNS = (
    # No role can post to external channels. Denied at the bash-permission level and
    # restated in every role prompt.
    "gh pr comment*", "gh issue comment*", "gh pr create*", "gh issue create*", "gh release*",
    "curl -X POST*", "curl --data*", "curl -d*", "curl -F*", "wget --post*",
    "slack*", "tweet*", "mail *", "sendmail*", "twilio*",
)


class RegistryError(ValueError):
    pass


def load(path: Path = None) -> dict:
    path = path or REGISTRY
    reg = json.loads(Path(path).read_text())
    validate(reg)
    return reg


def family_of(model: str, reg: dict) -> str:
    bare = model.split("/", 1)[-1]
    for fam, prefixes in reg["families"].items():
        if any(bare.startswith(p) for p in prefixes):
            return fam
    return "unknown:" + bare


def is_premium(model: str, reg: dict) -> bool:
    """True if the registry's models table tags `model` as premium-tier. Unknown or
    untagged models are treated as not-premium-confirmed (i.e. False), matching the
    conservative default: only a confirmed premium tag blocks a quota_fallback."""
    bare = model.split("/", 1)[-1]
    entry = reg.get("models", {}).get(bare)
    return bool(entry) and entry.get("premium") is True


def validate(reg: dict) -> None:
    roles = reg.get("roles", {})
    required = {"interactive", "implementer", "fast-worker", "lane-worker", "researcher", "document-writer", "frontend-worker", *REVIEWERS}
    missing = required - set(roles)
    if missing:
        raise RegistryError(f"registry missing roles: {sorted(missing)}")
    for name, r in roles.items():
        for k in ("model", "fallback", "effort", "tools", "quota_fallback"):
            if k not in r:
                raise RegistryError(f"role {name}: missing {k}")
        if family_of(r["model"], reg) == family_of(r["fallback"], reg):
            raise RegistryError(f"role {name}: fallback {r['fallback']} shares family with {r['model']}")
        if r.get("external_post", False):
            raise RegistryError(f"role {name}: external_post must be false")
        if name in REVIEWERS:
            if not r.get("sealed"):
                raise RegistryError(f"role {name}: reviewers must be sealed")
            if r["tools"].get("edit"):
                raise RegistryError(f"role {name}: sealed reviewers get no edit grant")
        qf_model, _ = _split_arm(r["quota_fallback"])
        if is_premium(qf_model, reg):
            raise RegistryError(f"role {name}: quota_fallback {r['quota_fallback']} is premium-tier")
    fams = {family_of(roles[x]["model"], reg) for x in REVIEWERS}
    if len(fams) < 2:
        raise RegistryError("reviewers must span at least two model families")


def _split_arm(s: str) -> tuple:
    """'model@effort' -> (model, effort); bare 'model' -> (model, None)."""
    if "@" in s:
        model, effort = s.rsplit("@", 1)
        return model, effort
    return s, None


def qualified(model: str, reg: dict) -> str:
    return model if "/" in model else f"{reg['provider']}/{model}"


def seat(role: str, reg: dict = None) -> dict:
    reg = reg or load()
    if role not in reg["roles"]:
        raise RegistryError(f"unknown role {role!r}")
    r = dict(reg["roles"][role])
    r["name"] = role
    r["model_q"] = qualified(r["model"], reg)
    r["fallback_q"] = qualified(r["fallback"], reg)
    r["family"] = family_of(r["model"], reg)
    r["fallback_family"] = family_of(r["fallback"], reg)
    r["agent"] = AGENT_PREFIX + role
    if r.get("quota_fallback"):
        qf_model, qf_effort = _split_arm(r["quota_fallback"])
        r["quota_fallback_model"] = qf_model
        r["quota_fallback_model_q"] = qualified(qf_model, reg)
        r["quota_fallback_effort"] = qf_effort if qf_effort is not None else r["effort"]
    return r


def cross_review_pair(reg: dict = None, exclude: set = ()) -> tuple:
    """Two reviewer roles on different families."""
    reg = reg or load()
    cands = [r for r in REVIEWERS if r not in exclude]
    for i, a in enumerate(cands):
        for b in cands[i + 1:]:
            if family_of(reg["roles"][a]["model"], reg) != family_of(reg["roles"][b]["model"], reg):
                return a, b
    raise RegistryError("no cross-family reviewer pair available")


def prompt_path(role: str) -> Path:
    return paths.roles_dir() / f"{role}.md"


def role_prompt(role: str) -> str:
    p = prompt_path(role)
    if not p.exists():
        raise RegistryError(f"no prompt for role {role}: {p}")
    return p.read_text()


def worker_contract() -> str:
    return (paths.roles_dir() / "CONTRACT.md").read_text()


# ---- agent file rendering (generated config; drift-guarded) ------------------

def render_agent(role: str, reg: dict) -> str:
    s = seat(role, reg)
    tools = s["tools"]
    perm = {}
    for tool in ("read", "glob", "grep", "list", "edit", "webfetch", "task"):
        perm[tool] = "allow" if tools.get(tool) else "deny"
    # bash: allow everything except external-channel posting. Reviewers get read-only-ish bash.
    bash_rules = {"*": "allow"}
    if s.get("sealed"):
        bash_rules = {"*": "allow", "git commit*": "deny", "git push*": "deny", "rm *": "deny", "mv *": "deny"}
    for pat in EXTERNAL_CHANNEL_PATTERNS:
        bash_rules[pat] = "deny"
    perm["bash"] = bash_rules if tools.get("bash") else "deny"
    perm["question"] = "deny"  # headless: never ask
    perm["todowrite"] = "allow"
    perm["doom_loop"] = "allow"
    perm["external_directory"] = "allow"
    body = role_prompt(role)
    if role != "interactive":
        body = worker_contract().rstrip() + "\n\n" + body
    front = {
        "description": f"GENERATED by `pipeline render-agents` from roles/registry.json; do not hand-edit. {s.get('purpose','')}",
        "mode": "primary",
        "model": s["model_q"],
        "variant": s["effort"],
        "permission": perm,
    }
    return "---\n" + _yaml(front) + "---\n" + body.rstrip() + "\n"


def _yaml(obj, indent=0) -> str:
    out = []
    pad = "  " * indent
    for k, v in obj.items():
        if isinstance(v, dict):
            out.append(f"{pad}{_ykey(k)}:")
            out.append(_yaml(v, indent + 1).rstrip("\n"))
        else:
            out.append(f"{pad}{_ykey(k)}: {json.dumps(v)}")
    return "\n".join(out) + "\n"


def _ykey(k: str) -> str:
    return json.dumps(k) if re.search(r"[^A-Za-z0-9_-]", k) else k


def rendered_agents(reg: dict = None) -> dict:
    reg = reg or load()
    return {f"{AGENT_PREFIX}{role}.md": render_agent(role, reg) for role in reg["roles"]}


def agents_dir() -> Path:
    override = os.environ.get("PIPELINE_AGENTS_DIR")
    return Path(override) if override else Path.home() / ".config" / "devpass-code" / "agent"


def write_agents(dest: Path = None, reg: dict = None) -> list:
    dest = dest or agents_dir()
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in rendered_agents(reg).items():
        (dest / name).write_text(text)
        written.append(dest / name)
    return written


def drift(dest: Path = None, reg: dict = None) -> list:
    """Files whose on-disk content differs from the regenerated content (or are missing)."""
    dest = dest or agents_dir()
    bad = []
    for name, text in rendered_agents(reg).items():
        p = dest / name
        if not p.exists() or p.read_text() != text:
            bad.append(str(p))
    return bad
