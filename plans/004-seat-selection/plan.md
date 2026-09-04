# Plan 004: seat selection for every role, decided by measured quality, wall and cost
WORKDIR: /root/pipeline

DECISION seat-selection: the operator wants cheaper and faster seats without losing a
significant amount of quality for the role's task; runtime is the priority axis. Rule, per
seat, over all arms including the incumbent: (1) keep arms whose mean rubric score is within
the role's quality band of the best arm; (2) rank the band by wall clock, then cost in
dollars. Bands: critical 0.5 (reviewers, researcher), standard 1.5 (implementer,
document-writer, frontend-worker, interactive), tolerant 2.5 (fast-worker, lane-worker).
Reviewers are chosen as a set of three on three different families by the same rule.
DECISION operator-nominees: the operator named a lineup (nominees.txt). Those models enter
the trials with equal standing to rule-generated candidates; nobody is seated on a name.
DECISION frontend-worker: a new role for browser-facing work (HTML/CSS/JS/TS components),
initial seat claude-sonnet-5 with a Google-family fallback, trialled like every other seat.
DECISION sample-size: arms under $0.01 per probe run each task twice (cheap and noisy);
others once. Score is the mean.
DECISION effort-as-arm: effort (reasoning variant) is a third dial that moves quality, wall
and cost together, and it has only ever been set by hand. A trial arm is (model, effort), not
model. For each candidate model: standard/tolerant roles trial the seat's current effort and
one step lower; critical roles trial current and one step higher. The operator declined to
hand-set lane-worker/researcher effort; the trials decide those too.
DECISION premium-tier: DevPass caps premium-tier model usage per week (observed: HTTP 402
"used your weekly allowance for premium-tier models on the max plan"). The operator does not
have the tier list; it is researched from DevPass/llmgateway documentation and the CLI binary,
and confirmed over time by journaled 402s. Every seat gets a quota_fallback: the best
non-premium arm within the role's band, taken from the same trial scores. Workers fall back
automatically on a 402; the interactive seat is switched manually by the operator, guided by
`trial-report interactive`.
DECISION interactive-fallback: the operator chose glm-5.3 as the manual standard-tier fallback
for the interactive seat (Fable 5.1) while the premium allowance is exhausted, citing its 1M
context window, long-horizon planning and clear prose. Set in the registry on 2026-09-04 as an
interim choice; glm-5.3 is a named nominee for the interactive trial, whose tasks and rubric
must measure exactly those three qualities (planning 4 / context 3 / clarity 3 of 10).
`trial-report interactive` names the measured best non-premium arm and the operator revises.
DECISION interim-standard-seats: the premium 402 recurs intermittently (hit again 20:03 UTC
after clearing at 19:00), and phase 6 burned twice on it because both the implementer seat
(sonnet-4-6) and its family fallback (gpt-5.4) are premium. Until the trials decide, every
worker seat and fallback is moved to a standard-tier model per premium.json: implementer /
document-writer / frontend-worker / reviewer-a -> claude-sonnet-5; reviewer-b -> gpt-5.3-codex;
researcher / reviewer-c -> gemini-3.1-pro-preview; fallbacks likewise standard. The
interactive seat stays on Fable with the operator's manual glm-5.3 fallback. Recorded in
each role's history in roles/registry.json.
DECISION no-premium-seats: DevPass defines premium-tier as models priced at $5+/M input or
$15+/M output tokens (source: plans/004-seat-selection/PREMIUM-TIER.md). The operator's policy
is that the pipeline prescribes NO premium model in any seat: primary, fallback or
quota_fallback, for every role including interactive. The operator may pick a premium model
by hand for an interactive session for their own use; the registry never does. The trials
therefore select only among standard-tier arms; premium arms are excluded from every pool.
The interactive seat moves to glm-5.3 (the operator's stated preference) as its interim
primary until `trial-report interactive` names the measured best standard-tier arm.
DECISION budget: round cap $50. Trials stop launching when the journal's summed cost for
this plan's trials passes the cap; whatever is scored decides; the rest waits.
DECISION candidates-approved: the operator approved the current candidate/task sets and their existing phase-5 publication in this conversation.

## Phase 1: decision rule and frontend-worker role (implementer)
TIMEOUT: 1800
EXIT: python3 tests/run.py 2>&1 | grep -q ' 0 failed'
EXIT: python3 -c "import sys;sys.path.insert(0,'.');from pipeline import roles;reg=roles.load();s=roles.seat('frontend-worker',reg);assert s['family']!=s['fallback_family'];assert reg['roles']['implementer']['band']=='standard' and reg['roles']['fast-worker']['band']=='tolerant' and reg['roles']['reviewer-a']['band']=='critical'"
EXIT: python3 -c "import sys;sys.path.insert(0,'.');from pipeline import trial;assert trial.BANDS=={'critical':0.5,'standard':1.5,'tolerant':2.5};assert callable(trial.select) and callable(trial.select_reviewers)"
EXIT: bin/pipeline check-agents
EXIT: git log -1 --format=%s | grep -qxF 'trial: band-then-wall-then-cost selection; frontend-worker role'

Implement DECISION seat-selection in pipeline/trial.py and wire it through pipeline/cli.py.
1. roles/registry.json: add `"band": "critical"|"standard"|"tolerant"` to every role per the
   decision; add role `frontend-worker` (purpose: browser-facing work: HTML/CSS/JS/TS
   components and their tests; model claude-sonnet-5; fallback gemini-3.1-pro-preview;
   effort high; tools like implementer; external_post false). Add roles/frontend-worker.md
   (prompt in the style of roles/implementer.md: component + test + exact ceremony; no
   framework churn). Add "frontend-worker" to roles.DISPATCHABLE. Run
   `bin/pipeline render-agents` so the generated agent files match (drift guard).
2. pipeline/trial.py:
   - `BANDS = {"critical": 0.5, "standard": 1.5, "tolerant": 2.5}`.
   - `trial_plan(role, candidates: list, tasks, rubric, workdir, reg, repeats: dict)`: N arms
     (incumbent + all candidates), blinded as A, B, C...; each task x arm x repeat is one
     phase with MODEL: pinned; mapping.json saved in the trial dir; a single scorer phase on
     a family not used by any arm; scores.json keyed by arm letter and task and repeat.
   - `select(arms: dict, band: float) -> dict`: arms = {model: {"quality": mean, "wall_s":
     mean per dispatch, "cost": mean per dispatch}}; keep arms with quality >= best - band;
     sort by wall_s then cost; return {"chosen": model, "band_kept": [...], "ranking":
     [...], "reasons": {...}} with every number included.
   - `select_reviewers(arms_by_model, band, families) -> list[3]`: best set of three models
     on three distinct families: maximise the count within the band, then minimise summed
     wall, then summed cost.
   - `decide(...)` uses cost in dollars from dispatch rows (`cost` field), never tokens as
     a proxy; tokens are reported alongside.
   - `apply(role, model, verdict, ...)` unchanged in spirit: refuses unless the verdict's
     chosen model is the one being applied; records history.
3. pipeline/cli.py: `trial <role> <model>...` accepts several candidates; `trial-apply
   <run> <role>` applies the verdict's chosen model (no model argument; the numbers choose);
   add `trial-report <run>` that prints the arms table (model, family, quality mean, wall
   mean, cost mean, in-band yes/no, chosen).
4. tests/test_suite.py: update the trial test for the new signature; add tests for `select`
   (band keeps a slightly-worse faster arm and drops a much-worse one; ties broken by wall
   then cost), `select_reviewers` (distinct families enforced), and registry bands present.
   Run `python3 tests/run.py`; all pass; the ratchet may rise, never fall.
Stage only: `git add -- pipeline/trial.py pipeline/cli.py pipeline/roles.py roles/registry.json roles/frontend-worker.md tests/test_suite.py tests/ratchet.json`
Commit exactly: `git commit -m 'trial: band-then-wall-then-cost selection; frontend-worker role'`
Do not push.

## Phase 2: re-probe under the worker agent (lane-worker)
AFTER: 1
LANES: candidates.txt
CEILING: 4
TIMEOUT: 900
ATTEMPTS: 1
EXIT: test -s "$LANE_OUT/$ITEM.json" && python3 -c "import json;d=json.load(open('$LANE_OUT/$ITEM.json'));assert d['model']=='$ITEM' and set(d)>={'reachable','tool_ok','edit_ok','wall_s','cost'},d"

Run exactly: `bash /root/pipeline/plans/004-seat-selection/probe.sh "$ITEM" "$LANE_OUT"`
It writes `$LANE_OUT/$ITEM.json`. Do not edit probe.sh. Do not interpret the result. If the
script itself errors, write the error to `$LANE_OUT/error.txt`. Final line: the json path.

## Phase 3: candidate table, task sets, rubrics (researcher)
AFTER: 6, 7
TIMEOUT: 2400
SURFACE: plans/004-seat-selection/CANDIDATES.md research-note
EXIT: test -s plans/004-seat-selection/probe-summary.md && test "$(grep -c '^| ' plans/004-seat-selection/probe-summary.md)" -ge 36
EXIT: test -s plans/004-seat-selection/CANDIDATES.md
EXIT: for r in interactive implementer fast-worker lane-worker researcher document-writer frontend-worker reviewer; do grep -q "^## $r" plans/004-seat-selection/CANDIDATES.md || { echo missing $r; exit 1; }; done
EXIT: test -s plans/004-seat-selection/candidates.json && python3 -c "import json;d=json.load(open('plans/004-seat-selection/candidates.json'));assert set(d)=={'interactive','implementer','fast-worker','lane-worker','researcher','document-writer','frontend-worker','reviewer'};assert all(2<=len(v)<=(20 if k=='interactive' else 10) and all('@' in a and a.split('@')[1] in ('low','medium','high') for a in v) for k,v in d.items()),{k:v for k,v in d.items()}"
EXIT: for r in interactive implementer fast-worker lane-worker researcher document-writer frontend-worker reviewer; do test -s plans/004-seat-selection/tasks/$r.md && test "$(grep -c '^## ' plans/004-seat-selection/tasks/$r.md)" -ge 5 && test -s plans/004-seat-selection/rubrics/$r.md || { echo missing $r; exit 1; }; done
EXIT: ! grep -qiE 'https?://' plans/004-seat-selection/CANDIDATES.md

Inputs: probe json files under the lanes of this run (find the run with
`grep -l 004-seat-selection /root/.system/runs/*/journal.jsonl`, then
`<run>/phase-2/lanes/lane-*/<model>.json`); plans/004-seat-selection/nominees.txt (operator
nominees); roles/registry.json (incumbents, families, bands); pipeline/trial.py (how
trials and selection work after phase 1).

Write:
1. `probe-summary.md`: one table row per probed model: model | family | reachable | tool_ok |
   edit_ok | wall_s plain/tool/edit | tokens edit | cost total | error (truncated). Sort by
   family, then cost. Wall values are seconds and are real this time; quote them.
2. `candidates.json`: {role: ["model@effort", ...]} for the eight keys interactive, implementer,
   fast-worker, lane-worker, researcher, document-writer, frontend-worker, reviewer (reviewer
   is one pool for the three seats). Between 2 and 5 candidates per role, built by these
   rules, then deduplicated: (a) every operator nominee for that role from nominees.txt that
   is reachable AND tool_ok AND edit_ok; (b) the incumbent; (c) the cheapest reachable+tool_ok+
   edit_ok model in each of the three cheapest families; (d) the fastest (lowest edit wall)
   reachable+tool_ok+edit_ok model overall; (e) for the reviewer pool, ensure at least four
   distinct families are represented; (f) for the interactive role only, the question is
   "which is the smartest non-premium model for planning, large context and clear prose", so
   the pool is the incumbent plus the strongest standard-tier tool_ok+edit_ok model in EVERY
   family present (by highest per-token price within the family, as the best available proxy
   before scoring), plus the nominees; expect 8-10 models for that role and allow up to 20
   arms for it. Then expand each model into arms per DECISION
   effort-as-arm: the role's current effort (roles/registry.json) plus one step lower for
   standard/tolerant roles or one step higher for critical roles (steps: low < medium < high;
   at the end of the scale, one arm). Write arms as "model@effort". A nominee that failed tool_ok or edit_ok is excluded and
   listed under "Excluded nominees" in CANDIDATES.md with the probe text that shows why.
3. `CANDIDATES.md` (register research-note; NO URLs anywhere; no external benchmarks, only
   probe numbers): per role section `## <role>` with the incumbent, each candidate with its
   probe wall/cost/edit_ok, and one sentence on why it is in. Then `## Excluded nominees`,
   then `## Open questions`.
4. `tasks/<role>.md` for all eight roles, 5 tasks each under `## <name>` headings, doable
   inside this repo without committing; each names its deliverable path relative to the arm's
   output dir ($OUT) and what a scorer can check. Role-specific: interactive tasks are
   operator asks; the deliverable is the plan.md or quick command the model would issue (the
   scorer checks restraint: fewest phases, EXITs present, gates for irreversibles). The
   interactive seat has three distinct demands and the five tasks must cover all three:
   (a) long-horizon planning: at least two tasks are multi-stage asks (6+ implied steps,
   some irreversible) where the deliverable is a plan.md; the rubric rewards correct
   dependency structure, a gate before each irreversible step, and the fewest phases that
   still reach the outcome; (b) large context: at least one task requires reading a large
   supplied input before deciding (e.g. a 200+ row journal excerpt and three plan files, all
   given inline or by path under /root/.system/runs) and the rubric checks that the answer
   uses facts only present deep in that input; (c) clarity: at least one task is a status
   report from given journal facts, and the rubric scores the first sentence stating the
   outcome, an explicit waiting-on-you list (empty stated as empty), no preamble, and
   length under a stated cap. The operator named glm-5.3 as a nominee for this seat citing
   1M context, long-horizon planning and clear prose; treat those as the qualities to
   measure, not as facts. Rubric weights for interactive: planning 4, context 3, clarity 3.
   researcher tasks demand sourced findings from files in this repo only (no web), and the
   rubric penalises any claim without a path:line. document-writer tasks name a surface
   register and the rubric includes the SURFACE score. frontend-worker tasks ask for a small
   self-contained HTML/JS component plus a node-free test (python3 http.server + a checklist
   is fine); the rubric checks structure and accessibility attributes. reviewer tasks embed a
   small artifact with a planted defect; the rubric is precision/recall on the planted
   defects plus verdict correctness.
5. `rubrics/<role>.md`: 0-10, 4-6 weighted criteria summing to 10, each checkable from the
   deliverable alone.
Do not edit roles/registry.json. Do not commit.

## Phase 4: approve candidates and task sets (gate)
AFTER: 3, 9, 10
GATE: /root/pipeline/plans/004-seat-selection/.approve-candidates

## Phase 5: land selection materials (implementer)
AFTER: 4, 8, 10
TIMEOUT: 600
EXIT: git log -1 --format=%s | grep -qxF 'seat-selection: probe results, candidates, trial tasks and rubrics'
EXIT: test -z "$(git status --porcelain -- plans/004-seat-selection plans/003-model-selection)"
EXIT: git fetch -q origin main && git diff --quiet HEAD origin/main

Stage only: `git add -- plans/003-model-selection plans/004-seat-selection`. Commit exactly:
`git commit -m 'seat-selection: probe results, candidates, trial tasks and rubrics'`
Then `git push origin main`. If the hook or push fails, write NOTES.md and stop; no flags.
Final lines: `git log -1 --format='%h %s'`.

## Phase 6: effort as a trial arm (implementer)
AFTER: 2
TIMEOUT: 1800
EXIT: python3 tests/run.py 2>&1 | grep -q ' 0 failed'
EXIT: python3 -c "import sys;sys.path.insert(0,'.');from pipeline import plan;p=plan.parse_text('## Phase 1: a (implementer)\nMODEL: x/y\nEFFORT: low\n');assert p.by_number(1).effort=='low'"
EXIT: ! python3 plans/004-seat-selection/check_effort_reject.py; python3 plans/004-seat-selection/check_effort_reject.py --positive
EXIT: python3 -c "import sys;sys.path.insert(0,'.');from pipeline import trial;assert callable(trial.parse_arm) and trial.parse_arm('gpt-5.6-luna@low')==('gpt-5.6-luna','low')"
EXIT: bin/pipeline check-agents
EXIT: git log -1 --format=%s | grep -qxF 'trial: effort is part of the arm'

Implement DECISION effort-as-arm.
1. pipeline/plan.py: new directive `EFFORT: low|medium|high` (PlanError on anything else);
   Phase.effort defaults to None (seat's registry effort). Document it in the module
   docstring's grammar list.
2. pipeline/engine.py: when ph.effort is set, pass it to the dispatch instead of the seat's
   effort (dispatch.run_dispatch gets an `effort` parameter; default None -> seat effort).
3. pipeline/trial.py: arms are "model@effort" strings; `parse_arm(s) -> (model, effort)`
   (effort defaults to the role's registry effort when absent). trial_plan pins both MODEL:
   and EFFORT: per arm phase; mapping.json records both; select/select_reviewers operate on
   arm strings; apply(role, arm, ...) writes both model and effort to the registry (and the
   history entry). The scorer must still see only arm letters.
4. pipeline/cli.py: `trial <role> <arm>...` accepts model@effort; `trial-report` shows
   effort as a column; `trial-apply` writes model+effort.
5. tests/test_suite.py: EFFORT parses and rejects bad values; the engine passes EFFORT to the
   worker (the fake records --variant; assert it); parse_arm; apply writes effort. Ratchet may
   rise, never fall. Run `bin/pipeline render-agents` if the registry changed (it should not).
Stage only: `git add -- pipeline/plan.py pipeline/engine.py pipeline/dispatch.py pipeline/trial.py pipeline/cli.py tests/test_suite.py tests/ratchet.json`
Commit exactly: `git commit -m 'trial: effort is part of the arm'`
Do not push.

## Phase 7: research the premium-tier model list (researcher)
AFTER: 2
TIMEOUT: 2400
SURFACE: plans/004-seat-selection/PREMIUM-TIER.md research-note
EXIT: test -s plans/004-seat-selection/PREMIUM-TIER.md
EXIT: test -s plans/004-seat-selection/premium.json && python3 -c "import json;d=json.load(open('plans/004-seat-selection/premium.json'));assert set(d)=={'premium','standard','unknown','sources','method'};assert isinstance(d['premium'],list) and isinstance(d['sources'],list)"
EXIT: python3 -c "import json;d=json.load(open('plans/004-seat-selection/premium.json'));c=[l.strip() for l in open('plans/004-seat-selection/candidates.txt') if l.strip() and not l.startswith('#')];cov=set(d['premium'])|set(d['standard'])|set(d['unknown']);missing=[m for m in c if m not in cov];assert not missing,missing"
EXIT: grep -q '^## Sources' plans/004-seat-selection/PREMIUM-TIER.md && grep -q '^## Method' plans/004-seat-selection/PREMIUM-TIER.md

Find out which models on the llmgateway-devpass provider count as "premium-tier" for the
DevPass weekly allowance. Observed fact to start from: on 2026-09-04 07:17 UTC, dispatches to
claude-sonnet-4-6 and gpt-5.4 returned HTTP 402 with the message "You've used your weekly
allowance for premium-tier models on the max plan. Redeem a Reset Pass from your dashboard
for an instant reset, upgrade for a higher allowance, or use any standard model now." At
07:25 UTC claude-haiku-4-5, gpt-5.6-luna, gpt-5.4-mini, deepseek-v4-pro, gemini-3.7-flash,
claude-sonnet-5 and gpt-5.6-sol answered normally (so they are standard-tier, OR the
allowance had reset; by 19:00 UTC all 36 models answered).
Sources to consult, in this order, citing each by URL or path:
1. The devpass-code binary: `grep -a -o -E '.{0,200}(premium|standard[- ]tier|allowance|coding subscription|Reset Pass).{0,200}' /usr/local/bin/devpass-code` and any embedded plan/model metadata (JSON blobs near "llmgateway-devpass").
2. DevPass web pages: try https://devpass.dev, https://devpass.ai, https://www.devpass.io, https://devpass.sh and their /pricing, /docs, /models, /plans paths; follow links about plans, tiers, allowances. Use webfetch; record HTTP status of each attempt.
3. llmgateway.io docs and API: https://llmgateway.io, https://docs.llmgateway.io, https://api.llmgateway.io/v1/models (the models endpoint needs the key at
   ~/.local/share/devpass-code/auth.json -> llmgateway-devpass.key; use it ONLY in an
   Authorization header, never print it, never write it to a file). Look for tier, premium,
   plan or pricing-band fields; compare per-token prices: if the tier is undocumented, a price
   threshold may separate the sets. Say so explicitly if you infer rather than read.
4. The local journals: `grep -l 402 /root/.system/runs/*/phase-*/attempt-*/try-*/transcript.jsonl` for any other models that hit the 402.
Write:
- `PREMIUM-TIER.md` (register research-note): first sentence is the answer (the list, or
  "DevPass does not publish the list; the following is inferred from X"). Then `## Premium`,
  `## Standard`, `## Unknown` lists with one line of evidence each, `## Method`, `## Sources`
  (URL or path per source, with what it did or did not say, including 404s), `## Open questions`.
- `premium.json`: {"premium": [...], "standard": [...], "unknown": [...], "sources": [...],
  "method": "..."} covering every model in candidates.txt exactly once.
No guessing presented as fact: a model goes in "unknown" unless a source or the 402 evidence
places it. Do not edit the registry. Do not commit.

## Phase 8: quota handling and premium tags (implementer)
AFTER: 6, 7
TIMEOUT: 1800
EXIT: python3 tests/run.py 2>&1 | grep -q ' 0 failed'
EXIT: python3 -c "import sys;sys.path.insert(0,'.');from pipeline import dispatch;assert dispatch.is_quota_error('{\"error\":{\"data\":{\"statusCode\":402,\"message\":\"You\u2019ve used your weekly allowance for premium-tier models\"}}}')"
EXIT: python3 -c "import sys;sys.path.insert(0,'.');from pipeline import roles;reg=roles.load();assert 'models' in reg and all('premium' in v for v in reg['models'].values());assert all('quota_fallback' in r for r in reg['roles'].values());prem={m for m,v in reg['models'].items() if v['premium'] is True};assert not any(r['fallback'] in prem or r['quota_fallback'].split('@')[0] in prem for r in reg['roles'].values()),'premium fallback present'"
EXIT: grep -q 'quota' pipeline/bench.py && grep -q '"quota"' pipeline/journal.py
EXIT: bin/pipeline check-agents
EXIT: git log -1 --format=%s | grep -qxF 'engine: 402 quota outcome, quota_fallback seats, premium tags'

Implement DECISION premium-tier.
1. pipeline/dispatch.py: `is_quota_error(text) -> bool` matching the 402 premium-tier
   signature (statusCode 402 AND "premium-tier" or "allowance" in the message). In
   run_dispatch, when the transcript contains it and no step completed, outcome = "quota"
   (new terminal outcome; add to journal.TERMINAL_DISPATCH).
2. roles/registry.json: new top-level `"models": {model: {"premium": true|false|"unknown"}}`
   from plans/004-seat-selection/premium.json (every model in candidates.txt). Every role
   gets `"quota_fallback": "<model>@<effort>"` chosen for now as the cheapest standard-tier
   tool_ok+edit_ok model of the role's current family or, failing that, any family (the
   trials will replace it with the best in-band non-premium arm). roles.validate: the
   quota_fallback must not be premium, AND the family `fallback` must not be premium either
   (invariant: only a seat's primary may be premium; every fallback under it is standard-tier,
   so neither a 402 nor a provider outage can strand a phase on two premium models). Add a
   test that a registry with a premium family fallback is rejected.
3. pipeline/engine.py `_dispatch`: model order is [seat, quota_fallback if outcome was quota,
   family fallback if the seat never completed a step for another reason]. A quota outcome
   never burns the family fallback and never counts as a phase attempt failure by itself: if
   the quota_fallback succeeds the phase proceeds normally. Journal dispatch.start carries
   `quota_fallback: true` on the retry.
4. pipeline/bench.py: quota outcomes are their own bucket ("premium quota hits"), neither
   machine failure nor host outage; also a per-model table of quota hits (this is how the
   premium list confirms itself over time).
5. pipeline/status.py: if any dispatch in a run ended with quota, the waiting-on-you list
   says so once ("premium allowance hit on <models>; resets <when if known>").
6. pipeline/trial.py: select() also returns `"quota_fallback": <best non-premium arm within
   the band or None>` using reg["models"]; trial-apply writes it; trial-report shows premium
   status per arm and the quota fallback.
7. tests: quota signature detection; engine falls to quota_fallback on a quota transcript
   (fake worker: add `FAKE: quota` that emits the 402 error event and exits 1); validate
   rejects a premium quota_fallback; bench shows the bucket. Ratchet may rise, never fall.
Run `bin/pipeline render-agents` (registry changed) so the drift guard passes.
Stage only: `git add -- pipeline/dispatch.py pipeline/engine.py pipeline/journal.py pipeline/bench.py pipeline/status.py pipeline/trial.py pipeline/roles.py pipeline/cli.py roles/registry.json tests/test_suite.py tests/fake-devpass-code tests/ratchet.json`
Commit exactly: `git commit -m 'engine: 402 quota outcome, quota_fallback seats, premium tags'`
Do not push.

## Phase 9: revise the interactive pool and add premium status (researcher)
AFTER: 3
TIMEOUT: 1800
EXIT: python3 -c "import json;d=json.load(open('plans/004-seat-selection/candidates.json'));v=d['interactive'];ms={a.split('@')[0] for a in v};assert 8<=len(ms)<=10 and len(v)<=20,(len(ms),v);assert {'claude-fable-5-1','glm-5.3','claude-sonnet-5','gemini-3.1-pro-preview','deepseek-v4-pro','kimi-k3','grok-4-6'}<=ms,ms"
EXIT: python3 -c "import json;d=json.load(open('plans/004-seat-selection/candidates.json'));p=json.load(open('plans/004-seat-selection/candidates-premium.json'));arms={a for v in d.values() for a in v};assert set(p)==arms,(arms^set(p));assert all(x in (True,False,'unknown') for x in p.values());[(k,v) for k,v in d.items() if not any(p[a] is False for a in v)]==[] or (_ for _ in ()).throw(AssertionError('role without a standard arm'))"
EXIT: grep -q 'planning' plans/004-seat-selection/rubrics/interactive.md && grep -qi 'context' plans/004-seat-selection/rubrics/interactive.md && grep -qi 'clarity' plans/004-seat-selection/rubrics/interactive.md
EXIT: test "$(grep -c '^## ' plans/004-seat-selection/tasks/interactive.md)" -ge 5
EXIT: sed -n '/^## interactive/,/^## implementer/p' plans/004-seat-selection/CANDIDATES.md | grep -c '^- \*\*' | awk '{exit !($1>=8)}'

Phase 3 ran on an earlier brief. Revise three things; leave every other role's entries as they
are.
1. `candidates.json["interactive"]`: the question for this seat is "which is the smartest
   non-premium model for long-horizon planning, large context and clear prose". Pool = the
   incumbent claude-fable-5-1, the nominee glm-5.3, and the strongest standard-tier
   tool_ok+edit_ok model in every family present in premium.json's standard list (strongest =
   highest per-token completion price in the family from the llmgateway models API, as the
   proxy before scoring; for openai choose between gpt-5.6-terra and gpt-5.3-codex by that
   rule). Expect: claude-sonnet-5, gpt-5.6-terra or gpt-5.3-codex, gemini-3.1-pro-preview,
   deepseek-v4-pro, glm-5.3, kimi-k3, grok-4-6, minimax-m3, seed-1-8-251228 as applicable.
   8 to 10 models, each at two efforts (current `high` and one lower: `medium`), max 20 arms.
   Drop glm-5.3-flash, gpt-oss-120b and deepseek-v4-flash from this role (they stay wherever
   else they appear).
2. `candidates-premium.json`: {arm: true|false|"unknown"} for EVERY arm in candidates.json
   (all roles), from plans/004-seat-selection/premium.json. Every role must keep at least one
   arm with status false.
3. `tasks/interactive.md` and `rubrics/interactive.md`: ensure the five tasks cover (a) two
   multi-stage asks (6+ implied steps, some irreversible) whose deliverable is a plan.md,
   scored on dependency structure, a gate before each irreversible step, fewest phases;
   (b) one large-context task whose correct answer depends on facts deep inside a supplied
   200+ row journal excerpt plus three plan files (point at real files under
   /root/.system/runs and /root/pipeline/plans, or embed the excerpt), scored on using those
   deep facts; (c) one status-report task from given journal facts, scored on first sentence
   stating the outcome, explicit waiting-on-you list (an empty one stated), no preamble,
   length cap. Rubric weights exactly: planning 4, context 3, clarity 3 (sum 10), each
   criterion checkable from the deliverable alone. Rewrite the files if they do not meet this.
4. Update the `## interactive` section of CANDIDATES.md to list the revised pool with probe
   wall/cost/edit_ok per model and one sentence each; keep the register (research-note),
   no URLs.
Do not edit the registry. Do not commit.

## Phase 10: no premium seats, anywhere (implementer)
AFTER: 8, 9
TIMEOUT: 1800
EXIT: python3 tests/run.py 2>&1 | grep -q ' 0 failed'
EXIT: python3 -c "import sys,json;sys.path.insert(0,'.');from pipeline import roles;reg=roles.load();prem={m for m,v in reg['models'].items() if v['premium'] is True};bad=[(n,k,r[k]) for n,r in reg['roles'].items() for k in ('model','fallback','quota_fallback') if r[k].split('@')[0] in prem];assert not bad,bad;assert reg['roles']['interactive']['model']=='glm-5.3'"
EXIT: python3 -c "import json;d=json.load(open('plans/004-seat-selection/candidates.json'));p=json.load(open('plans/004-seat-selection/candidates-premium.json'));bad=[a for v in d.values() for a in v if p.get(a) is not False];assert not bad,bad"
EXIT: python3 -c "import sys;sys.path.insert(0,'.');from pipeline import roles,trial;assert 'premium' in open('pipeline/roles.py').read() and 'premium' in open('pipeline/trial.py').read()"
EXIT: bin/pipeline check-agents
EXIT: git log -1 --format=%s | grep -qxF 'policy: no premium-tier model in any seat'

Implement DECISION no-premium-seats.
1. pipeline/roles.py `validate`: reject a registry where ANY role's model, fallback or
   quota_fallback is a model tagged premium in reg["models"]; the error names the role and
   field. Keep the existing rules (cross-family fallback, sealed reviewers, etc.).
2. roles/registry.json: interactive model -> glm-5.3 (effort high), fallback -> a
   standard-tier model on another family (claude-sonnet-5), quota_fallback -> a standard arm;
   append a history entry citing DECISION no-premium-seats. Confirm every other seat is
   already standard (they should be after DECISION interim-standard-seats); fix any that is
   not. Run `bin/pipeline render-agents`.
3. pipeline/trial.py: `select` and `select_reviewers` exclude arms whose model is premium
   before banding (a premium arm can never be chosen); the report still shows them if they
   were scored, marked "excluded: premium". `apply` refuses a premium arm.
4. plans/004-seat-selection/candidates.json and candidates-premium.json: remove every arm whose
   premium status is true (claude-fable-5-1 from interactive, and any other), and every arm
   marked "unknown" unless premium.json places its model in standard; update the matching
   sections of CANDIDATES.md (register research-note, no URLs) so the listed pools equal the
   json. Note in `## Open questions` that Fable/Opus/Sonnet-4.x/GPT-5.4/5.5/5.6-sol were
   excluded by policy, not by score.
5. tests: validate rejects a premium primary; select never returns a premium arm even when it
   scores best; the registry on disk has no premium anywhere. Ratchet may rise, never fall.
Stage only: `git add -- pipeline/roles.py pipeline/trial.py roles/registry.json tests/test_suite.py tests/ratchet.json plans/004-seat-selection/candidates.json plans/004-seat-selection/candidates-premium.json plans/004-seat-selection/CANDIDATES.md`
Commit exactly: `git commit -m 'policy: no premium-tier model in any seat'`
Do not push.
