# Plan 000: the pipeline lands itself
WORKDIR: /root/pipeline

DECISION dogfood-first-commit: the first commit of this repository is landed by the
pipeline (document-writer phase scored against operator-doc, then a finisher phase),
not by hand, so the journal records the system's own birth.

## Phase 1: README (document-writer)
TIMEOUT: 900
SURFACE: README.md operator-doc
EXIT: test -s README.md
EXIT: grep -q 'quick -r' README.md && grep -q 'run <dir>/plan.md' README.md
EXIT: grep -q '## Phase N: Name (role)' README.md
EXIT: grep -q 'EXIT:' README.md && grep -q 'AFTER:' README.md && grep -q 'LANES:' README.md && grep -q 'REVIEW: cross' README.md && grep -q 'SURFACE:' README.md && grep -q '(gate)' README.md
EXIT: grep -q 'pipeline bench' README.md && grep -q 'pipeline suite' README.md && grep -q 'bootstrap.sh' README.md
EXIT: ! grep -qi 'seamless\|cutting-edge\|leverage' README.md

Write /root/pipeline/README.md for the operator of this system. Sources of truth to read
first: pipeline/plan.py (module docstring = the plan grammar), roles/interactive.md,
roles/CONTRACT.md, pipeline/cli.py (module docstring = the commands), bootstrap.sh,
pipeline/sentry.py (module docstring), registers/operator-doc.json (the register your
prose is scored against; read its rules).

Sections, in this order, with these exact headings:
1. `# pipeline` then one paragraph: what it is (plans -> verified, journaled work via
   headless devpass-code workers; the operator talks to one interactive agent).
2. `## Install`: `./bootstrap.sh`, what it does (sticky estate in ~/.system/estate,
   launchers in ~/.system/bin, agent files rendered, sentry as systemd --user unit),
   and the PATH line.
3. `## Two shapes`: `echo "task" | quick -r <role>` and `run <dir>/plan.md`. State
   that the interactive agent never does the work itself.
4. `## Plan grammar`: a fenced example plan, then one line per directive:
   `## Phase N: Name (role)`, `EXIT:`, `AFTER:`, `TIMEOUT:`, `LANES:` + `CEILING:`,
   `REVIEW: cross`, `SURFACE:`, `(gate)` + `GATE:`, `ATTEMPTS:`, `WORKDIR:`, `DECISION`.
   State that a capability not written into a plan does not run, and that plans are
   append-aware.
5. `## Roles`: the role names from roles/registry.json, one line each, and that seats
   change only via `pipeline trial`.
6. `## Status and the journal`: `status`, `status --mine`, `pipeline conv`; journal is
   the source of truth; liveness = process + transcript mtime.
7. `## Resilience`: detached launch, storm armor, the sentry (relight max 3, deliberate
   stops never auto-restarted), finisher (`finish --subject ... --paths ...`),
   `pipeline resume <run>`.
8. `## Quality`: gates only ratchet (tests/ratchet.json), drift guard
   (`pipeline check-agents`), `pipeline suite`, `pipeline bench > BENCHMARKS.md`.
9. `## Layout`: a short tree of the repo directories.

Register: operator-doc. First sentence states what the tool does. No exclamation
marks. No marketing adjectives. Sentences under 25 words on average. Do not write any
other file. Do not commit.

## Phase 2: first commit (implementer)
TIMEOUT: 600
EXIT: git log -1 --format=%s | grep -qxF 'pipeline: engine, quick, sentry, registry, suite, bootstrap'
EXIT: git diff --quiet HEAD -- pipeline bin roles registers tests systemd plugin plans bootstrap.sh README.md .gitignore
EXIT: test -z "$(git status --porcelain -- pipeline bin roles registers tests systemd plugin plans bootstrap.sh README.md .gitignore)"

Land the first commit of this repository. The work is already in the tree.
1. `git status --porcelain` to see the tree.
2. Stage exactly these paths: `git add -- .gitignore bootstrap.sh bin pipeline plugin registers roles systemd tests plans README.md`
3. Do NOT stage anything else (no docs/, no BENCHMARKS.md, no __pycache__).
4. Commit with exactly this subject and no body: `git commit -m 'pipeline: engine, quick, sentry, registry, suite, bootstrap'`
5. Final lines: output of `git log -1 --format='%h %s'` and `git show --stat HEAD | tail -3`.
No push (there is no remote).
