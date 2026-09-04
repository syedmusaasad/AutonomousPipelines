# Plan 003: model selection, staged (probe -> nominate -> approve -> trials)
WORKDIR: /root/pipeline

DECISION roster-width: probe every text-capable model on the gateway (42 in candidates.txt,
about $1 total) rather than a hand-picked shortlist, so nominations rest on measured
reachability, tool use, latency and cost, not on names.
DECISION task-source: the journal has too few real dispatches to sample from, so a researcher
writes 5 tasks per role modelled on plans 000-002 (real repo, verifiable EXITs). The operator
approves the task sets at the gate before any trial spends tokens.
DECISION trial-scope: first round trials only the high-volume seats (implementer, fast-worker,
reviewer-a/b/c), at most 2 candidates per seat; budget cap $25 for the round. Researcher,
document-writer, lane-worker and interactive seats wait for round two.
DECISION blind-scoring: the trial scorer must not see which arm is incumbent; phase 1 fixes
this before any trial runs.

## Phase 1: blind the trial scorer (implementer)
TIMEOUT: 1200
EXIT: python3 tests/run.py trial_ 2>&1 | grep -q ' 0 failed'
EXIT: grep -q 'arm-A\|arm_a\|"A"' pipeline/trial.py
EXIT: python3 -c "import sys;sys.path.insert(0,'.');from pipeline import trial,roles;reg=roles.load();t=trial.trial_plan('implementer','gpt-5.4',[('t1','do x')],'rubric',__import__('pathlib').Path('/w'),reg);assert 'incumbent' not in t.split('## Phase 3')[1] and 'candidate' not in t.split('## Phase 3')[1] and 'gpt-5.4' not in t.split('## Phase 3')[1], t.split('## Phase 3')[1]"
EXIT: git log -1 --format=%s | grep -qxF 'trial: blind the scorer to arm identity'

In pipeline/trial.py the scorer phase currently names arms `incumbent`/`candidate` and the
task phases write to `trial/task<i>/<arm>/`. Change it so each trial run picks a random
mapping {A,B} -> {incumbent,candidate}, saved to `trial/mapping.json` by the plan preamble
(a `DECISION` line is fine) but NOT mentioned in the scorer phase brief; task phases write
to `trial/task<i>/A/` and `trial/task<i>/B/`; the scorer writes scores keyed by `A`/`B`;
`decide()` reads mapping.json to translate back. Keep the model pinned per arm via MODEL:.
The scorer brief must contain no model names and no words incumbent/candidate. Update the
test `trial_is_a_plan_with_pinned_models_third_family_scorer_and_three_axis_decision` in
tests/test_suite.py to assert the blinding (it may currently assert the old layout).
Run: `python3 tests/run.py` (all must pass; the pre-commit hook runs it again).
Stage only: `git add -- pipeline/trial.py pipeline/cli.py tests/test_suite.py`
Commit with exactly: `git commit -m 'trial: blind the scorer to arm identity'`
Do not push.

## Phase 2: probe every candidate (lane-worker)
AFTER: 1
LANES: candidates.txt
CEILING: 4
TIMEOUT: 900
ATTEMPTS: 1
EXIT: test -s "$LANE_OUT/$ITEM.json" && python3 -c "import json,sys;d=json.load(open('$LANE_OUT/$ITEM.json'));assert d['model']=='$ITEM' and set(d)>={'reachable','tool_ok','wall_ms','tokens','cost'},d"

Run exactly: `bash /root/pipeline/plans/003-model-selection/probe.sh "$ITEM" "$LANE_OUT"`
It writes `$LANE_OUT/$ITEM.json`. Do not edit probe.sh. Do not interpret the result; the
next phase does. If the script itself errors (not the model), write the error to
`$LANE_OUT/error.txt`. Final line: the path of the json written.

## Phase 3: nominate (researcher)
AFTER: 2
TIMEOUT: 1800
SURFACE: plans/003-model-selection/NOMINATIONS.md research-note
EXIT: test -s plans/003-model-selection/probe-summary.md
EXIT: test -s plans/003-model-selection/NOMINATIONS.md
EXIT: grep -c '^| ' plans/003-model-selection/probe-summary.md | awk '{exit !($1>=40)}'
EXIT: for r in implementer fast-worker reviewer-a reviewer-b reviewer-c; do grep -q "^## $r" plans/003-model-selection/NOMINATIONS.md || exit 1; done
EXIT: test -s plans/003-model-selection/tasks/implementer.md && test -s plans/003-model-selection/tasks/fast-worker.md && test -s plans/003-model-selection/tasks/reviewer.md
EXIT: test -s plans/003-model-selection/rubrics/implementer.md && test -s plans/003-model-selection/rubrics/fast-worker.md && test -s plans/003-model-selection/rubrics/reviewer.md
EXIT: for f in plans/003-model-selection/tasks/*.md; do test "$(grep -c '^## ' $f)" -ge 5 || exit 1; done

Inputs: the probe results in the most recent run's lanes. Find them with
`ls -d /root/.system/runs/*/phase-2/lanes/lane-*/` for the run whose journal names this plan
(`grep -l 003-model-selection /root/.system/runs/*/journal.jsonl`). Each lane dir has one
`<model>.json`. Also read roles/registry.json (current seats, families) and pipeline/trial.py
(how a trial runs).

Write four things:
1. `plans/003-model-selection/probe-summary.md`: one markdown table, one row per probed
   model (all 42): model | family | reachable | tool_ok | wall_ms plain | wall_ms tool |
   tokens tool | cost tool | error (truncated). Sort by family then model.
2. `plans/003-model-selection/NOMINATIONS.md` (register: research-note). For each of the
   sections `## implementer`, `## fast-worker`, `## reviewer-a`, `## reviewer-b`,
   `## reviewer-c`: the incumbent, then at most 2 nominated candidates, each with: probe
   evidence (reachable, tool_ok, wall, cost from your table), and one line of external
   evidence with a URL (vendor model card or a public benchmark) marked as "nominates, does
   not decide". Only nominate models that are reachable and tool_ok. Reviewer nominations
   must keep the three reviewer seats on three different families; say which family each
   nominee is. Prefer cheaper-per-token candidates where probe quality is equal. End with
   `## Open questions`.
3. `plans/003-model-selection/tasks/{implementer,fast-worker,reviewer}.md`: 5 tasks each,
   one per `## <name>` heading, modelled on the work in plans/000-bootstrap, 001-ci-and-bench
   and 002-publish. Each task states the deliverable path under the arm's output dir and
   what a scorer can check. Implementer tasks must be doable inside this repo without
   committing (e.g. "write a patch file to <out>/fix.patch that makes X pass"). Reviewer
   tasks give a small artifact (inline in the task) with a planted defect to find.
4. `plans/003-model-selection/rubrics/{implementer,fast-worker,reviewer}.md`: 0-10 scoring
   rubric, 4-6 criteria with weights summing to 10, each criterion checkable from the
   deliverable alone.
Do not edit roles/registry.json. Do not commit.

## Phase 4: approve nominations and task sets (gate)
AFTER: 3
GATE: /root/pipeline/plans/003-model-selection/.approve-nominations

## Phase 5: land the selection materials (implementer)
AFTER: 4
TIMEOUT: 600
EXIT: git log -1 --format=%s | grep -qxF 'model-selection: probe results, nominations, trial tasks and rubrics'
EXIT: test -z "$(git status --porcelain -- plans/003-model-selection)"
EXIT: git fetch -q origin main && git diff --quiet HEAD origin/main

Stage only: `git add -- plans/003-model-selection` (the .gitignore already excludes
`.approve-*`). Commit with exactly:
`git commit -m 'model-selection: probe results, nominations, trial tasks and rubrics'`
Then `git push origin main`. If the hook or push fails, write NOTES.md and stop; no flags.
Final lines: `git log -1 --format='%h %s'`.
