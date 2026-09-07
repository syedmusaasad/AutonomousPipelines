# Adoption dogfood

Run: `run_20260904T060316_431609` (stopped, originally owned by `ses_f957d9dc1ffepFpvyzZoBE3AmE`).
Caller conversation: `ses_f82fc65d2ffeijPxUbBnnx2gpn`.

## Before adoption

Command: `PIPELINE_CONVERSATION=ses_f82fc65d2ffeijPxUbBnnx2gpn bin/pipeline status --mine`

```text
No runs recorded for conversation ses_f82fc65d2ffeijPxUbBnnx2gpn.

In flight:
  (none)

Waiting on you:
  nothing
```

## Adopt into this conversation

Command: `PIPELINE_CONVERSATION=ses_f82fc65d2ffeijPxUbBnnx2gpn bin/pipeline adopt run_20260904T060316_431609`

```text
adopted run_20260904T060316_431609 from ses_f957 -> ses_f82f
```

## After adoption (`status --mine`)

```text
Nothing in flight; 1 run(s) stopped and need judgment, 0 done (conversation ses_f82fc65d2ffeijPxUbBnnx2gpn).

In flight:
  (none)

Stopped (need judgment):
  run_20260904T060316_431609  stopped:gate_failed  plan=/root/pipeline/plans/003-model-selection/plan.md

Waiting on you:
  - [run_20260904T060316_431609] phase 4: write sentinel /root/pipeline/plans/003-model-selection/.approve-nominations
  - [run_20260904T060316_431609] deliberate stop [gate_failed]: phase 4 sentinel /root/pipeline/plans/003-model-selection/.approve-nominations says: no: superseded by plan 004 (operator decision seat-selection); nominations rewritten there  (resume with `pipeline resume run_20260904T060316_431609` after judging)
```

## Return to original conversation

Command: `PIPELINE_CONVERSATION=ses_f957d9dc1ffepFpvyzZoBE3AmE bin/pipeline adopt run_20260904T060316_431609`

```text
adopted run_20260904T060316_431609 from ses_f82f -> ses_f957
```

The two adoption receipts (`ses_f957 -> ses_f82f`, then `ses_f82f -> ses_f957`) are the custody-chain proof; the run remains stopped with the same gate-failed execution state.
