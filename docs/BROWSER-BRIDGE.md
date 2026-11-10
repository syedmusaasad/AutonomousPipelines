# Browser bridge

Some sites refuse cloud browsers or need your login (Glassdoor, Blind). The browser
bridge lets pipeline workers on the cloud host read those pages through a Chrome on
your own Mac, where you are signed in and on your home network.

```
worker on cloud host                      your Mac
pipeline browser fetch <url>  ──►  127.0.0.1:19222 ══ SSH reverse tunnel ══►  127.0.0.1:9222
                                                                              bridge Chrome (own profile)
```

## What it does and does not do

- Opens the URL in a **background tab** of the bridge Chrome, waits for it to load,
  returns the visible text (optionally the links), and closes the tab.
- Only domains in `browser.allow` (roles/registry.json) can be opened. Anything
  else is refused.
- Only roles with the `browser` tool grant can use it: researcher, lane-worker,
  fast-worker. Your own shell can always use it.
- One page at a time, at least `min_interval_s` (8 s, plus jitter) apart per site,
  and at most `daily_max` (150) pages per site per day. This keeps your account
  looking like the person it is; raise the numbers at your own risk.
- It does not hide automation and does not answer CAPTCHAs. When a site shows a bot
  check or its sign-in page, the command exits 3 and tells the worker to stop and
  report it. You clear it once in the bridge Chrome window.
- The bridge Chrome uses its **own profile**. It holds only the logins you create in
  that window, not your everyday Chrome's. (Chrome also refuses remote debugging on
  the default profile.)
- Nothing listens on the network. Chrome's debugging port is bound to the Mac's
  loopback, the cloud end is bound to the cloud host's loopback, and SSH connects
  them. Anyone with a shell on the cloud host as your user can drive that browser
  profile, so treat it like your SSH access.

## Set up the Mac (once)

You need key-based SSH from the Mac to the cloud host (the same login you already use).

```sh
mkdir -p ~/.autonomous-pipelines
curl -fsSL https://raw.githubusercontent.com/syedmusaasad/AutonomousPipelines/main/mac/browser-bridge.sh \
  -o ~/.autonomous-pipelines/browser-bridge.sh
bash ~/.autonomous-pipelines/browser-bridge.sh install <user@cloud-host>
bash ~/.autonomous-pipelines/browser-bridge.sh open      # then sign in to Glassdoor and Blind in that window
```

`install` adds a launchd agent that starts at login and keeps both the bridge Chrome
and the tunnel running. Other commands: `status`, `open [url]`, `stop`, `start`,
`uninstall`. Logs: `~/.autonomous-pipelines/bridge.log`.

If your SSH key has a passphrase, add it to the Keychain so launchd can use it:
`ssh-add --apple-use-keychain ~/.ssh/<key>` and `UseKeychain yes` / `AddKeysToAgent yes`
in `~/.ssh/config`.

After a sleep or network change the agent reconnects by itself. If the cloud host still
holds the old tunnel's port, the script clears it first, but only when that port is
held by an SSH process and no browser answers on it.

The bridge is down while the Mac sleeps or is offline. Workers then get exit 3 with
"browser bridge is not reachable" and do not fetch anything.

## Use it on the cloud host

```sh
pipeline browser status
pipeline browser fetch "https://www.glassdoor.com/Reviews/<company>-Reviews-E<id>.htm" --scroll 3 --links
pipeline browser fetch <url> --out page.txt        # write to a file, print a one-line summary
pipeline browser fetch <url> --json                # title, url, text, links, status
```

| Exit | Meaning |
|---|---|
| 0 | page read |
| 2 | refused: site not allowlisted, role has no grant, or daily cap reached |
| 3 | you must act: bridge down, bot check, or sign-in page |
| 1 | other error |

Options: `--wait S` (settle time after load, default 3), `--scroll N` (page-downs for
lazy content), `--max-chars N` (default 40000), `--timeout S` (default 45).

## Configure

`roles/registry.json`:

```json
"browser": {
  "endpoint": "http://127.0.0.1:19222",
  "allow": ["glassdoor.com", "teamblind.com"],
  "min_interval_s": 8,
  "daily_max": 150
}
```

Add a site by adding its bare domain to `allow` (subdomains are included). The
endpoint must be loopback. `PIPELINE_BROWSER_ENDPOINT` overrides it for one command.

## Troubleshooting

- `pipeline browser status` fails: run `browser-bridge.sh status` on the Mac. "tunnel
  DOWN" with Chrome up usually means SSH cannot log in without a prompt, or an old
  tunnel still holds the port on the cloud host (it clears within a minute or two).
- Exit 3 with a bot check: `browser-bridge.sh open <url>` on the Mac, complete the
  check once, and retry later. Repeated checks mean the pace is too fast for that site.
- The bridge Chrome keeps reopening: that is the agent doing its job. `browser-bridge.sh
  stop` pauses it.
