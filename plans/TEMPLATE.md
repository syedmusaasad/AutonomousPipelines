# <title>: one line, the outcome
WORKDIR: /abs/path/to/project

DECISION <name>: <what the operator chose and why>   (only if a judgment call changed the plan)

## Phase 1: <name> (implementer)
TIMEOUT: 1800
EXIT: <shell predicate the engine runs after the worker; e.g. python3 -m pytest -q tests/x.py>
EXIT: git log -1 --format=%s | grep -qxF '<exact commit subject>'

Task facts only. Exact paths. Exact commit subject. Scope fence ("git add X only").
Evidence requirement (the verification grep, the suite entrypoint).

## Phase 2: <independent work> (researcher)
AFTER: 1
EXIT: test -s FINDINGS.md

## Phase 3: <prose> (document-writer)
AFTER: 1
SURFACE: docs/*.md operator-doc
REVIEW: cross
EXIT: test -s docs/guide.md

## Phase 4: ship (gate)
AFTER: 2, 3
GATE: .approve-ship

## Phase 5: push (implementer)
EXIT: git fetch -q origin main && git diff --quiet HEAD origin/main

`git push origin main` only. Nothing else.
