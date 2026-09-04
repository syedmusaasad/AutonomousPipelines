# Plan 002: publish to github.com/syedmusaasad/AutonomousPipelines (private)
WORKDIR: /root/pipeline

DECISION publish-private: operator chose a private GitHub repo named AutonomousPipelines
under syedmusaasad; the push is irreversible and external, so it sits behind a gate.
The token lives at ~/.config/pipeline/github-token and is never read into any file in
the repo, printed, or echoed.

## Phase 1: create repo and set remote (fast-worker)
TIMEOUT: 300
EXIT: git remote get-url origin | grep -qx 'https://github.com/syedmusaasad/AutonomousPipelines.git'
EXIT: python3 -c "import urllib.request,json;t=open('/root/.config/pipeline/github-token').read().strip();r=urllib.request.Request('https://api.github.com/repos/syedmusaasad/AutonomousPipelines',headers={'Authorization':'Bearer '+t});d=json.load(urllib.request.urlopen(r));assert d['private'] is True and d['default_branch'] in ('main',), d"
EXIT: git config credential.helper | grep -qx '!f() { echo username=syedmusaasad; echo "password=$(cat /root/.config/pipeline/github-token)"; }; f'
EXIT: ! git grep -q "$(cat /root/.config/pipeline/github-token)" -- . ; ! grep -rq "$(cat /root/.config/pipeline/github-token)" .git/config

1. Create the repository with this exact Python (do not use curl):
   python3 - <<'PY'
   import urllib.request, json
   t = open('/root/.config/pipeline/github-token').read().strip()
   body = json.dumps({"name": "AutonomousPipelines", "private": True, "description": "Autonomous software pipeline: plans -> verified, journaled work via headless AI coding workers", "has_wiki": False}).encode()
   req = urllib.request.Request('https://api.github.com/user/repos', data=body, method='POST', headers={'Authorization': 'Bearer ' + t, 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json'})
   try:
       print(json.load(urllib.request.urlopen(req))['html_url'])
   except urllib.error.HTTPError as e:
       b = e.read().decode(); print(e.code, b)
       if e.code != 422 or 'already exists' not in b: raise
   PY
   A 422 "name already exists" is fine: the repo is already there.
2. `git remote add origin https://github.com/syedmusaasad/AutonomousPipelines.git` (or `git remote set-url origin ...` if origin exists).
3. Configure a repo-local credential helper that reads the token from the file at push
   time, so the token is never stored in .git/config:
   git config credential.helper '!f() { echo username=syedmusaasad; echo "password=$(cat /root/.config/pipeline/github-token)"; }; f'
4. Do not push. Do not print the token. Do not commit anything.
Final lines: `git remote -v`.

## Phase 2: approve push (gate)
GATE: /root/pipeline/plans/002-publish/.approve-push

## Phase 3: push main (implementer)
TIMEOUT: 300
EXIT: git fetch -q origin main && git diff --quiet HEAD origin/main
EXIT: git rev-parse --abbrev-ref main@{upstream} | grep -qx origin/main
EXIT: ! git grep -q "$(cat /root/.config/pipeline/github-token)" -- .

Run exactly: `git push -u origin main`. No force, no tags, no other branches.
If the push is rejected, write the rejection to NOTES.md and stop; do not retry with any flag.
Final lines: `git log -1 --format='%h %s'` and `git status -sb | head -1`.
