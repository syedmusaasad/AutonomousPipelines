# Writing plans

A plan is judged by what it leaves out. Write the fewest phases that reach the outcome, then stop.

## Before you write

Decide the shape. One small, well-specified piece of work is a `quick`, not a plan. A plan earns its file when work has stages, when a stage needs verification the worker cannot fake, or when something irreversible needs a gate.

## Rules

1. One phase, one role, one outcome. If a phase description needs the word "and" between two deliverables, split it or cut one.
2. Every phase carries at least one `EXIT:`. The engine runs it. A phase without an EXIT lands on the worker's word alone.
3. Default is sequence. Add `AFTER:` only when phases are independent in fact, not in hope. Two phases that touch the same file are not independent.
4. `LANES:` is for items that share a recipe. Ten different tasks are ten phases or ten quicks, not ten lanes.
5. `REVIEW: cross` costs two dispatches. Spend it on code that lands, not on notes.
6. `SURFACE:` scores prose. Name the register (`operator-doc`, `research-note`, `commit-message`) and the glob.
7. Everything irreversible sits behind a `(gate)`: pushes to shared branches, deletions, deploys, external sends. The sentinel path is named in the plan.
8. Briefs carry task facts: exact paths, exact commit subjects, scope fences ("git add X only"), the suite entrypoint, the verification grep. Do not restate the worker contract; the role prompt already carries it.
9. Ceremony is where workers die. Name it exactly and give the finisher something to check: `EXIT: git log -1 --format=%s | grep -qxF '<subject>'`.
10. When a judgment call changes the plan, ask once and record the answer: `DECISION <name>: <choice and why>` in the preamble.

## Anti-patterns

- A "setup" phase that does nothing verifiable.
- A "polish" or "cleanup" phase. Cut it.
- `TIMEOUT:` raised to make a stalled worker pass. Split the work instead.
- EXIT predicates weakened after a failure. Gates only ratchet; fix the work.
- A brief that says "ensure quality" or "be careful". Say what to run and what must be true.

## Example: the smallest useful plan

```markdown
# Fix the flaky retry test
WORKDIR: /srv/app

## Phase 1: fix (implementer)
EXIT: python3 -m pytest -q tests/test_retry.py -p no:randomly --count 5
EXIT: git log -1 --format=%s | grep -qxF 'fix: stabilize retry backoff test'

tests/test_retry.py::test_backoff_jitter fails one run in ten with "expected 0.5 <= 0.49".
The jitter bound in app/retry.py:41 is exclusive; the test asserts inclusive. Fix the
bound, not the test. `git add app/retry.py only`. Commit subject exactly:
`fix: stabilize retry backoff test`.
```
