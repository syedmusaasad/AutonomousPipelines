# pipeline

A plan-driven engine that dispatches headless coding workers. The operator talks to one
interactive agent; everything else is machinery. Work outside the journal did not happen.

## Install

```sh
git clone <this repo> && cd pipeline
./bootstrap.sh
export PATH="$HOME/.system/bin:$PATH"
```

Bootstrap records the estate (state root) on the most reliable local disk, installs
launchers, renders agent files, and starts the sentry as a systemd user unit.

## The two shapes

- quick: one detached dispatch for small work. `echo "task facts" | quick -r <role>`
- plan: a markdown file of phases for staged work. `run <dir>/plan.md`

The interactive agent routes every ask into one of these two shapes and never does the
work itself.

## Plan grammar

| Directive | Meaning |
|---|---|
| `## Phase N: Name (role)` | one phase, one role |
| `EXIT: <predicate>` | engine runs it after the worker; completion refused while it fails |
| `AFTER: 1, 2` | explicit dependencies; default is strict sequence |
| `TIMEOUT: <seconds>` | per attempt; bounds are 30 to 7200 |
| `ATTEMPTS: <n>` | worker attempts before the phase burns |
| `LANES: <file>` + `CEILING: <n>` | fan out one dispatch per item, n concurrent |
| `REVIEW: cross` | two reviewers on different families after EXIT passes |
| `SURFACE: <glob> <register>` | score prose artifacts against a register standard |
| `GATE: <path>` | operator sentinel; the engine waits |
| `ITERATE: on` + `CEILING: <n>` | fresh-session loop until EXIT passes |
| `PROGRESS: <predicate>` | custom progress signal for ITERATE |
| `MODEL:` / `EFFORT:` | pin model or effort for trials |
| `LANDING: on` | phase holds the landing lock |
| `WORKDIR:`, `DECISION` | worker cwd; recorded operator decisions |

`pipeline materialize <workflow> <out.md>` renders a named workflow (quick-fix,
feature, release) into a plan. `pipeline plan-check <plan.md>` validates grammar,
roles, header completeness, required EXITs, and TIMEOUT bounds; `run` refuses a
failing plan.

## Roles

| Role | Model | Effort | Fallback |
|---|---|---|---|
| interactive | kimi-k3.1-pro | high | gpt-6.1-sol |
| implementer | claude-sonnet-5-5 | high | deepseek-v4.1-flash |
| fast-worker | gpt-6-luna | low | deepseek-v4.1-flash@medium |
| lane-worker | deepseek-v4.1-flash | medium | gpt-6-luna@low |
| researcher | gemini-3.8-flash | medium | grok-4-7 |
| document-writer | gemini-3.8-flash | high | grok-4-7 |
| frontend-worker | claude-sonnet-5-5 | high | deepseek-v4.1-flash |
| reviewer-a | gpt-6.1-sol | high | deepseek-v4-pro |
| reviewer-b | grok-4-7 | high | gpt-6-sol |
| reviewer-c | claude-sonnet-5-5 | high | gemini-3.8-flash |

All seats are standard-tier per the DevPass $5/$15 rule; seats change only via
`pipeline trial`. The interactive seat is read-only: writes are refused with a receipt
naming the dispatch it should have used.

## Status

`status` reports from the journal: runs, phases, live workers with model, tokens, and
wall seconds per dispatch. `pipeline defects` prints the open defect count and entries.
`pipeline adopt <run>` transfers a run between sessions with a journal receipt.

## Resilience

Everything launches detached. Journal writes retry with backoff (storm armor). The
sentry relights dead engines (max 3 per window), defers a stalled worker one tick,
kills it on the second consecutive stalled tick, and never restarts deliberate stops.
`pipeline resume <run>` restores the recorded working directory. Landings serialize on
a flock; a lock whose pid is dead is reclaimed.

## Quality gates

`pipeline suite` runs the characterization suite plus hygiene gates: README claims map
to ratchet tests, generated role files match the registry rendering, role prompts stay
under word ceilings, and tracked files carry no operator paths or hostnames. The
ratchet only rises. An evidence gate refuses phases whose brief claims verification the
worker never ran.

## Defect log

Faults append to DEFECTS.md under flock (`pipeline defects` prints them); a fixing
session moves entries to # Fixed with the landing hash.

## Layout

```
pipeline/   engine, dispatch, journal, sentry, gc, cloudtier, tui, workflows, defects
roles/      registry.json, role prompts, CONTRACT.md
registers/  writing-register standards
tests/      suite, ratchet ledger, fake worker, probe, hygiene gates
plans/      executed plan records
```
