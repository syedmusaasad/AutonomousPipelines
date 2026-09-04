# pipeline

pipeline runs plans—markdown files of phases—as verified, journaled work dispatched to headless devpass-code workers. The operator talks to one interactive agent, which routes every ask into a quick dispatch or a plan; that agent never does the work itself. All work is recorded in a journal that is the source of truth.

## Install

Run `./bootstrap.sh` from the repo root.

The script picks the most reliable local block device, records it in `~/.system/estate`, and refuses to repoint on later runs unless you pass `--repoint`. It creates subdirectories under the estate (`runs`, `quick`, `state`, `logs`, `projects`), writes launcher symlinks to `~/.system/bin`, renders agent config files, installs the conversation plugin to `~/.config/devpass-code/plugin/`, and starts the sentry as a `systemd --user` unit (or a detached loop when systemd is unavailable).

Add the launchers to your shell:

```sh
export PATH="$HOME/.system/bin:$PATH"
```

## Two shapes

**Quick dispatch** — one detached worker for small work:

```sh
echo "task facts" | quick -r <role>
```

**Plan** — staged phases with verified exits:

```sh
run <dir>/plan.md
```

The interactive agent writes the plan file and calls `run`; it does not execute the work itself.

## Plan grammar

```markdown
WORKDIR: /path/to/project
DECISION my-choice: use approach A because it avoids the circular dep

## Phase 1: Fetch data (fast-worker)
TIMEOUT: 300
EXIT: test -f data/raw.json

## Phase 2: Approve schema (gate)
GATE: .gate-2

## Phase 3: Generate report (document-writer)
AFTER: 1, 2
LANES: items.txt
CEILING: 4
SURFACE: report.md operator-doc
REVIEW: cross
ATTEMPTS: 3
Write the report from data/raw.json.
```

One directive per line, in the phase block:

| Directive | Meaning |
|---|---|
| `## Phase N: Name (role)` | One phase, one role. Default deps are strict sequence. |
| `EXIT:` | Shell predicate the engine runs after the worker. Phase cannot complete while it fails. |
| `AFTER:` | Explicit deps by phase number. Phases whose deps are met run concurrently. |
| `TIMEOUT:` | Per-phase timeout in seconds (default 1800). |
| `LANES:` + `CEILING:` | Fan out one dispatch per non-empty line of the items file; `CEILING` caps concurrency (default 2). |
| `REVIEW: cross` | Two reviewers on different model families, run after EXIT passes. |
| `SURFACE:` | Score a prose artifact against a named register standard. |
| `(gate)` + `GATE:` | Pause the plan until the operator writes the sentinel file. Gate rejection is a deliberate stop. |
| `ATTEMPTS:` | Worker attempts before the phase burns (default 2). |
| `WORKDIR:` | Worker working directory; set in the plan preamble. Default is the plan's directory. |
| `DECISION` | Named operator decision recorded in the preamble (`DECISION name: what was chosen`). |

A capability not written into a plan does not run. Plans are append-aware: the engine resumes from the last completed phase, so adding phases to a finished plan extends it.

## Roles

Roles are defined in `roles/registry.json`. Each role has a model seat and a fallback on a different model family. Seats change only via `pipeline trial`.

| Role | Purpose |
|---|---|
| `interactive` | The one agent the operator talks to. Routes asks into quick or plan; never does the work itself. |
| `implementer` | Code changes with tests and exact ceremony (commit subject, verification grep). |
| `fast-worker` | Small, well-specified work: a fetch, a one-line fix, a file rename. |
| `lane-worker` | One item of a fan-out; writes only under `$LANE_OUT`. |
| `researcher` | Reads and fetches; writes findings to files with sources. Never edits code. |
| `document-writer` | Prose artifacts scored against a register standard; expects `SURFACE:`. |
| `reviewer-a` | Sealed reviewer, family A. Writes verdict to its own review directory only. |
| `reviewer-b` | Sealed reviewer, family B. Used for `REVIEW: cross` alongside reviewer-a. |
| `reviewer-c` | Sealed reviewer, family C. Substitutes when A or B is unavailable. |

## Status and the journal

```sh
status                  # all runs
status --mine           # runs this conversation launched
pipeline conv           # what this conversation set in motion
```

The journal is the source of truth for all run state. Liveness is determined by process presence and transcript mtime together; a journal row that says "running" can be a corpse.

## Resilience

**Detached launch** — `run` and `quick` detach immediately. The parent process exiting does not stop the engine.

**Storm armor** — transient API failures retry with backoff. The worker sees failure text on the next attempt.

**Sentry** — runs as a `systemd --user` unit (or a detached process). Each tick it relights dead engines up to 3 times per run per 6-hour window. Deliberate stops are never auto-restarted. Stalled worker processes (alive but transcript mtime older than 900 seconds) are killed once per phase so the engine retries.

**Finisher** — lands the exact named commit:

```sh
finish --subject "commit subject" --paths path/to/file
```

**Resume** — lift a deliberate stop after operator review:

```sh
pipeline resume <run>
```

## Quality

**Gates ratchet** — `tests/ratchet.json` records the minimum test count and required test names. The count never goes down.

**Drift guard** — generated agent files must match the registry rendering:

```sh
pipeline check-agents
```

**Characterization suite**:

```sh
pipeline suite
```

**Benchmarks** — collect timing from the journal and write `BENCHMARKS.md`:

```sh
pipeline bench > BENCHMARKS.md
```

## Layout

```
pipeline/
├── bin/            launchers (run, quick, status, finish, pipeline)
├── pipeline/       engine, plan parser, sentry, CLI, journal, bench, trial
├── plans/          plan files for this repo's own operations
├── registers/      register JSON files for prose surface scoring
├── roles/          registry.json, role prompt files, CONTRACT.md
├── tests/          characterization suite, ratchet ledger
├── plugin/         devpass-code conversation plugin
├── systemd/        pipeline-sentry.service unit file
├── docs/           additional documentation
└── bootstrap.sh    installer
```
