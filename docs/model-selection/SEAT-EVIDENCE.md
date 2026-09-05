# Seat changes from published benchmarks (2026-09-05)

No model tokens were spent on selection. Two sources, fetched directly:

- **AA** = Artificial Analysis Intelligence Index / speed / latency / price,
  https://artificialanalysis.ai/leaderboards/models (retrieved 2026-09-05).
- **LB** = LiveBench 2026-06-25 release, https://livebench.ai/table_2026_06_25.csv
  (per-task scores; effort variants noted per model).

`*` = AA-computed score on partial data (their marker). All picks are standard-tier
per the DevPass $5/$15 rule (verified per-model pricing in registry `models`).

| Seat | Was | Now | Why (published numbers) |
|---|---|---|---|
| fast-worker | claude-haiku-4-5 @low | **gpt-5.6-luna @low** (fb: deepseek-v4-flash) | AA: Luna 26*–43 vs Haiku 22; faster (107 vs 100 t/s); 1M vs 200k ctx; LiveBench code_completion 87.0 (Luna max) vs Haiku 22 int. |
| lane-worker | claude-haiku-4-5 @med | **deepseek-v4-flash @med** (fb: gpt-5.6-luna) | AA: 41 vs 22; $0.14/task; TTFT 0.94s (fastest on board); 131 t/s. Volume seat: cheapest+fastest wins ties. |
| document-writer | claude-sonnet-5 @med | **gemini-3.8-flash @high** (fb: claude-sonnet-5) | AA: 47 vs 45; LB prose: summarize 87.5 vs 71.2, story 82.8 vs 62.3, paraphrase 80.0 vs 61.25. Best writer on the board at $0.74. |
| researcher | gemini-3.1-pro-preview @med | **gemini-3.8-flash @medium** (fb: claude-sonnet-5) | AA: 47 vs 37 (3.1 Pro); LB summarize 87.5 (best) — reading/digesting sources is the researcher's core task. |
| reviewer-a | claude-sonnet-5 @high | **gpt-5.6-terra @high** (fb: deepseek-v4-pro) | AA: 47 (next in line after operator rule: no reviewer may share the interactive model). OpenAI family, LB code_completion 80.4 / typos 86. |
| reviewer-b | gpt-5.3-codex @high | **grok-4-6 @high** (fb: gpt-5.3-codex) | AA: 51 — highest non-premium intelligence. TTFT 52.7s is fine for detached review. xai family. |
| reviewer-c | gemini-3.1-pro-preview @high | **kimi-k3 @high** (fb: gemini-3.8-flash) | AA: 50; LB: code_completion 82.6, paraphrase 74.4. moonshot family. |
| implementer | claude-sonnet-5 @high | **unchanged** | AA: 45; LB code_completion 78.3/codegen 83.1. Luna 43/87.0/78.9 and Terra 47*/80.4/76.1 are candidates, but AA intelligence is not coding-specific and Luna's LB lead is one task; not enough published evidence to displace a seat that has landed every commit so far. Codex-style gpt-5.2-codex scores LB code_completion 87.0 but is not confirmed on this gateway's roster. |
| interactive | glm-5.3 @high | **unchanged** | AA: 49 (operator's pick, confirmed 2nd-highest non-premium intelligence; Grok 4.6's 51 has 52s TTFT — unusable for interactive). |

Caveats, honestly: AA intelligence is a general index, not our workload; LB is one
release; per-effort LB rows are sparse (many `--`); and none of this measures our
exact EXIT-ceremony behavior. These are the best available published numbers, not
proof of optimality. The trials path remains for anyone we later want to displace.
