# Candidates for Seat Selection

## interactive
Incumbent: glm-5.3
- **glm-5.3** (wall: 16.35s, cost: $0.008713, edit_ok: True): included as per rules.
- **claude-sonnet-5** (wall: 21.26s, cost: $0.077439, edit_ok: True): included as per rules.
- **gpt-5.3-codex** (wall: 11.04s, cost: $0.028028, edit_ok: True): included as per rules.
- **gemini-3.1-pro-preview** (wall: 20.55s, cost: $0.06689, edit_ok: True): included as per rules.
- **deepseek-v4-pro** (wall: 24.62s, cost: $0.00312, edit_ok: True): included as per rules.
- **kimi-k3** (wall: 13.49s, cost: $0.032589, edit_ok: False): included as per rules.
- **grok-4-6** (wall: 15.89s, cost: $0.056052, edit_ok: True): included as per rules.
- **minimax-m3** (wall: 6.9s, cost: $0.009775, edit_ok: True): included as per rules.
- **seed-1-8-251228** (wall: 12.63s, cost: $0.008793, edit_ok: True): included as per rules.

## implementer
Incumbent: claude-sonnet-5
- **glm-5.3-flash** (wall: 20.42s, cost: $0.002302, edit_ok: True): included as per rules.
- **gpt-5.6-luna** (wall: 25.16s, cost: $0.003048, edit_ok: True): included as per rules.
- **claude-sonnet-5** (wall: 21.26s, cost: $0.077439, edit_ok: True): included as per rules.
- **gpt-oss-120b** (wall: 11.78s, cost: $0.000944, edit_ok: True): included as per rules.
- **deepseek-v4-flash** (wall: 12.14s, cost: $0.00101, edit_ok: True): included as per rules.

## fast-worker
Incumbent: claude-haiku-4-5
- **claude-haiku-4-5** (wall: 9.14s, cost: $0.031047, edit_ok: True): included as per rules.
- **glm-5.3-flash** (wall: 20.42s, cost: $0.002302, edit_ok: True): included as per rules.
- **gpt-5.6-luna** (wall: 25.16s, cost: $0.003048, edit_ok: True): included as per rules.
- **gpt-oss-120b** (wall: 11.78s, cost: $0.000944, edit_ok: True): included as per rules.
- **deepseek-v4-flash** (wall: 12.14s, cost: $0.00101, edit_ok: True): included as per rules.

## lane-worker
Incumbent: claude-haiku-4-5
- **claude-haiku-4-5** (wall: 9.14s, cost: $0.031047, edit_ok: True): included as per rules.
- **glm-5.3-flash** (wall: 20.42s, cost: $0.002302, edit_ok: True): included as per rules.
- **gpt-5.6-luna** (wall: 25.16s, cost: $0.003048, edit_ok: True): included as per rules.
- **gpt-oss-120b** (wall: 11.78s, cost: $0.000944, edit_ok: True): included as per rules.
- **deepseek-v4-flash** (wall: 12.14s, cost: $0.00101, edit_ok: True): included as per rules.

## researcher
Incumbent: gemini-3.1-pro-preview
- **glm-5.3-flash** (wall: 20.42s, cost: $0.002302, edit_ok: True): included as per rules.
- **gemini-3.1-pro-preview** (wall: 20.55s, cost: $0.06689, edit_ok: True): included as per rules.
- **gpt-oss-120b** (wall: 11.78s, cost: $0.000944, edit_ok: True): included as per rules.
- **deepseek-v4-flash** (wall: 12.14s, cost: $0.00101, edit_ok: True): included as per rules.

## document-writer
Incumbent: claude-sonnet-5
- **glm-5.3-flash** (wall: 20.42s, cost: $0.002302, edit_ok: True): included as per rules.
- **gpt-5.6-terra** (wall: 18.0s, cost: $0.029513, edit_ok: True): included as per rules.
- **claude-sonnet-5** (wall: 21.26s, cost: $0.077439, edit_ok: True): included as per rules.
- **gpt-oss-120b** (wall: 11.78s, cost: $0.000944, edit_ok: True): included as per rules.
- **deepseek-v4-flash** (wall: 12.14s, cost: $0.00101, edit_ok: True): included as per rules.

## frontend-worker
Incumbent: claude-sonnet-5
- **minimax-m3** (wall: 6.9s, cost: $0.009775, edit_ok: True): included as per rules.
- **glm-5.3-flash** (wall: 20.42s, cost: $0.002302, edit_ok: True): included as per rules.
- **claude-sonnet-5** (wall: 21.26s, cost: $0.077439, edit_ok: True): included as per rules.
- **gpt-oss-120b** (wall: 11.78s, cost: $0.000944, edit_ok: True): included as per rules.
- **deepseek-v4-flash** (wall: 12.14s, cost: $0.00101, edit_ok: True): included as per rules.

## reviewer
Incumbents: claude-sonnet-5, gpt-5.3-codex, gemini-3.1-pro-preview
- **glm-5.3-flash** (wall: 20.42s, cost: $0.002302, edit_ok: True): included as per rules.
- **deepseek-v4-pro** (wall: 24.62s, cost: $0.00312, edit_ok: True): included as per rules.
- **claude-sonnet-5** (wall: 21.26s, cost: $0.077439, edit_ok: True): included as per rules.

## Excluded nominees
DECISION no-premium-seats: the operator's policy prescribes no premium-tier model in any
seat, so premium-tagged nominees never enter the candidate pool, regardless of measured
quality, wall or cost — excluded by policy, not by score.
- **claude-fable-5-1** (interactive nominee/incumbent): premium ($10.0e-6/$50.0e-6);
  glm-5.3 is the interim primary per DECISION no-premium-seats.
- **claude-opus-4-8** (researcher nominee): premium ($5.0e-6/$25.0e-6).
- **claude-opus-5** (reviewer nominee): premium ($5.0e-6/$25.0e-6).
- **gpt-5.6-sol** (reviewer nominee): premium ($5.0e-6/$30.0e-6).

## Open questions
Fable (claude-fable-5, claude-fable-5-1), Opus (claude-opus-4-6/4-7/4-8/5), the
Sonnet-4.x line (claude-sonnet-4-5, claude-sonnet-4-6), and GPT-5.4/5.5/5.6-sol were
excluded from every candidate pool by policy (DECISION no-premium-seats,
premium.json), not by score. Nothing in this document implies those models underperform
the seated arms; they were priced at or above the $5/M input or $15/M output threshold
and never entered a trial.
