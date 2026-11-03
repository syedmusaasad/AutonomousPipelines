# Worker CLI compatibility

Date: 2026-11-03. Installed binary: `/usr/local/bin/devpass-code`.

## Fault

`pipeline/dispatch.py` built the worker argv with `--variant <effort>` and `--dir <cwd>`.
The installed `devpass-code run` rejects both:

```
ERRORS
  Unrecognized flag: --variant in command devpass-code run
  Unrecognized flag: --dir in command devpass-code run
```

Every worker launch exited 1 before any model step. Evidence:
`/root/.system/runs/q_20261103T183431_1ad345/phase-1/attempt-1/try-0/transcript.jsonl`.
Runs `q_20261103T183431_1ad345` and `q_20261103T184100_e847d3` burned their attempts
on it and stopped. Their history was not edited.

## Installed contract (`devpass-code run --help`)

```
USAGE
  devpass-code run [flags] [<message...>]
  --model, -m string   Model to use in the format provider/model#variant
  --agent string       Agent to use
  --format choice      Output format (choices: default, json)
  --file, -f string    File to attach to the message
  --auto               Auto-approve permissions that are not explicitly denied
```

There is no `--variant` and no `--dir`. Effort is the `#variant` suffix of `--model`.
The working directory is the process cwd.

## Prompt input

Documented input is the positional `[<message...>]` and `--file`. Neither the installed
help nor the read-only V2 docs fetched (`https://opencode.ai/v2/docs/cli/` and
`https://opencode.ai/v2/docs/cli/commands/`, whose `run` examples use a quoted message
and repeated `--file`) document stdin as prompt input. Stdin is therefore no longer
used: the worker gets stdin from `/dev/null`. The brief is attached with `--file` (the
saved `brief.md` in the dispatch out dir, absolute path) with a fixed short message,
which also avoids the per-argument size limit of a long brief passed as a message.

Not verified live: no worker was dispatched, per the brief. The live check is the next
engine dispatch; the argv matches the installed help exactly.

## Fix

`pipeline/dispatch.py`: `build_argv` and `model_arg`. Detached execution
(`start_new_session`), captured streams, role agents, quota and retry handling, and the
timeout kill-group path are unchanged.

Invocation produced for role `implementer`, effort `medium` (from the suite's fake CLI log):

```
devpass-code run --format json --agent pl-implementer \
  --model <provider>/<model>#medium --auto \
  --file <abs out_dir>/brief.md "Carry out the attached brief."
```

spawned with `cwd=<phase working directory>`, `stdin=DEVNULL`, stdout and stderr to
`transcript.jsonl`.

## Regression tests

- `tests/fake-devpass-code` now behaves like the current CLI: exits 1 with
  `Unrecognized flag` on `--variant` or `--dir`, reads the brief from `--file`, uses the
  process cwd, and splits `#variant` from `--model`.
- `dispatch_argv_uses_model_variant_and_attached_brief`
- `dispatch_runs_current_cli_with_effort_in_model_and_process_cwd`
- `fake_current_cli_rejects_obsolete_flags`
- Existing `engine_passes_effort_to_worker` and `engine_runs_fallback_at_pinned_effort`
  pass unchanged.

## Verification result

Full suite, run once: `264/264 passed, 0 failed, 59.1s; ratchet floor 260`, ratchet
raised to 263.

## Not changed

Model seats, permissions, credentials, stopped-run history, and the requested later
board (interactive Sol to Kimi, reviewer-a Kimi to Sol, reviewer-b Grok4.7 to
DeepSeekV4Pro), which is not authorized here. No push.
