# Google Drive cloud tier

The cloud tier archives eligible GC artifacts to Google Drive before local
deletion. It does not replace the journal-aware 72-hour eligibility checks.

## One-time OAuth setup

Run this command on the MacBook (or another machine with a web browser), not
on the headless pipeline host:

```bash
rclone config create pipeline-drive drive --headless=false
```

When rclone opens the Google authorization page, sign in to the operator's
Google account and approve access. Copy the token/configuration text that
rclone requests, paste it back into the terminal on the pipeline host, and
finish the prompt. Confirm that the remote exists:

```bash
rclone listremotes
```

If the OAuth step has not been completed, this repository deliberately makes
no claim that Drive is configured; see `plans/009-gc/OAUTH-PENDING.md`.

## Preview and opt in

First inspect the proposed cloud archive without changing local files:

```bash
pipeline gc --cloud pipeline-drive --dry-run
```

The daily systemd service remains report-only by default. To opt into cloud
archive plus sweep, change its documented `ExecStart` to:

```ini
ExecStart=%h/.system/bin/pipeline gc --cloud pipeline-drive --sweep
Environment=PIPELINE_GC_SWEEP=1
```

Only enable this after OAuth and a successful dry run. The journal, registry,
STOPPED receipts, and plan files remain protected.
