# Token Efficiency Baseline Measurements

## Status

✓ **Complete** - Both child runs completed successfully with all immutable fixture checks passing.

## Runs

- **Repair**: run_20260905T001926_cbe4f7
- **Researcher**: run_20260905T002109_0c0fd0
- **Conversation**: ses_f957d9dc1ffepFpvyzZoBE3AmE
- **Timestamp**: 2026-09-05T00:21:53+00:00

## Aggregate Metrics

### Token Counts
- **Total Input Tokens**: 65,319
- **Total Output Tokens**: 3,486
- **Total Reasoning Tokens**: 2,883
- **Total Tokens**: 71,688

### Cost & Time
- **Total Cost**: $0.223394 USD
- **Total Wall Time**: 106.65 seconds

## Per-Run Summary

### Repair Run (run_20260905T001926_cbe4f7)

**Role**: Implementer (fix normalize function bug)
**Status**: Completed with cross-review

**Metrics**:
- Input Tokens: 45,508
- Output Tokens: 3,302
- Reasoning Tokens: 2,449
- Cost: $0.176356
- Wall Time: 90.88s

**Dispatches**:
| Role | Model | Input | Output | Reasoning | Cost | Wall (s) |
|------|-------|-------|--------|-----------|------|----------|
| implementer | llmgateway-devpass/claude-sonnet-5 | 18,495 | 1,126 | 213 | $0.057057 | 28.29 |
| reviewer-b | llmgateway-devpass/gpt-5.3-codex | 8,144 | 833 | 2,215 | $0.063465 | 36.03 |
| reviewer-a | llmgateway-devpass/claude-sonnet-5 | 18,869 | 1,343 | 21 | $0.055834 | 26.56 |

### Researcher Run (run_20260905T002109_0c0fd0)

**Role**: Researcher (extract facts from synthetic journal)
**Status**: Completed

**Metrics**:
- Input Tokens: 19,811
- Output Tokens: 184
- Reasoning Tokens: 434
- Cost: $0.047038
- Wall Time: 15.77s

**Dispatches**:
| Role | Model | Input | Output | Reasoning | Cost | Wall (s) |
|------|-------|-------|--------|-----------|------|----------|
| researcher | llmgateway-devpass/gemini-3.1-pro-preview | 19,811 | 184 | 434 | $0.047038 | 15.77 |

## Verification Summary

### Immutable Fixture Hashes
✓ All immutable test and checker files verified unchanged:
- repair/tests/test_calc.py: c0b779f34bcb4b56297f7db4426e1566ea3037488dcc8deeba39820d0c8212a9
- researcher/answer_key.py: a8705017707a9c5c638659898a6fed9f46aba5b0fb8f459f3635a387945b7086
- researcher/journal.jsonl: 9a0441c953f75a3f5f6da7487bb2e22f082b627732b79eba35b1a824cdae48b8

### Modifiable Artifacts
- ✓ repair/calc.py correctly modified by implementer (bug fix applied)
- ✓ researcher/findings.txt created by researcher (within allowed changes)

## Settings

### Frozen Role/Model Configuration
- **Provider**: llmgateway-devpass
- **Implementer**: claude-sonnet-5 (fallback: gpt-5.6-luna)
- **Reviewer-A**: claude-sonnet-5 (fallback: deepseek-v4-pro)
- **Reviewer-B**: gpt-5.3-codex (fallback: grok-4-6)
- **Researcher**: gemini-3.1-pro-preview (fallback: claude-sonnet-5)

## Limitations

1. **Answer-Key Exposure**: The researcher answer checker is readable inside the prepared fixture (researcher/fixtures/answer_key.py). This is a workflow baseline, not proof of blind research quality. The answer key was accessible during the researcher's execution.

2. **Cache Token Attribution**: Cache read/write tokens are provider-reported totals. These are distinguished from input/output token counts and are not conflated with context length or actual cache savings.

3. **Reasoning Tokens**: Reasoning token counts come from model provider telemetry where available; some models may not report this separately.

4. **Token Counting**: Token counts are aggregated from individual dispatch records and provider-reported totals; no synthetic replay or cumulative recalculation is used.

## Regeneration

To rerun these measurements using the same conversation and fixtures:

```bash
cd /root/pipeline/plans/005-token-efficiency
python3 run_baseline.py
```

If runs already exist for this exact conversation and plan combination, they will be reused instead of rerun (idempotent check-in).

---

**Generated**: 2026-09-05T00:21:53+00:00
**Workflow**: plan-005-token-efficiency baseline measurements
**No production code changes, no commits, no registry updates**
