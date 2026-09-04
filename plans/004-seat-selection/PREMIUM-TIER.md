DevPass does not publish the list directly; the following is inferred from the https://devpass.llmgateway.io/pricing documentation which defines premium models as those priced at $5+ per million input tokens or $15+ per million output tokens, applied to the prices from the llmgateway API.

## Premium
- claude-fable-5: API price P=10.0e-6, C=50.0e-6 (exceeds threshold).
- claude-fable-5-1: API price P=10.0e-6, C=50.0e-6 (exceeds threshold).
- claude-opus-4-6: API price P=5.0e-6, C=25.0e-6 (exceeds threshold).
- claude-opus-4-7: API price P=5.0e-6, C=25.0e-6 (exceeds threshold).
- claude-opus-4-8: API price P=5.0e-6, C=25.0e-6 (exceeds threshold).
- claude-opus-5: API price P=5.0e-6, C=25.0e-6 (exceeds threshold).
- claude-sonnet-4-5: API price P=3.0e-6, C=15.0e-6 (exceeds threshold).
- claude-sonnet-4-6: API price P=3.0e-6, C=15.0e-6 (exceeds threshold), confirmed by journal 402.
- gpt-5.4: API price P=3.0e-6, C=15.0e-6 (exceeds threshold), confirmed by journal 402.
- gpt-5.5: API price P=5.0e-6, C=30.0e-6 (exceeds threshold).
- gpt-5.6-sol: API price P=5.0e-6, C=30.0e-6 (exceeds threshold).
- kimi-k3-fast: API price P=5.0e-6, C=23.0e-6 (exceeds threshold).

## Standard
- claude-haiku-4-5: API price P=1.0e-6, C=5.0e-6 (below threshold).
- claude-sonnet-5: API price P=2.0e-6, C=10.0e-6 (below threshold).
- deepseek-v4-flash: API price P=0.0e-6, C=0.0e-6 (below threshold).
- deepseek-v4-pro: API price P=0.0e-6, C=1.0e-6 (below threshold).
- gemini-3.1-pro-preview: API price P=2.0e-6, C=12.0e-6 (below threshold).
- gemini-3.7-flash: API price P=1.0e-6, C=4.0e-6 (below threshold).
- gemini-3.8-flash: API price P=1.0e-6, C=4.0e-6 (below threshold).
- glm-5.3: API price P=1.0e-6, C=4.0e-6 (below threshold).
- glm-5.3-flash: API price P=0.0e-6, C=0.0e-6 (below threshold).
- gpt-5.3-codex: API price P=2.0e-6, C=14.0e-6 (below threshold).
- gpt-5.4-mini: API price P=1.0e-6, C=5.0e-6 (below threshold).
- gpt-5.4-nano: API price P=0.0e-6, C=1.0e-6 (below threshold).
- gpt-5.6-luna: API price P=0.0e-6, C=1.0e-6 (below threshold).
- gpt-5.6-terra: API price P=2.0e-6, C=12.0e-6 (below threshold).
- gpt-oss-120b: API price P=0.0e-6, C=0.0e-6 (below threshold).
- grok-4-5: API price P=2.0e-6, C=6.0e-6 (below threshold).
- grok-4-6: API price P=2.0e-6, C=6.0e-6 (below threshold).
- grok-build-0-1: API price P=1.0e-6, C=2.0e-6 (below threshold).
- kimi-k3: API price P=3.0e-6, C=14.0e-6 (below threshold).
- mimo-v2.5-pro: API price P=0.0e-6, C=1.0e-6 (below threshold).
- minimax-m3: API price P=0.0e-6, C=1.0e-6 (below threshold).
- muse-spark-1.3: API price P=1.0e-6, C=4.0e-6 (below threshold).
- muse-spark-1.3-contributor: API price P=0.0e-6, C=0.0e-6 (below threshold).
- seed-1-8-251228: API price P=0.0e-6, C=2.0e-6 (below threshold).

## Unknown
(None)

## Method
Queried the llmgateway.io models API to extract per-token prices for each candidate. Cross-referenced the pricing information with the DevPass website documentation which explicitly categorizes models as premium if they are priced at $5+ per million input tokens or $15+ per million output tokens. Correlated these findings with journaled HTTP 402 errors to confirm `claude-sonnet-4-6` and `gpt-5.4` hit the allowance limit. `gpt-5.6-sol` exceeded the threshold and is classified as premium; its normal response at 07:25 implies a Reset Pass was used between 07:17 and 07:25.

## Sources
- `/usr/local/bin/devpass-code`: binary checked via grep, contained no explicit tier lists, but pointed to API functionality.
- `https://devpass.dev/pricing`: Webfetch failed (NXDOMAIN / Transport error).
- `https://devpass.ai/pricing`: Webfetch failed (NXDOMAIN / Transport error).
- `https://www.devpass.io/pricing`: Webfetch failed (NXDOMAIN / Transport error).
- `https://devpass.sh/pricing`: Webfetch failed (NXDOMAIN / Transport error).
- `https://llmgateway.io/pricing`: Successfully fetched, linked to the DevPass website and verified models list at API level.
- `https://devpass.llmgateway.io/pricing`: Successfully fetched, explicitly stated the premium threshold ("Frontier fair-use covers premium models — any model priced at $5+ per million input tokens or $15+ per million output tokens").
- `https://api.llmgateway.io/v1/models`: Used to retrieve the actual per-token prices for all candidate models using the local `auth.json` API key.
- `/root/.system/runs/*/phase-*/attempt-*/try-*/transcript.jsonl`: Local journals parsed to identify specific models that triggered the 402 "weekly allowance" billing error (found gpt-5.4 and claude-sonnet-4-6).

## Open questions
- Since `gpt-5.6-sol` is classified as Premium by pricing, its normal response at 07:25 confirms an allowance reset occurred. Could its response normally without an active allowance indicate a provider-side delay in enforcing limits, or was a manual Reset Pass definitively redeemed?
