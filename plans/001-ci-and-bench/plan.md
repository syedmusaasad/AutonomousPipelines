# Plan 001: CI hook, benchmarks, plan template land under cross review
WORKDIR: /root/pipeline

## Phase 1: cross-review the CI hook (implementer)
TIMEOUT: 600
REVIEW: cross
EXIT: test -x .githooks/pre-commit
EXIT: bash -n .githooks/pre-commit
EXIT: test -s plans/TEMPLATE.md && test -s BENCHMARKS.md
EXIT: git config core.hooksPath | grep -qx .githooks

Verify (do not rewrite) these already-present files: .githooks/pre-commit, plans/TEMPLATE.md,
BENCHMARKS.md, plans/000-bootstrap/plan.md, plans/001-ci-and-bench/plan.md.
Checks: the hook runs `bin/pipeline check-agents` and `python3 tests/run.py` with no bypass
flag; `bash -n .githooks/pre-commit` passes; TEMPLATE.md parses (`bin/pipeline validate
plans/TEMPLATE.md` may report unknown paths but must not report a grammar error).
Write your check results to docs/CHECKS-001.md (one line per check: PASS/FAIL + evidence).
Do not commit.

## Phase 2: land (implementer)
TIMEOUT: 600
EXIT: git log -1 --format=%s | grep -qxF 'ci: pre-commit hook, plan template, generated benchmarks'
EXIT: test -z "$(git status --porcelain -- .githooks plans BENCHMARKS.md docs/CHECKS-001.md)"

Stage exactly: `git add -- .githooks plans/TEMPLATE.md plans/000-bootstrap plans/001-ci-and-bench BENCHMARKS.md docs/CHECKS-001.md`
Commit with exactly: `git commit -m 'ci: pre-commit hook, plan template, generated benchmarks'`
The pre-commit hook runs the suite (about 30 s); wait for it. If the hook fails, do not
retry with any flag: write the failure to NOTES.md and stop.
Final lines: `git log -1 --format='%h %s'`.
