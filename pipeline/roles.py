"""Role registry: role -> model seat, effort, tool grants, prompt. Each model family maps
to the CLI that runs it (registry `clis`): anthropic seats run on Claude Code, openai
seats on Codex. Renders Claude Code agent files (generated; hand-edits fail the drift
guard). There are no fallback models."""

import json
import os
import re
from pathlib import Path

from . import paths

REGISTRY = paths.roles_dir() / "registry.json"
REVIEWER = "reviewer"
REVIEWERS = (REVIEWER,)
DISPATCHABLE = ("implementer", "fast-worker", "lane-worker", "researcher", "document-writer", "frontend-worker") + REVIEWERS
REQUIRED = ("interactive",) + DISPATCHABLE
AGENT_PREFIX = "pl-"
CLIS = ("claude", "codex")
EFFORTS = {"claude": ("low", "medium", "high", "xhigh", "max"), "codex": ("minimal", "low", "medium", "high", "xhigh")}
RETIRED_FIELDS = ("fallback", "quota_fallback")

INTERACTIVE_ALLOWED_BASH = (
    "run *", "run", "quick *", "quick", "status *", "status", "pipeline defects *", "pipeline defects",
    "pipeline status *", "pipeline status", "pipeline conv *", "pipeline conv",
    "ls *", "ls", "cat *", "cat", "grep *", "grep", "head *", "head", "tail *", "tail",
    "git status*", "git log*", "git diff*", "git show*", "wc *", "wc", "find *", "find", "echo *", "echo",
    "ps *", "ps", "which *", "which", "df *", "df", "du *", "du",
    "readlink *", "readlink", "type *", "type", "pipeline check-agents", "pipeline check-agents *",
    "codex login status",
)

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


def cli_of(model: str, reg: dict) -> str:
    """The CLI that runs `model`: the registry maps each family to one CLI."""
    fam = family_of(model, reg)
    cli = reg.get("clis", {}).get(fam)
    if cli not in CLIS:
        raise RegistryError(f"model {model} (family {fam}) has no CLI in registry clis")
    return cli


def is_premium(model: str, reg: dict) -> bool:
    """True if an optional registry `models` table tags `model` premium. Kept for trial
    reports over older registries; the current registry has no tiers."""
    bare = model.split("/", 1)[-1]
    entry = reg.get("models", {}).get(bare)
    return bool(entry) and entry.get("premium") is True


def validate(reg: dict) -> None:
    roles = reg.get("roles", {})
    missing = set(REQUIRED) - set(roles)
    if missing:
        raise RegistryError(f"registry missing roles: {sorted(missing)}")
    for name, r in roles.items():
        for k in ("model", "effort", "tools"):
            if k not in r:
                raise RegistryError(f"role {name}: missing {k}")
        for k in RETIRED_FIELDS:
            if k in r:
                raise RegistryError(f"role {name}: {k} is retired; seats have no fallback models")
        cli = cli_of(r["model"], reg)
        if r["effort"] not in EFFORTS[cli]:
            raise RegistryError(f"role {name}: effort {r['effort']!r} is not a {cli} effort {EFFORTS[cli]}")
        if r.get("external_post", False):
            raise RegistryError(f"role {name}: external_post must be false")
        if name in REVIEWERS:
            if not r.get("sealed"):
                raise RegistryError(f"role {name}: reviewers must be sealed")
            if r["tools"].get("edit"):
                raise RegistryError(f"role {name}: sealed reviewers get no edit grant")
    if cli_of(roles["interactive"]["model"], reg) != "claude":
        raise RegistryError("interactive seat must run on Claude Code (agent file + settings)")


def _split_arm(s: str) -> tuple:
    """'model@effort' -> (model, effort); bare 'model' -> (model, None)."""
    if "@" in s:
        model, effort = s.rsplit("@", 1)
        return model, effort
    return s, None


def qualified(model: str, reg: dict = None) -> str:
    """Model ids are passed to the CLI as-is; a legacy 'provider/model' keeps only model."""
    return model.split("/", 1)[-1]


def seat(role: str, reg: dict = None) -> dict:
    reg = reg or load()
    if role not in reg["roles"]:
        raise RegistryError(f"unknown role {role!r}")
    r = dict(reg["roles"][role])
    r["name"] = role
    r["model_q"] = qualified(r["model"], reg)
    r["family"] = family_of(r["model"], reg)
    r["cli"] = cli_of(r["model"], reg)
    r["agent"] = AGENT_PREFIX + role
    return r


def reviewer_role(reg: dict = None) -> str:
    """The one sealed reviewer seat."""
    reg = reg or load()
    if REVIEWER not in reg["roles"]:
        raise RegistryError("no reviewer seat")
    return REVIEWER


def prompt_path(role: str) -> Path:
    return paths.roles_dir() / f"{role}.md"


def role_prompt(role: str) -> str:
    p = prompt_path(role)
    if not p.exists():
        raise RegistryError(f"no prompt for role {role}: {p}")
    return p.read_text()


def worker_contract() -> str:
    return (paths.roles_dir() / "CONTRACT.md").read_text()


# ---- Claude Code agent files + permissions (generated; drift-guarded) --------

# registry tool grant -> Claude Code tool names
CLAUDE_TOOLS = {
    "read": ("Read",), "glob": ("Glob",), "grep": ("Grep",), "list": (),
    "bash": ("Bash",), "edit": ("Edit", "Write", "NotebookEdit"),
    "webfetch": ("WebFetch", "WebSearch"), "task": ("Agent",),
}
SEALED_BASH_DENY = ("git commit *", "git push *", "rm *", "mv *")


def claude_tools(s: dict) -> list:
    out = []
    for grant, names in CLAUDE_TOOLS.items():
        if s["tools"].get(grant):
            out.extend(names)
    out.append("TodoWrite")
    return out


def claude_permissions(role: str, reg: dict) -> dict:
    """allow/deny rule lists for a Claude seat. Used both as headless CLI flags
    (--allowed-tools/--disallowed-tools under --permission-mode dontAsk: anything not
    allowed is refused, never asked) and as the interactive seat's settings file."""
    s = seat(role, reg)
    tools = claude_tools(s)
    deny = [f"Bash({p})" for p in EXTERNAL_CHANNEL_PATTERNS]
    if role == "interactive":
        allow = [t for t in tools if t != "Bash"] + [f"Bash({v})" for v in INTERACTIVE_ALLOWED_BASH]
        deny = ["Edit", "Write", "NotebookEdit"] + [f"Bash({p})" for p in SEALED_BASH_DENY] + deny
    else:
        allow = tools
        if s.get("sealed"):
            deny = ["Edit", "Write", "NotebookEdit"] + [f"Bash({p})" for p in SEALED_BASH_DENY] + deny
    return {"allow": allow, "deny": deny}


def interactive_settings(reg: dict = None) -> dict:
    """Claude Code settings for the interactive seat: dontAsk, so anything outside the
    read-only allowlist is refused (the seat guard) instead of prompting."""
    reg = reg or load()
    perms = claude_permissions("interactive", reg)
    return {"permissions": {"defaultMode": "dontAsk", "allow": perms["allow"], "deny": perms["deny"]}}


def render_agent(role: str, reg: dict) -> str:
    s = seat(role, reg)
    body = role_prompt(role)
    if role != "interactive":
        body = worker_contract().rstrip() + "\n\n" + body
    front = {
        "name": s["agent"],
        "description": f"GENERATED by `pipeline render-agents` from roles/registry.json; do not hand-edit. {s.get('purpose','')}",
        "model": s["model_q"],
        "effort": s["effort"],
        "tools": ", ".join(claude_tools(s)),
    }
    return "---\n" + "".join(f"{k}: {json.dumps(v)}\n" for k, v in front.items()) + "---\n" + body.rstrip() + "\n"


def codex_prompt(role: str) -> str:
    """Codex has no agent files: the contract and role prompt lead every brief."""
    return worker_contract().rstrip() + "\n\n" + role_prompt(role).rstrip() + "\n"


def rendered_agents(reg: dict = None) -> dict:
    reg = reg or load()
    out = {f"{AGENT_PREFIX}{role}.md": render_agent(role, reg)
           for role in reg["roles"] if cli_of(reg["roles"][role]["model"], reg) == "claude"}
    out[INTERACTIVE_SETTINGS] = json.dumps(interactive_settings(reg), indent=2) + "\n"
    return out


INTERACTIVE_SETTINGS = "pl-interactive.settings.json"


def agents_dir() -> Path:
    override = os.environ.get("PIPELINE_AGENTS_DIR")
    return Path(override) if override else Path.home() / ".claude" / "agents"


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
