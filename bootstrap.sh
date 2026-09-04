#!/usr/bin/env bash
# Bootstrap installer. STICKY: picks the most reliable local disk once, records it in
# ~/.system/estate, and refuses to repoint on later runs unless --repoint is given
# by a human. Everything (state, launchers, new project dirs) lives under the estate.
set -euo pipefail
REPO="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
SYS="$HOME/.system"
POINTER="$SYS/estate"
REPOINT=0; ESTATE_ARG=""; SENTRY=1
for a in "$@"; do case "$a" in --repoint) REPOINT=1;; --estate=*) ESTATE_ARG="${a#--estate=}";; --no-sentry) SENTRY=0;; esac; done

pick_disk() {
  # Most reliable local disk: a real block device mount (not tmpfs/overlay/nfs/fuse),
  # with the most free space, preferring the one holding $HOME.
  local best="" bestfree=0
  while read -r src mnt type; do
    case "$type" in tmpfs|overlay|nfs*|fuse*|squashfs|devtmpfs|proc|sysfs|cgroup*) continue;; esac
    case "$src" in /dev/*) ;; *) continue;; esac
    local free; free=$(df -Pk "$mnt" 2>/dev/null | awk 'NR==2{print $4}')
    [ -z "$free" ] && continue
    if [ "$free" -gt "$bestfree" ] || { [ "$free" -eq "$bestfree" ] && [ "$mnt" = "/" ]; }; then best="$mnt"; bestfree="$free"; fi
  done < <(awk '{print $1, $2, $3}' /proc/mounts)
  [ -z "$best" ] && best="/"
  echo "$best"
}

mkdir -p "$SYS"
if [ -s "$POINTER" ] && [ "$REPOINT" = 0 ]; then
  ESTATE="$(cat "$POINTER")"
  if [ -n "$ESTATE_ARG" ] && [ "$ESTATE_ARG" != "$ESTATE" ]; then
    echo "bootstrap: estate is sticky at $ESTATE; refusing to repoint to $ESTATE_ARG (pass --repoint to override)" >&2
    exit 3
  fi
  echo "bootstrap: estate sticky at $ESTATE"
else
  if [ -n "$ESTATE_ARG" ]; then ESTATE="$ESTATE_ARG"
  else
    DISK="$(pick_disk)"
    case "$HOME" in "$DISK"*) ESTATE="$SYS";; *) ESTATE="$DISK/pipeline-estate";; esac
    [ "$DISK" = "/" ] && ESTATE="$SYS"
  fi
  mkdir -p "$ESTATE"
  printf '%s\n' "$ESTATE" > "$POINTER"
  echo "bootstrap: estate set to $ESTATE (recorded in $POINTER)"
fi

mkdir -p "$ESTATE"/{runs,quick,state,logs,projects} "$SYS/bin"
# runs.jsonl lives in the estate; ~/.system/runs.jsonl is that file (or a link to it)
touch "$ESTATE/runs.jsonl"
if [ "$ESTATE" != "$SYS" ] && [ ! -e "$SYS/runs.jsonl" ]; then ln -s "$ESTATE/runs.jsonl" "$SYS/runs.jsonl"; fi
# launchers
for b in pipeline run quick status finish; do ln -sfn "$REPO/bin/$b" "$SYS/bin/$b"; done
# repo location, so workers can find it
printf '%s\n' "$REPO" > "$SYS/repo"
# generated agent files + conversation plugin
"$REPO/bin/pipeline" render-agents >/dev/null
mkdir -p "$HOME/.config/devpass-code/plugin"
cp "$REPO/plugin/pipeline-conversation.js" "$HOME/.config/devpass-code/plugin/pipeline-conversation.js"
# sentry (systemd --user when available; otherwise a detached loop). --no-sentry is for
# the characterization suite, which installs into a throwaway HOME.
if [ "$SENTRY" = 0 ]; then
  echo "bootstrap: sentry install skipped (--no-sentry)"
elif command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
  mkdir -p "$HOME/.config/systemd/user"
  cp "$REPO/systemd/pipeline-sentry.service" "$HOME/.config/systemd/user/pipeline-sentry.service"
  systemctl --user daemon-reload
  systemctl --user enable --now pipeline-sentry.service
  echo "bootstrap: sentry enabled (systemd --user)"
else
  if ! pgrep -f "pipeline.cli sentry" >/dev/null; then
    ( setsid nohup "$SYS/bin/pipeline" sentry >>"$ESTATE/logs/sentry.log" 2>&1 & )
    echo "bootstrap: sentry launched detached (no systemd --user)"
  fi
fi
case ":$PATH:" in *":$SYS/bin:"*) ;; *) echo "bootstrap: add to PATH: export PATH=\"$SYS/bin:\$PATH\"";; esac
echo "bootstrap: done. estate=$ESTATE repo=$REPO"
