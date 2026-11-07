# Worker CLI compatibility

Date: 2026-11-07. Installed: Claude Code 2.1.319, codex-cli 0.167.0.

DevPass Code is retired. A seat's model family picks its CLI (`clis` in
roles/registry.json): anthropic seats run on Claude Code, openai seats on Codex.

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

## Codex seat (`codex exec`)

```
codex exec --json --model gpt-6.1-sol -c model_reasoning_effort="high"
  --sandbox read-only --skip-git-repo-check --ephemeral --cd <cwd> -o <dir>/last-message.md -
```

- Codex has no agent files: the contract and role prompt lead the stdin prompt,
  followed by the brief.
- The reviewer is sealed in a read-only sandbox (verified with `codex sandbox`: reads
  work, writes fail with "Read-only file system"). The engine saves its final
  message as `$REVIEW_OUT/review.md`, after deleting any earlier record, so only that
  dispatch's answer counts.
- Tokens come from `turn.completed` events. Codex reports no cost. An `error` or
  `turn.failed` event that names a usage or rate limit counts as a usage limit.

## Interactive seat

`pl` runs `claude --agent pl-interactive --model claude-opus-5-5 --effort high --settings
~/.claude/agents/pl-interactive.settings.json`. The settings use `defaultMode: auto`
with the read-only Bash allowlist auto-approved and deny rules on Edit/Write/NotebookEdit
and shell writes (`git commit`, `git push`, `rm`, `mv`). Do not start it with
`--dangerously-skip-permissions`; that would override the seat guard. A SessionStart
hook (`plugin/session-env.sh`) exports `PIPELINE_CONVERSATION` to its shells.
