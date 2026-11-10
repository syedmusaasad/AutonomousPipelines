#!/bin/bash
# AutonomousPipelines browser bridge, macOS side.
#
# Keeps two things running on this Mac, through a launchd agent:
#   1. a separate Chrome instance with its own profile and remote debugging on
#      127.0.0.1:9222 (Chrome refuses remote debugging on your default profile);
#   2. a reverse SSH tunnel so the pipeline host reaches that port on ITS localhost.
# Nothing listens on the network: both ends are loopback, the link is SSH.
#
# Usage (see docs/BROWSER-BRIDGE.md):
#   browser-bridge.sh install <user@host> [--ssh-port N] [--remote-port N]
#   browser-bridge.sh status | open | stop | start | uninstall
set -euo pipefail

DIR="$HOME/.autonomous-pipelines"
SELF="$DIR/browser-bridge.sh"
CONF="$DIR/bridge.conf"
PROFILE="$DIR/chrome-profile"
LOG="$DIR/bridge.log"
LABEL="com.autonomouspipelines.browser-bridge"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
CHROME_APP="/Applications/Google Chrome.app"
LOCAL_PORT=9222
DEST=""; SSH_PORT=22; REMOTE_PORT=19222

die() { echo "browser-bridge: $*" >&2; exit 1; }
load_conf() { [ -f "$CONF" ] || die "not installed (run: browser-bridge.sh install <user@host>)"; . "$CONF"; }
chrome_up() { curl -fsS -m 3 "http://127.0.0.1:$LOCAL_PORT/json/version" >/dev/null 2>&1; }

start_chrome() {
  # -g: do not bring it to the front; -n: a new instance beside your normal Chrome.
  open -gna "$CHROME_APP" --args --user-data-dir="$PROFILE" --remote-debugging-port="$LOCAL_PORT" \
    --no-first-run --no-default-browser-check
  for _ in 1 2 3 4 5 6 7 8 9 10; do chrome_up && return 0; sleep 1; done
  return 1
}

ssh_opts() {
  echo -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
       -o StrictHostKeyChecking=accept-new -p "$SSH_PORT"
}

# After a sleep or network change, the pipeline host can keep the old tunnel's listener
# for a long time, which blocks the new one. Clear it only when it is a leftover: the
# port is held by an sshd process and no browser answers on it.
STALE_CHECK='p=__PORT__
if ss -Hltn "sport = :$p" 2>/dev/null | grep -q . && ! curl -fsS -m 4 "http://127.0.0.1:$p/json/version" >/dev/null 2>&1; then
  for pid in $(ss -Hltnp "sport = :$p" 2>/dev/null | grep -o "pid=[0-9]*" | cut -d= -f2 | sort -u); do
    case "$(cat /proc/$pid/comm 2>/dev/null)" in sshd*) kill "$pid" && echo "cleared stale tunnel (pid $pid)";; esac
  done
fi'
free_stale_port() {
  # shellcheck disable=SC2046,SC2029
  ssh $(ssh_opts) -o ExitOnForwardFailure=no -o ConnectTimeout=10 "$DEST" \
    "$(printf '%s' "$STALE_CHECK" | sed "s/__PORT__/$REMOTE_PORT/")" 2>/dev/null || true
}

cmd_run() {  # launchd runs this; it never returns
  load_conf
  SSH_PID=""
  trap '[ -n "$SSH_PID" ] && kill "$SSH_PID" 2>/dev/null; exit 0' TERM INT
  while true; do
    chrome_up || start_chrome || echo "$(date '+%F %T') chrome did not start" >&2
    free_stale_port
    # shellcheck disable=SC2046
    ssh -N $(ssh_opts) -R "127.0.0.1:$REMOTE_PORT:127.0.0.1:$LOCAL_PORT" "$DEST" &
    SSH_PID=$!
    echo "$(date '+%F %T') tunnel up (pid $SSH_PID) -> $DEST:$REMOTE_PORT"
    while kill -0 "$SSH_PID" 2>/dev/null; do
      chrome_up || start_chrome || true
      sleep 20
    done
    echo "$(date '+%F %T') tunnel down; retrying in 5s" >&2
    sleep 5
  done
}

cmd_install() {
  [ $# -ge 1 ] || die "usage: browser-bridge.sh install <user@host> [--ssh-port N] [--remote-port N]"
  DEST="$1"; shift
  while [ $# -gt 0 ]; do
    case "$1" in
      --ssh-port) SSH_PORT="$2"; shift 2;;
      --remote-port) REMOTE_PORT="$2"; shift 2;;
      *) die "unknown option $1";;
    esac
  done
  [ "$(uname)" = "Darwin" ] || die "this script is for macOS"
  [ -d "$CHROME_APP" ] || die "Google Chrome is not installed at $CHROME_APP"
  [ -f "$0" ] || die "run it from a file: curl -fsSL <raw url> -o \"$SELF\" && bash \"$SELF\" install $DEST"
  # shellcheck disable=SC2046
  ssh $(ssh_opts) -o ConnectTimeout=10 "$DEST" true 2>/dev/null \
    || die "cannot SSH to $DEST without a password prompt. Set up key login first (ssh-copy-id $DEST) and make sure the key is in your agent or Keychain."
  mkdir -p "$DIR" "$PROFILE" "$HOME/Library/LaunchAgents"
  [ "$0" -ef "$SELF" ] || cp "$0" "$SELF"
  chmod 700 "$DIR"; chmod 755 "$SELF"
  printf 'DEST=%q\nSSH_PORT=%q\nREMOTE_PORT=%q\n' "$DEST" "$SSH_PORT" "$REMOTE_PORT" > "$CONF"
  cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$SELF</string><string>run</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict></plist>
PLIST
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "installed. Waiting for the bridge..."
  sleep 6
  cmd_status || true
  echo
  echo "Next: run '$SELF open', then sign in to each site (Glassdoor, Blind) once in that Chrome window."
  echo "Leave that Chrome instance running; you can minimise it. It restarts at login."
}

cmd_status() {
  load_conf
  if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then echo "launchd agent: loaded"; else echo "launchd agent: NOT loaded"; fi
  if chrome_up; then echo "bridge Chrome:  up on 127.0.0.1:$LOCAL_PORT"; else echo "bridge Chrome:  DOWN"; fi
  # shellcheck disable=SC2046
  if ssh $(ssh_opts) -o ExitOnForwardFailure=no -o ConnectTimeout=10 "$DEST" \
       "curl -fsS -m 5 http://127.0.0.1:$REMOTE_PORT/json/version >/dev/null" 2>/dev/null; then
    echo "tunnel:         up ($DEST sees the browser on its 127.0.0.1:$REMOTE_PORT)"
  else
    echo "tunnel:         DOWN (see $LOG)"; return 1
  fi
}

cmd_open() {  # bring the bridge Chrome to the front, to sign in or clear a bot check
  chrome_up || start_chrome || die "could not start the bridge Chrome"
  open -na "$CHROME_APP" --args --user-data-dir="$PROFILE" ${1:+"$1"}
}

cmd_stop() { launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true; pkill -f "ssh -N .*-R 127.0.0.1:[0-9]*:127.0.0.1:$LOCAL_PORT" 2>/dev/null || true; echo "stopped (bridge Chrome left open; quit it yourself if you want)"; }
cmd_start() { [ -f "$PLIST" ] || die "not installed"; launchctl bootstrap "gui/$(id -u)" "$PLIST"; echo "started"; }
cmd_uninstall() { cmd_stop; rm -f "$PLIST" "$CONF" "$LOG"; echo "removed the agent. Your bridge profile (logins) is still at $PROFILE; delete it to sign out everywhere."; }

case "${1:-}" in
  install) shift; cmd_install "$@";;
  run) cmd_run;;
  status) cmd_status;;
  open) shift; cmd_open "${1:-}";;
  stop) cmd_stop;;
  start) cmd_start;;
  uninstall) cmd_uninstall;;
  *) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 2;;
esac
