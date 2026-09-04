#!/usr/bin/env bash
# probe.sh <model> <outdir> : measure one model on the gateway. Writes <outdir>/<model>.json.
# Two calls: (1) plain answer, (2) one bash tool call. Records reachability, tool use, wall, tokens, cost.
set -uo pipefail
M="$1"; OUT="$2"; mkdir -p "$OUT"; W=$(mktemp -d)
run() { # $1=label $2=prompt -> writes $W/$1.jsonl, echoes wall_ms
  local t0=$(date +%s%3N)
  timeout 240 devpass-code run --format json --model "llmgateway-devpass/$M" --dir "$W" "$2" > "$W/$1.jsonl" 2>"$W/$1.err"
  echo $(( $(date +%s%3N) - t0 ))
}
W1=$(run plain "Reply with exactly the single word PONG and nothing else.")
W2=$(run tool "Use the bash tool to run exactly: echo probe-\$((6*7)) . Then reply with only the command's output.")
python3 - "$M" "$OUT" "$W" "$W1" "$W2" <<'PY'
import json,sys,os,re
m,out,w,w1,w2=sys.argv[1:]
def parse(p):
    tok=0;cost=0.0;texts=[];err=None
    for l in open(p,errors='replace'):
        l=l.strip()
        if not l.startswith('{'):
            if l: err=(err or '')+l[:200]
            continue
        try: e=json.loads(l)
        except: continue
        part=e.get('part',{})
        if e.get('type')=='step_finish': tok+=part.get('tokens',{}).get('total',0) or 0; cost+=part.get('cost',0) or 0
        if e.get('type')=='text': texts.append(part.get('text',''))
        if e.get('type')=='error' or part.get('type')=='error': err=json.dumps(e)[:300]
    return tok,cost,' '.join(texts).strip(),err
t1,c1,x1,e1=parse(f'{w}/plain.jsonl'); t2,c2,x2,e2=parse(f'{w}/tool.jsonl')
stderr=(open(f'{w}/plain.err').read()+open(f'{w}/tool.err').read())[-400:]
r={"model":m,"reachable":bool(x1) and 'PONG' in x1.upper(),"plain_ok":x1.strip().upper()=="PONG","tool_ok":"probe-42" in x2,
   "wall_ms":{"plain":int(w1),"tool":int(w2)},"tokens":{"plain":t1,"tool":t2},"cost":{"plain":round(c1,6),"tool":round(c2,6)},
   "plain_text":x1[:120],"tool_text":x2[:120],"error":(e1 or e2 or stderr.strip() or None)}
json.dump(r,open(os.path.join(out,m+'.json'),'w'),indent=1); print(json.dumps(r))
PY
rm -rf "$W"
