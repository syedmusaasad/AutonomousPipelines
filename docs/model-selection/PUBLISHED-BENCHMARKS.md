# Published Benchmarks: Model Selection Research

**Note**: This is a documentary research report. Research-worker tokens were spent to compile this, but **no evaluation-model tokens were spent** (0 new model tests run). No final winners or seat changes are established from this report alone.

## Premium Definition and Limitations
According to the official pricing page at `https://devpass.llmgateway.io/pricing` (retrieved Sept 5, 2026), the premium threshold is defined as follows:
> "Frontier fair-use covers premium models — any model priced at $5+ per million input tokens or $15+ per million output tokens."

Models exceeding this threshold draw from a limited weekly allowance and are ineligible for automated standard-tier seats (including `interactive`).

## Evidence of Quality, Speed, and Cost
Data retrieved from Artificial Analysis Leaderboard (`https://artificialanalysis.ai/leaderboards/models`) on Sept 5, 2026. Note that `livebench.ai`, `swebench.com`, and `lmarena.ai/leaderboard` were either payload-restricted (5MB limit) or required JavaScript to render results, preventing full automated extraction. RULER (`https://github.com/NVIDIA/RULER`) was fetched but lacked direct mappings for 2026 models like Gemini 3.1 Pro or Claude Sonnet 5.

| Model | Tier | Evidence/Score (Intelligence Index) | Cost/Task | Speed (Tokens/s) |
| :--- | :--- | :--- | :--- | :--- |
| **Claude Fable 5.1 (max)** | Premium | 57 | $6.12 | 69 |
| **Grok 4.6 (high)** | Standard | 51 | $1.25 | 65 |
| **Kimi K3 (max)** | Standard | 50 | $1.58 | 40 |
| **GLM-5.3 (max)** | Standard | 49 | $1.26 | 80 |
| **GPT-5.6 Terra (max)** | Standard | 47 | $0.81 | 111 |
| **GLM-5.3-Flash** | Standard | 46 | $0.18 | 48 |
| **Claude Sonnet 5 (max)** | Standard | 45 | $3.31 | 74 |
| **Gemini 3.7 Flash (high)** | Standard | 45 | $0.55 | 310 |
| **GPT-5.6 Luna (max)** | Standard | 43 | $0.10 | 135 |
| **DeepSeek V4 Pro (max)** | Standard | 42 | $0.33 | 70 |
| **DeepSeek V4 Flash** | Standard | 41 | $0.14 | 135 |
| **Gemini 3.1 Pro Preview** | Standard | 38* | -- | 116 |
| **MiniMax-M3** | Standard | 36 | $0.23 | 87 |
| **MiMo-V2.5-Pro** | Standard | 33 | $0.04 | 36 |

*(Scores with an asterisk are unverified/partial on the leaderboard).*

## Provisional Role-by-Role Shortlist (Standard-Tier Eligible)

### 1. Interactive
**Purpose**: Routing, reasoning, long-horizon planning, and clarity.
*   **GLM-5.3**: Score 49. Strong evidence of high intelligence among standard-tier open weights models. Nominated by the operator.
*   **Kimi K3**: Score 50. Leading intelligence index among open weights, but slower output speed (40 t/s).

### 2. Implementer
**Purpose**: Code changes, tests, precise ceremony.
*   **GPT-5.6 Terra**: Score 47. Strong capability and speed (111 t/s). Nominee `gpt-5.6-luna` scored lower (43), but Terra provides a better balance for high-effort coding.
*   **Claude Sonnet 5**: Score 45. Incumbent. Solid performance but higher cost ($3.31/task).

### 3. Fast-Worker & Lane-Worker
**Purpose**: Small, well-specified, cheap tasks (fetch, rename, fan-out).
*   **GPT-5.6 Luna**: Score 43, extremely fast (135 t/s), low cost ($0.10/task). Nominated by operator.
*   **DeepSeek V4 Flash**: Score 41, equal speed (135 t/s), $0.14/task. Excellent fallback.
*(Incumbent `claude-haiku-4-5` has insufficient evidence on the leaderboard to verify its quality relative to these).*

### 4. Researcher
**Purpose**: Reads, fetches, compares.
*   **Gemini 3.1 Pro Preview**: Incumbent. Moderate evidence (38 index), but fast (116 t/s).
*   **Claude Sonnet 5**: Strong intelligence (45), better suited for complex comparisons.

### 5. Document-Writer & Frontend-Worker
**Purpose**: Prose artifacts, browser-facing HTML/CSS/JS.
*   **GPT-5.6 Terra**: High intelligence (47), fast (111 t/s). Nominated for writer.
*   **Claude Sonnet 5**: Incumbent for both roles. Strong general-purpose capability (45).

### 6. Reviewers (A, B, C)
**Purpose**: Sealed review spanning multiple families.
*   **Reviewer A**: Claude Sonnet 5 (Incumbent) / DeepSeek V4 Pro (Nominee, Score 42).
*   **Reviewer B**: Grok 4.6 (Fallback incumbent, Score 51) / GPT-5.3 Codex.
*   **Reviewer C**: Kimi K3 (Score 50) / Gemini 3.1 Pro Preview.

## Unknown / Unmapped Roster
The following models are listed as candidates or incumbents but lack sufficient published evidence from reachable benchmarks. They are not silently ruled out but require local trial data:
*   `claude-haiku-4-5` (identity mapping to 'Claude 4.5 Haiku (Non-reasoning)' is unverified)
*   `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.4-nano`, `gpt-5.5`
*   `gpt-oss-120b`
*   `grok-build-0-1`
*   `muse-spark-1.3`, `muse-spark-1.3-contributor`
*   `seed-1-8-251228`
*   `gemini-3.8-flash` (Listed on AA but with no score/speed data)

## Remaining Local Compatibility Checks (Not Executed)
To finalize these shortlists into actual registry seats, the following trials must be executed via `pipeline trial`:
1.  **Tool Use & Exit Predicate Reliability**: Test if Kimi K3 and GLM-5.3 correctly respect JSON tool schemas and bash single-line execution constraints.
2.  **Latency & TTFT under Load**: Public latency does not guarantee DevPass gateway latency. Verify actual TTFT.
3.  **Context Degradation**: Test long-context recall (NIAH) up to 100k tokens for Interactive and Researcher roles.
4.  **Exact Ceremony**: Implementer candidates (Terra, Luna) must be tested against strict commit message constraints.