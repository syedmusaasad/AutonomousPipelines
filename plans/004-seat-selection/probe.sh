#!/usr/bin/env bash
# probe.sh <model> <outdir> : measure one model under the WORKER agent (pl-fast-worker), not the
# interactive agent, so the probe tests "will you run a tool" rather than "will you route an ask".
# Three calls: plain answer, one bash tool call, one small file-edit task. Wall measured in Python.
set -uo pipefail
M="$1"; OUT="$2"; mkdir -p "$OUT"; W=$(mktemp -d); export PIPELINE_HEADLESS=1
python3 - "$M" "$OUT" "$W" <<'PY'
import json, subprocess, sys, time, os
m, out, w = sys.argv[1:]
def call(label, prompt, timeout=240):
    t0 = time.monotonic()
    p = subprocess.run(["devpass-code", "run", "--format", "json", "--agent", "pl-fast-worker", "--model", f"llmgateway-devpass/{m}",
                        "--dir", w, "--auto", prompt], capture_output=True, text=True, timeout=timeout, cwd=w)
    wall = round(time.monotonic() - t0, 2)
    tok = 0; cost = 0.0; texts = []; err = None; steps = 0; ttft = None
    for l in p.stdout.splitlines():
        l = l.strip()
        if not l.startswith("{"): continue
        try: e = json.loads(l)
        except Exception: continue
        part = e.get("part", {})
        if e.get("type") == "step_finish":
            steps += 1; tok += part.get("tokens", {}).get("total", 0) or 0; cost += part.get("cost", 0) or 0
        if e.get("type") == "text":
            texts.append(part.get("text", ""))
            if ttft is None and part.get("time"): ttft = round((part["time"]["start"] - int(e.get("timestamp", 0))) / 1000, 2)
        if e.get("type") == "error" or part.get("type") == "error": err = json.dumps(e)[:300]
    if p.returncode != 0 and not err: err = (p.stderr or "")[-300:] or f"exit {p.returncode}"
    return {"wall_s": wall, "tokens": tok, "cost": round(cost, 6), "steps": steps, "text": " ".join(texts).strip()[:160], "error": err}
try:
    plain = call("plain", "Reply with exactly the single word PONG and nothing else.")
    tool = call("tool", "Run this exact shell command with your bash tool and reply with only its output: echo probe-$((6*7))")
    edit = call("edit", "Create a file named hello.py in the current directory containing a function add(a, b) that returns a+b, "
                        "with a __main__ block that prints add(2, 3). Then run it with python3 and reply with only the output.")
    edit_ok = os.path.exists(os.path.join(w, "hello.py")) and "5" in edit["text"]
    try:
        edit_ok = edit_ok and subprocess.run(["python3", os.path.join(w, "hello.py")], capture_output=True, text=True, timeout=20).stdout.strip() == "5"
    except Exception:
        edit_ok = False
    r = {"model": m, "reachable": bool(plain["text"]) and "PONG" in plain["text"].upper(), "plain_ok": plain["text"].strip().rstrip(".").upper() == "PONG",
         "tool_ok": "probe-42" in tool["text"], "edit_ok": bool(edit_ok),
         "wall_s": {"plain": plain["wall_s"], "tool": tool["wall_s"], "edit": edit["wall_s"]},
         "tokens": {"plain": plain["tokens"], "tool": tool["tokens"], "edit": edit["tokens"]},
         "cost": {"plain": plain["cost"], "tool": tool["cost"], "edit": edit["cost"], "total": round(plain["cost"] + tool["cost"] + edit["cost"], 6)},
         "steps": {"tool": tool["steps"], "edit": edit["steps"]},
         "text": {"plain": plain["text"], "tool": tool["text"], "edit": edit["text"]},
         "error": plain["error"] or tool["error"] or edit["error"]}
except subprocess.TimeoutExpired as e:
    r = {"model": m, "reachable": False, "plain_ok": False, "tool_ok": False, "edit_ok": False, "wall_s": {}, "tokens": {}, "cost": {"total": 0},
         "steps": {}, "text": {}, "error": f"timeout: {e}"}
json.dump(r, open(os.path.join(out, m + ".json"), "w"), indent=1); print(json.dumps(r))
PY
rm -rf "$W"
