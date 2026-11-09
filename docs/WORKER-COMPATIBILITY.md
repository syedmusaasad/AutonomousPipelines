# Worker CLI compatibility

Date: 2026-11-07. Installed: Claude Code 2.1.319, codex-cli 0.167.0, agy 1.5.3.

DevPass Code is retired. A seat's model family picks its CLI (`clis` in
roles/registry.json): anthropic seats run on Claude Code, openai seats on Codex, google
seats on Antigravity (`agy`).

## Claude Code seats (`claude -p`)

```
claude -p --output-format stream-json --verbose
  --agent pl-<role> --model <model> --effort <effort>
  --permission-mode dontAsk --allowed-tools <grants> --disallowed-tools <denies>
  --strict-mcp-config --no-session-persistence --exclude-dynamic-system-prompt-sections
```

- The brief is piped on stdin. `--allowed-tools`, `--disallowed-tools` and
  `--mcp-config` take variadic values, so a prompt passed as a trailing argument
  gets read as one of their values. With the prompt on stdin, nothing follows them.
- `stream-json` writes events as they happen, so the transcript's mtime stays a live
  liveness signal for the sentry. Plain `-p` text output writes nothing until the end.
- `dontAsk` refuses anything outside the role's grants instead of prompting. Verified
  as root: allowed `Write` and `Bash` work outside the cwd; a `--disallowed-tools`
  rule with spaces inside a comma-separated list is refused
  (`system/permission_denied`).
- Agent files (`~/.claude/agents/pl-<role>.md`) carry the contract and role prompt;
  `pipeline render-agents` writes them, and the drift guard diffs them.
- Tokens and cost come from the final `result` event. A `rate_limit_event` with
  `status: "rejected"` is a subscription usage limit; the engine stops the run as
  `usage_limit` and records `resetsAt`.

### Start-up speed

Measured on this host with `claude-haiku-5-5` and a one-line prompt, 2-3 runs each:

| configuration | wall |
|---|---|
| `claude -p` defaults | 2.41 s |
| `--strict-mcp-config` only | 2.36 s |
| + `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` + `--no-session-persistence` | 1.51 s |

The API time for that prompt was about 1.0 s, so a default headless launch spends about
1.4 s on its own start-up. The non-essential-traffic switch removes most of it.
Workers also set `ENABLE_CLAUDEAI_MCP_SERVERS=false`, so the operator's claude.ai
connectors (mail, drive, calendar) are not loaded into every worker's tool list, and
`DISABLE_AUTOUPDATER=1`.
`--exclude-dynamic-system-prompt-sections` moves per-machine sections out of the
system prompt, so dispatches share one cached prompt prefix.

`--bare` would skip more start-up work, but it reads only `ANTHROPIC_API_KEY`, never
the subscription's OAuth login, so it is not used.

## Codex seats (`codex exec`)

```
codex exec --json --model gpt-6.1-sol -c model_reasoning_effort="high"
  --sandbox read-only --skip-git-repo-check --ephemeral --cd <cwd> -o <dir>/last-message.md -
```

- Codex has no agent files: the contract and role prompt lead the stdin prompt,
  followed by the brief.
- Unsealed seats (fast-worker) run with `--dangerously-bypass-approvals-and-sandbox`
  instead of `--sandbox read-only`. Codex's `workspace-write` sandbox makes `.git`
  read-only, so `git commit` failed with an `index.lock` error when tested. External
  posting is refused by the generated execpolicy file `~/.codex/rules/pipeline.rules`
  (`prefix_rule ... decision = "forbidden"`). Verified: a forbidden command was
  rejected before execution even with the sandbox bypassed, and `codex execpolicy
  check` marks `gh pr create` and `curl -X POST` forbidden while `git commit` passes.
  The rules file is global, so it also applies to Codex sessions you start yourself.
- The reviewer is sealed in a read-only sandbox (verified with `codex sandbox`: reads
  work, writes fail with "Read-only file system"). The engine saves its final
  message as `$REVIEW_OUT/review.md`, after deleting any earlier record, so only that
  dispatch's answer counts.
- Tokens come from `turn.completed` events. Codex reports no cost. An `error` or
  `turn.failed` event that names a usage or rate limit counts as a usage limit.

## Antigravity seats (`agy -p`)

```
agy --output-format stream-json --model gemini-3.8-flash --effort <effort>
  --dangerously-skip-permissions --disable-slash-commands
  --settings '{"permissions":{"deny":["command(gh pr comment)", ...]}}' -p=<prompt>
```

- The prompt (contract + role prompt + brief) is attached to `-p=`. agy does not read a
  prompt from stdin, and a bare `-p` swallows the next flag as its prompt. A prompt over
  100 KB is replaced with a pointer to the saved `stdin.md`.
- Headless agy cannot prompt and refuses any tool without an allow rule, so workers
  run with `--dangerously-skip-permissions`; explicit deny rules still apply under it.
  Verified: `command(touch)` refused `touch` ("Matches user-configured deny rule").
- `--model gemini-3.8-flash --effort medium` is accepted; `agy models` lists the same
  model with the effort in its ID (`gemini-3.8-flash-medium`).
- Tokens come from the final `result` event (`output_tokens` include
  `thinking_tokens`). agy reports no cost. A result whose status is not `SUCCESS`
  and names a quota or `RESOURCE_EXHAUSTED` counts as a usage limit. That shape is
  inferred, not yet observed.
- agy has no verified read-only mode, so sealed seats cannot run on it (the registry
  refuses one).

## Interactive seat

`pl` runs `claude --agent pl-interactive --model claude-fable-5-2 --effort high --settings
~/.claude/agents/pl-interactive.settings.json`. The settings use `defaultMode: dontAsk`
with the read-only Bash allowlist and deny Edit/Write/NotebookEdit and shell writes
(`git commit`, `git push`, `rm`, `mv`). Do not start it with
`--dangerously-skip-permissions`; that would override the seat guard. A SessionStart
hook (`plugin/session-env.sh`) exports `PIPELINE_CONVERSATION` to its shells.
