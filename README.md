# pipeline

A plan-driven engine that dispatches headless coding workers. The operator talks to one
interactive agent; everything else is machinery. Work outside the journal did not happen.

Workers run on two CLIs: Claude Code (`claude -p`, a Claude subscription) for every
Anthropic seat, and Codex (`codex exec`, a ChatGPT subscription) for the reviewer.

## Install

```sh
git clone <this repo> && cd pipeline
./bootstrap.sh
export PATH="$HOME/.system/bin:$PATH"
claude          # sign in once (Claude subscription)
codex login     # sign in once (ChatGPT subscription; the reviewer seat)
pl              # the interactive seat
```

Bootstrap records the estate (state root) on the most reliable local disk, installs
launchers, renders Claude Code agent files into `~/.claude/agents`, adds a SessionStart
hook that keys runs to the conversation, and starts the sentry as a systemd user unit.

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
| `REVIEW: on` | one sealed Codex reviewer after EXIT passes (`cross` is the legacy spelling) |
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

| Role | CLI | Model | Effort |
|---|---|---|---|
| interactive | claude | claude-opus-5-5 | high |
| implementer | claude | claude-sonnet-5-5 | high |
| fast-worker | claude | claude-haiku-5-5 | low |
| lane-worker | claude | claude-haiku-5-5 | low |
| researcher | claude | claude-opus-5-5 | high |
| document-writer | claude | claude-sonnet-5-5 | high |
| frontend-worker | claude | claude-sonnet-5-5 | high |
| reviewer | codex | gpt-6.1-sol | high |

A seat's model family picks its CLI (`clis` in roles/registry.json). There are no
fallback models: a failed dispatch is retried on the same seat as a phase attempt, and a
subscription usage-limit refusal stops the run (`usage_limit`, no attempt charged) until
the operator resumes it after the reset. Seats change only via `pipeline trial`. The
interactive seat is read-only: `pl` starts it with settings that refuse writes (dontAsk)
instead of prompting, and the role prompt names the dispatch it should have used.

The reviewer is sealed: Codex runs it in a read-only sandbox, and the engine saves its
final message as `review.md`. BLOCKING gets one revision dispatch and one re-review;
BLOCKING again is a deliberate stop.

Headless Claude Code is tuned for start-up speed (see docs/WORKER-COMPATIBILITY.md):
non-essential traffic, claude.ai connectors and stray MCP servers are off, there is no
session persistence (the transcript is the record), and per-machine prompt sections
are moved out of the system prompt so workers share one prompt-cache prefix.

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
plugin/     Claude Code SessionStart hook (conversation id)
registers/  writing-register standards
tests/      suite, ratchet ledger, fake worker, probe, hygiene gates
plans/      executed plan records
```
