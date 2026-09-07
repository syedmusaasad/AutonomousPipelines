# Fallback sweep findings (2026-09-06)

Criterion: each fallback must be the next-best by the SAME criteria as its primary,
non-premium, and on a different family from its own primary. Numbers from
docs/model-selection/SEAT-EVIDENCE.md (AA leaderboard + LiveBench 2026-06-25, fetched 2026-09-05).
Two worker attempts timed out mid-analysis; the interactor completed the judgment from the
same tables, recorded as DECISION fallback-sweep in the registry history.

## Changed
- researcher: claude-sonnet-5 -> glm-5.3. Role criterion = reading/digesting.
  glm-5.3 beats sonnet-5 on every axis: LB summarize 74.55 vs 71.22; AA 49 vs 45; TTFT 2.0s vs 2.9s.
- document-writer: claude-sonnet-5 -> glm-5.3. Role criterion = prose.
  Same evidence class; glm-5.3 leads sonnet-5 on summarize and AA.

## Kept, with reasons
- interactive: claude-sonnet-5. Criterion = responsive conversation. TTFT 2.9s vs kimi-k3's 28.5s
  (kimi is smarter, AA 50 vs 45, but a 28.5s first reply disqualifies it for interactive use);
  glm-5.3 is ineligible (same model as the primary).
- implementer: gpt-5.6-luna. Code tasks at speed/cost: 125 t/s, $0.10/task; deepseek-v4-pro is
  slower and no better on LiveBench code.
- fast-worker: deepseek-v4-flash. 131 t/s, $0.14/task, TTFT 0.94s — best-in-class for the role.
- lane-worker: gpt-5.6-luna. Same criterion; symmetric with fast-worker.
- reviewer-a: deepseek-v4-pro (AA 42, strong analyst, deepseek family distinct from terra's openai).
- reviewer-b: claude-sonnet-5 (superseded 2026-09-07: codex was constraint-satisficing; sonnet-5 strictly better on AA 45 vs 37* and TTFT 2.9s vs 58.9s; anthropic family free since the Terra swap).
- reviewer-c: gemini-3.8-flash (AA 47, TTFT 6.2s at high — fine for detached review; google family).

Note: two seats now share glm-5.3 as fallback with the interactive primary. That is deliberate:
fallbacks fire only when a primary is down; interactive is manually switched by the operator,
and the registry validator (family distinctness, non-premium) passes.
