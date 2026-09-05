# Non-Premium Role-Model Recommendation

## Sources and Verification
Evidence was retrieved Sept 5, 2026, from:
1. `https://devpass.llmgateway.io/pricing`: Defines premium as $5+ per million input or $15+ per million output tokens.
2. `https://artificialanalysis.ai/leaderboards/models`: General intelligence index scores and speed/cost data.

**Crucial Limitation**: Task-specific evidence (e.g., LiveBench, SWE-Bench) could not be verified because the sources were inaccessible (JS/payload limits). General index scores alone cannot establish code/research/frontend suitability or guarantee optimality.

### Verified Intelligence Index (Max/High Effort)

| Model | Score | Speed | Cost/Task | Status |
| :--- | :--- | :--- | :--- | :--- |
| **Grok 4.6 (high)** | 51 | 65 t/s | $1.25 | Non-premium |
| **Kimi K3 (max)** | 50 | 40 t/s | $1.58 | Non-premium |
| **GLM-5.3 (max)** | 49 | 80 t/s | $1.26 | Non-premium |
| **GPT-5.6 Terra (max)** | 47 | 111 t/s | $0.81 | Non-premium |
| **Claude Sonnet 5 (max)** | 45 | 74 t/s | $3.31 | Non-premium |
| **GPT-5.6 Luna (max)** | 43 | 135 t/s | $0.10 | Non-premium |
| **DeepSeek V4 Pro (max)** | 42 | 70 t/s | $0.33 | Non-premium |
| **DeepSeek V4 Flash (max)** | 41 | 135 t/s | $0.14 | Non-premium |
| **Gemini 3.1 Pro Preview** | 38* (unverified) | 116 t/s | -- | Non-premium |
| **GPT-5.3 Codex (xhigh)** | 37* (unverified) | 134 t/s | -- | Non-premium |

*(Models like `claude-haiku-4-5` and `gemini-3.5-flash` are unmapped or lack scores).*

## Role Recommendations

Because task-specific metrics are completely unverified, most candidates are marked **unresolved**. Current registry settings are retained as provisional, not validated.

*   **Interactive (High Effort)**
    *   *Primary*: `glm-5.3` (GLM family).
    *   *Fallback*: `claude-sonnet-5` (Anthropic).
    *   *Comparison*: GLM-5.3 (max) scores 49 vs Grok 4.6 (high) at 51. While Grok is a serious non-premium interactive alternative, the operator explicitly nominated GLM-5.3. Evidence at high effort for GLM-5.3 and Sonnet 5 is completely absent (only max effort scores exist). Status: Unresolved, provisional.

*   **Implementer (High Effort)**
    *   *Primary*: `claude-sonnet-5`.
    *   *Fallback*: `gpt-5.6-luna`.
    *   *Comparison*: General index scores cannot establish coding suitability. Status: Unresolved, provisional.

*   **Fast-Worker (Low Effort)**
    *   *Primary*: `claude-haiku-4-5`.
    *   *Fallback*: `gemini-3.5-flash`.
    *   *Comparison*: Luna (max, 43) vs Haiku/DeepSeek Flash (max, 41). Haiku lacks any verified leaderboard evidence. We recommend fast-worker low per operator policy, but explicitly state that evidence at low effort is entirely absent for all candidates. Status: Unresolved, provisional.

*   **Lane-Worker (Medium Effort)**
    *   *Primary*: `claude-haiku-4-5`.
    *   *Fallback*: `deepseek-v4-flash`.
    *   *Comparison*: Luna vs Haiku/DeepSeek Flash. Medium effort evidence is absent. Status: Unresolved, provisional.

*   **Researcher (Medium Effort)**
    *   *Primary*: `gemini-3.1-pro-preview`.
    *   *Fallback*: `claude-sonnet-5`.
    *   *Comparison*: Research capabilities are unverified. Status: Unresolved, provisional.

*   **Document-Writer (Medium Effort)**
    *   *Primary*: `claude-sonnet-5`.
    *   *Fallback*: `gpt-5.6-luna`.
    *   *Comparison*: Prose capability cannot be validated by general intelligence index. Status: Unresolved, provisional.

*   **Frontend-Worker (High Effort)**
    *   *Primary*: `claude-sonnet-5`.
    *   *Fallback*: `gemini-3.1-pro-preview`.
    *   *Comparison*: Browser-facing tasks are unverified. Status: Unresolved, provisional.

*   **Reviewers A, B, C (High Effort)**
    *   *Reviewer A (Anthropic / DeepSeek)*: `claude-sonnet-5` / `deepseek-v4-pro`.
    *   *Reviewer B (OpenAI / xAI)*: `gpt-5.3-codex` / `grok-4-6`. Comparison: Luna (max, 43) vs Codex (xhigh, 37*). Luna indicates higher general capability, but coding review strength is unverified.
    *   *Reviewer C (Google / Moonshot)*: `gemini-3.1-pro-preview` / `kimi-k3`.
    *   *Status*: Unresolved, provisional. The reviewer set correctly spans more than three distinct known families (Anthropic, DeepSeek, OpenAI, xAI, Google, Moonshot).

## Conclusions & Next Steps
**No registry changes** are recommended, and **no empirical winners** are established by this documentary evidence. General indices do not guarantee optimality. Limited compatibility trials would still be required through `pipeline trial` before changing seats under existing policy.

**Minimal Next Steps**:
1. Run `pipeline trial` to capture latency, TTFT under load, context recall (NIAH), and exit predicate reliability.
