#!/usr/bin/env bash
# Claude Code SessionStart hook: exports the owning conversation id into every shell
# the interactive agent spawns (via $CLAUDE_ENV_FILE), so pipeline launchers key runs to
# the conversation. Stable across resumes of the same session. Headless workers keep
# the PIPELINE_CONVERSATION they inherited from the engine.
set -euo pipefail
[ "${PIPELINE_HEADLESS:-}" = "1" ] && exit 0
[ -n "${CLAUDE_ENV_FILE:-}" ] || exit 0
sid="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null || true)"
[ -n "$sid" ] && printf 'export PIPELINE_CONVERSATION=%q\n' "$sid" >> "$CLAUDE_ENV_FILE"
exit 0
