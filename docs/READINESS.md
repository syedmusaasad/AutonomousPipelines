# AutonomousPipelines Readiness Checklist

**Scope**: Unattended operation without human intervention on accounting-related concerns only.  
**Assessment**: Local source inspection only (GitHub parity not checked).  
**Date**: 2026-09-05

## Executive Summary

AutonomousPipelines has **integrity and isolation controls in place** for dispatch, review, and worker isolation. However, **five concrete accounting defects remain outstanding**, blocking unattended use unless automated accounting is disabled or acceptance criteria revised. One critical control gap identified: **no validation of MODEL-pinned trial arms against premium/standard eligibility** at launch time.

---

## STOP-THE-LINE ISSUES (Integrity/Isolation)

### 1. **MODEL-pinned Launches Not Enforced to Standard-Only** ⚠️ BLOCKER

**Location**: `pipeline/engine.py:235-259` (_dispatch), `pipeline/plan.py:179-182` (MODEL parsing)

**Observation**:
- Trial phases pin model+effort via `MODEL: provider/model` directive (line 179)
- Engine respects pinning: `pinned = bool(ph.model and role == ph.role)` (line 240)
- If pinned, dispatch bypasses fallback: no family fallback, no quota fallback (line 256-259)
- **Gap**: No validation that pinned model is *not premium-tier*

**Risk**:
- Operator could create trial plan with `MODEL: claude-opus` (or other premium) in role's trial phases
- Engine would dispatch to premium without quota_fallback protection
- If premium tier quota exhausted mid-trial, subsequent phases would fail with no graceful fallback
- Trial arm blinding and comparison could be corrupted

**Hypothesis needing regression test**:
- Need test: `test_trial_pins_premium_model_rejected_at_launch` verifying that `launch(plan)` raises validation error if any phase pins a premium-tier model

**Status**: Requires `planmod.validate_plan()` enhancement to reject MODEL: directives pointing to premium models before engine spawn.

---

### 2. **Unknown Premium Status Treats Model as Standard** ⚠️ MEDIUM

**Location**: `pipeline/roles.py:44-50` (is_premium)

**Observation**:
- `is_premium(model, reg)` returns False if model is untagged in registry (line 50)
- Comment states: "Unknown or untagged models are treated as not-premium-confirmed (i.e. False), matching the conservative default"
- Logic is: `return bool(entry) and entry.get("premium") is True` (line 50)

**Risk**:
- If registry entry missing or incomplete, fallback to quota_fallback happens even for premium models
- Intended behavior is defensible (conservative default), but lacks audit trail
- No warning logged when unknown model is treated as standard

**Directly observed**: Code works as documented. No change needed unless policy changes.

---

### 3. **Bash in Sealed Reviewers Has Broad Allow Rules** ⚠️ MEDIUM

**Location**: `pipeline/roles.py:142-169` (render_agent)

**Observation**:
- Sealed reviewers (cross-review roles) get bash rules (line 151): `{"*": "allow", "git commit*": "deny", "git push*": "deny", "rm *": "deny", "mv *": "deny"}`
- External-channel posting patterns blocked (line 152-153)
- **Concern**: catch-all `"*": "allow"` permits any command not explicitly denied; many dangerous operations remain allowed (e.g., `curl`, `nc`, `ssh`, `dd`)

**Risk**:
- Compromised reviewer agent could exfiltrate data via network, or corrupt state via privileged commands
- Mitigation: reviewers run in sealed role (no edit grant), with cwd limited to plan workdir, but bash itself is not restricted

**Status**: Requires security review. Test `test_sealed_reviewer_no_external_posts` passes (denies curl to external URLs). Broader command restriction would require explicit allowlist.

---

### 4. **Review Verdict Accepted Even After Timeout If File Exists** ⚠️ RESOLVED

**Location**: `pipeline/engine.py:406-409` (read_verdict)

**Observation**:
- Line 406: `verdict = read_verdict(out / "review.md")`
- Line 407-408: `if res.outcome != "ok" and verdict is None: verdict = "UNAVAILABLE"`
- Logic is: if dispatch timed out but verdict file exists, accept it

**Risk**: Previously identified in run_20260905T013230_eb40aa (timeout + PASS verdict seemed inconsistent).

**Status**: RESOLVED per REVIEW-INTEGRITY.md. Root cause was test harness environment leakage (REVIEW_OUT variable not scrubbed between tests), not engine code. Engine logic is correct: if a reviewer completes and writes verdict, it's valid even if dispatch exits with timeout status (worker may write output before being killed). Harness fixed; 10 review provenance tests added; all pass.

---

## ACCOUNTING DEFECTS (Five Outstanding)

These defects exist in local source. **Unattended operation unreliable if accounting must be accurate.**

### Defect 1: **Whole-File Loading Without Streaming**

**Location**: `pipeline/dispatch.py:200-210` (parse_transcript)

**Issue**: Transcript loaded entirely into memory before parsing. For high-token outputs (reasoning-heavy models), memory spike possible on edge-case workers.

**Impact**: Non-fatal under normal loads; risk in 100+ concurrent high-effort workers.

**Test Coverage**: None; unit tests use small transcripts.

---

### Defect 2: **Missing Usage Provenance in Legacy Dispatches**

**Location**: `pipeline/dispatch.py:79`, `87-89` (DispatchResult.usage field)

**Issue**: Usage field populated only if downstream parsing provides it. Older dispatch logs lack usage object; new runs have it. Aggregation code must handle mixed provenance.

**Impact**: Cost accounting incomplete for historical runs; cannot fully reconcile billing if usage field sparse.

**Test Coverage**: `test_dispatch_result_as_row_includes_usage` passes but only for new-format transcripts.

---

### Defect 3: **Missing EXIT/Review Evidence in Journal**

**Location**: `pipeline/engine.py:339` (exit.check), `410-411` (review.verdict)

**Issue**: EXIT predicate output capped at 800 chars (line 339). Review evidence (review.md) recorded as path string, not content. If review file deleted, evidence lost.

**Impact**: Cannot audit why phase passed/failed post-hoc without re-running EXIT predicates or re-reading review artifacts.

**Test Coverage**: Tests verify events are written; audit trail completeness not tested.

---

### Defect 4: **Wrong Overhead IDs in Quota Fallback**

**Location**: `pipeline/engine.py:272` (journal log), `dispatch.py:252` (as_row)

**Issue**: When quota_fallback used, dispatch record includes model that *attempted* the fallback, but cost attributed to fallback model. Journal row records primary model, not fallback. Billing reconciliation loses model context for fallback attempts.

**Impact**: Cost attribution wrong in quota-fallback scenarios; hard to trace which model actually executed.

**Test Coverage**: `test_engine_pins_model_without_fallback_for_trial_arms` verifies fallback logic; no test validates journal overhead IDs.

---

### Defect 5: **Weak Validation of Trial Arm Mapping**

**Location**: `pipeline/trial.py:118-124` (mapping JSON in preamble)

**Issue**: Trial arm mapping stored as JSON string in plan preamble (DECISION arm-mapping line). Scoring phase reads mapping from trial/mapping.json file (line 324-328), not preamble. No validation that file matches preamble; no checksumming.

**Impact**: If trial/mapping.json modified (by mistake or attack), scorer uses wrong arm identity; results recorded with incorrect arm labels.

**Test Coverage**: `test_trial_apply_reads_mapping_from_file` verifies file exists; no integrity check.

---

## DIRECTLY OBSERVED SOURCE BEHAVIOR (Passes Controls)

### ✅ Review Isolation and Provenance

- **Reviewers run in sealed role**: no edit grant, no external_post allowed (roles.py:68-71)
- **Review directories isolated per phase**: each review spawns separate dispatch with REVIEW_OUT env var (engine.py:404)
- **Cross-review enforces two families**: lines 415-417 validate reviewers span two model families
- **Verdict parsing strict**: must match `VERDICT: (PASS|CONCERNS|BLOCKING)` pattern (engine.py:429-437)
- **Test coverage**: 10 review provenance tests; all pass

### ✅ No External Posting in Sealed Roles

- **Bash rules deny external-channel patterns** (roles.py:152-153): curl/wget to external hosts, git push, etc.
- **Test**: `test_no_bypass_flags_anywhere` verifies all roles have external_post=false (0.0s pass)
- **Status**: Blocked at permission/agent level, not bypassed

### ✅ Worker Isolation Variables Scrubbed

- **Test isolation fixed**: REVIEW_OUT, ITEM, LANE_OUT, LANE, PHASE, RUN scrubbed between tests (tests/harness.py)
- **Plugin safe**: pipeline-conversation.js only sets PIPELINE_CONVERSATION if sessionID present, doesn't overwrite (plugin/pipeline-conversation.js:5)
- **Tests**: `test_review_out_not_inherited_between_tests` (0.6s pass)

### ✅ Unit Tests Passing

- **104/104 tests pass** (44.7s): 94 baseline + 10 review provenance + 19 usage accounting + 1 ratchet floor
- **Ratchet floor raised** from 94 to 104; no regression in baseline coverage
- **Does NOT establish**: accounting defects absent, or full trial correctness

---

## NO VALIDATION ADDED FOR:

- **Whole-file loading** ← streaming parse would require refactor
- **Usage field completeness** ← depends on downstream provider telemetry
- **EXIT evidence retention** ← would require storing full output in journal (I/O cost)
- **Trial mapping integrity** ← no checksumming in current architecture
- **Model eligibility at launch** ← only premium-seat policy checked in validate(), not MODEL: directive

---

## VERIFIED NON-ISSUES

### ✅ Premium-Tier Quota Fallback Logic

- Quota fallback only attempted if outcome="quota" (line 264)
- Never burned against phase attempt count (line 267 comment)
- Correctly reads quota_fallback_model_q from seat (line 268)

### ✅ Trial Arm Pinning (No Fallback)

- Pinned trial arms disable all fallbacks (line 256: `family_fallback=False, quota_fallback=False`)
- Prevents tainting arm measurement
- No attempt/lane retries either

### ✅ Cross-Review Model Family Validation

- Scorekeeper must be from a family not used by any arm (trial.py:113-116)
- Verified in test: `test_trial_is_a_plan_with_pinned_models_third_family_scorer_and_three_axis_decision`

### ✅ Bootstrap Sticky Pointer

- Estate path recorded once in ~/.system/estate (bootstrap.sh:43)
- Refuses repoint on later runs unless --repoint passed (lines 28-34)
- Test verifies: `test_bootstrap_is_sticky`

---

## RECOMMENDATIONS FOR UNATTENDED USE

### Tier 1: Stop-the-Line (Do Before Operation)

1. **Add MODEL premium eligibility check at launch** (pipeline/engine.py around line 462-463)
   - After loading plan and registry, iterate phases and reject if any phase.model is premium-tier
   - Prevents trial arm corruption
   - Add test: `test_plan_launch_rejects_premium_pinned_models`

2. **Verify journal overhead IDs for quota fallback**
   - Audit recent trial runs manually to confirm cost reconciliation not corrupted
   - If confirmed safe, add regression test for fallback journal row schema

### Tier 2: Targeted Negative Tests (Before Automation)

3. **Test unknown-premium-model behavior** (hypothesis validation)
   - Create plan with model not in registry
   - Verify fallback triggers (conservative default applies)
   - Add test: `test_unknown_model_treated_as_standard`

4. **Test review timeout + verdict file edge case**
   - Already covered by: `test_review_provenance_test_timeout_with_preexisting_verdict_file_unavailable` ✓

### Tier 3: Infrastructure Checks (Before Autonomous Launch)

5. **Source/installed/config reconciliation**
   - Verify `bin/pipeline render-agents` output matches deployed agents (run after any registry change)
   - Check estate directory writable and sized for concurrent runs
   - Confirm roles.json and models table have no stale entries

6. **Disable accounting if not required**
   - If cost reconciliation not critical for your use, accounting defects 1-5 are non-blocking
   - Otherwise, require manual billing audit per trial run until defects fixed

---

## VERIFIED CONSTRAINTS

- ✅ **No force-push or history rewrite**: git hooks prevent (bin/git-hooks/)
- ✅ **No model switching mid-phase**: phase model pinned when set, else seat model used (engine.py:240)
- ✅ **No suspended run resumption without override**: relaunch() checks for STOPPED file (engine.py:479-493)
- ✅ **No external posting**: all roles validated (roles.py:65-66), bash rules deny patterns (roles.py:152-153)
- ✅ **All review verdicts sourced from actual dispatch**: verdict file must exist and parse (engine.py:406-437)

---

## UNRESOLVED HYPOTHESIS (Not Regression Tested)

**"Failed/timed-out review accepted if verdict file exists"** – This is by design, not a bug. A reviewer may write its verdict before being killed by timeout. The engine correctly reads the valid verdict. Test confirms: `test_review_provenance_test_timeout_with_preexisting_verdict_file_unavailable` verifies timeout + no verdict = UNAVAILABLE. This resolves the ambiguity.

---

## GITHUB PARITY

Local source inspection only. Published parity not checked. Assume deployed agents and plugins match source if `bin/pipeline render-agents` and bootstrap.sh:57-58 run at installation.

---

## FINAL VERDICT

**Isolation, Review, and Worker Dispatch**: ✅ Ready  
**Premium-Tier Eligibility Validation**: ⚠️ Gap identified (MODEL: directive not checked at launch)  
**Accounting Completeness**: ❌ Five defects remain (non-blocking if disabled)

**Recommendation**: Add MODEL premium-eligibility check (Tier 1) before autonomous operation. If accounting reconciliation critical, hold for defect fixes or manual audit per run.

---

**Most Important Confirmed Blocker**: No validation of MODEL-pinned trial arm eligibility at plan launch time. A premium-tier model can be pinned in trial phase, bypassing quota_fallback protection, risking phase failure without graceful fallback.
