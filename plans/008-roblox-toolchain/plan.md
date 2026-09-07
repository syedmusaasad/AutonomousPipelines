# Plan 008: Roblox toolchain, skills, and MCP wiring for the pipeline
WORKDIR: /root/pipeline

DECISION toolchain-versions: pinned, primary-source downloads (GitHub releases, all with
linux-x86_64 assets, verified 2026-09-05): rojo 7.7.0, luau-lsp 1.69.0, StyLua 2.5.2,
selene 0.31.0, lune 0.10.5. Binaries install to the ESTATE (~/.system/bin) so they
survive repo moves; never into the repo.
DECISION mcp-choice: Roblox's BUILT-IN Studio MCP (create.roblox.com/docs/studio/mcp)
is the only sanctioned live-loop path: script tools, generate_mesh/material/
procedural_model, insert_asset, execute_luau, playtest + screen_capture + input
simulation. Third-party robloxstudio-mcp (boshyxd) is archived (2026-06); Roblox's
studio-rust-mcp-server is deprecated in favor of the built-in one. The pipeline host
is Linux: Studio-side tools execute on the operator's Studio machine; our workers reach
them only through a gate-locked step. No Studio MCP is installed on this host.
DECISION source-of-truth: skills live in roblox/skills/<name>/SKILL.md in the repo;
bootstrap syncs them to ~/.config/devpass-code/skills/; a check command diff-guard
enforces sync (same pattern as agent files).
DECISION two-machine-workflow: operator develops natively: cloud Linux desktop (this
host, full pipeline + headless toolchain) AND MacBook running Roblox Studio with the
built-in Studio MCP. Live bridge: `rojo serve --address 0.0.0.0` on this host + the
Rojo Studio plugin on the MacBook pointing at host:34848 gives two-way live sync
(verify asset/port with `rojo serve` docs; the plugin connects over HTTP). For remote
access across networks: SSH reverse port-forward (`ssh -R 34848:localhost:34848`) from
the MacBook, or tailscale/wireguard; the plan does not choose for the operator but
documents both. Studio MCP runs on the MacBook; pipeline workers emit VERIFY-PACKET.md
for the operator's Studio-side session; `mcp-proxy`/stdio-over-ssh is documented as an
OPTIONAL bridge but not installed. Phase-1 toolchain is now installed at
~/.system/bin (rojo 7.7.0, stylua 2.5.2, selene 0.31.0, lune 0.10.5, luau-lsp 1.69.0);
phase-1's luau-lsp 404 was the wrong asset name, fixed by hand; unzip absent on host,
python zipfile used. Phases 2-6 proceed unchanged in scope.
DECISION budget-discipline: each phase is one bounded dispatch, ATTEMPTS: 1, small
timeouts. No live model calls beyond the dispatched workers themselves; no Studio
automation (no Studio on this host); no publishing to syedmusaasad/dbz-cell-arena —
that repo is read-only reference material cloned into the estate.

## Phase 1: install and verify the headless Luau toolchain (fast-worker)
TIMEOUT: 600
ATTEMPTS: 1
EXIT: ~/.system/bin/rojo --version | grep -q 7.7.0
EXIT: ~/.system/bin/stylua --version | grep -q 2.5.2
EXIT: ~/.system/bin/selene --version | grep -q 0.31.0
EXIT: ~/.system/bin/lune --version | grep -q 0.10.5
EXIT: ~/.system/bin/luau-lsp --version
EXIT: git log -1 --format=%s | grep -qxF 'roblox: headless toolchain (rojo, luau-lsp, stylua, selene, lune)'

Download the pinned linux-x86_64 release assets (GitHub Releases, exact versions above)
with curl -L to /tmp/devpass-code, unzip, install the binaries into ~/.system/bin with
chmod +x. Verify each with the EXIT version checks. Also commit to the repo:
roblox/selene.toml (Roblox-standard library config: base roblox, std=luau+roblox),
roblox/stylua.toml (defaults, quote_type = AutoPreferDouble), and
roblox/luau-lsp-config.json (roblox security level 2, types via roblox/types if
fetchable, else minimal). Do NOT commit binaries. If any download URL fails, record it
in NOTES.md and stop; do not silently substitute versions. Stage only roblox/ files and
NOTES.md if present. Commit exactly:
`git commit -m 'roblox: headless toolchain (rojo, luau-lsp, stylua, selene, lune)'`.
Do not push.

## Phase 2: scaffold dbz-cell-arena as a Rojo project in the estate (fast-worker)
TIMEOUT: 900
ATTEMPTS: 1
EXIT: test -d ~/.system/projects/dbz-cell-arena && test -f ~/.system/projects/dbz-cell-arena/default.project.json
EXIT: cd ~/.system/projects/dbz-cell-arena && ~/.system/bin/rojo build -o /tmp/devpass-code/dbz-build.rbxlx && test -s /tmp/devpass-code/dbz-build.rbxlx
EXIT: cd ~/.system/projects/dbz-cell-arena && ~/.system/bin/selene generate ./src > /dev/null 2>&1 || true
EXIT: git -C /root/pipeline log -1 --format=%s | grep -qxF 'roblox: dbz-cell-arena rojo scaffold + baseline'

git clone https://github.com/syedmusaasad/dbz-cell-arena.git into
~/.system/projects/ (public repo, read-only reference). Restructure for Rojo inside a
NEW directory ~/.system/projects/dbz-cell-arena-work/ (do not rewrite his history):
src/ tree with src/server, src/client, src/StarterGui, src/StarterPlayerScripts etc.,
copying his .lua files from their container-shaped folders into the mirrored src/
layout, and a default.project.json that maps them back to the right services. His
container folders already mirror the datamodel, so the mapping is mechanical:
ServerScriptService/ -> src/server/, ServerStorage/<Move>/<Move>.lua -> ModuleScripts,
StarterGui/ScreenGui/** -> src/client/ui. Do NOT touch his .rbxl (binary, reference
only). Then: ~/.system/bin/rojo build must succeed; record a selene + stylua --check
baseline (counts only, in roblox/BASELINE-dbz.md — the old .lua files are 2020-era,
they will lint noisily; record, do not rewrite his code). Stage only
roblox/BASELINE-dbz.md. Commit exactly:
`git commit -m 'roblox: dbz-cell-arena rojo scaffold + baseline'`. Do not push.

## Phase 3: Roblox skills for the agents (implementer)
TIMEOUT: 1500
ATTEMPTS: 1
EXIT: for s in luau-conventions rojo-sync studio-verify ui-loop animation-assets data-persistence; do test -f roblox/skills/$s/SKILL.md || exit 1; done
EXIT: python3 -c "import pipeline.roblox_skills as r; r.check_sync() or (_ for _ in ()).throw(AssertionError('skills not synced'))"
EXIT: git log -1 --format=%s | grep -qxF 'roblox: agent skills (luau, rojo, studio-verify, ui, animation, persistence)'

Write roblox/skills/ (SKILL.md files, frontmatter with name + description, body with
the discipline; follow the headless-worker-contract tone — rules, not essays):
- luau-conventions: strict mode, --!strict, typed signatures, no spawn() in hot paths,
  remote events validated server-side (client-server boundary security), selene +
  stylua must pass before a deliverable is registered.
- rojo-sync: default.project.json conventions, src/ layout mapping to services,
  rojo build as the pre-commit check, never edit a place directly when a Rojo project
  exists.
- studio-verify: the two-machine reality — source work + static checks happen here;
  live verification happens on the Studio machine through the built-in Studio MCP
  (playtest, screen_capture, get_console_output, character_navigation). A worker
  writing game code MUST emit a VERIFY-PACKET.md (steps: what to playtest, expected
  console output, what a screen capture should show) so the operator's Studio-side
  session can execute it; the worker must never claim live verification it cannot do.
- ui-loop: UIGridLayout/UIListLayout conventions, scale-not-offset sizing, the
  code -> playtest -> screen_capture -> adjust loop, accessibility (GamepadActivated
  etc.).
- animation-assets: animations are assets (insert_asset / marketplace IDs),
  Animator:LoadAnimation server-side, KeyframeSequence authoring stays in Studio;
  what a worker CAN do headlessly (script the wiring, timing tables) vs cannot.
- data-persistence: pattern distilled from the operator's own dbz-cell-arena repo
  (server-authoritative stats in ServerScriptService, DataStore writes with
  pcall + retry + session locking, never trust the client's claimed yen/kills).
Also: pipeline/roblox_skills.py with install_skills() (sync roblox/skills -> the
global skills dir) and check_sync() (diff guard, returns bool); a `pipeline
roblox-skills` CLI verb with --check; call install_skills() from bootstrap.sh.
Run the sync, add tests for check_sync catching a deliberate edit (same drift-test
pattern as agents), register in the suite, run the suite ONCE. Stage roblox/skills/,
pipeline/roblox_skills.py, pipeline/cli.py, bootstrap.sh, tests. Commit exactly:
`git commit -m 'roblox: agent skills (luau, rojo, studio-verify, ui, animation, persistence)'`.

## Phase 4: toolchain document + a working proof (implementer)
TIMEOUT: 1200
ATTEMPTS: 1
EXIT: test -s docs/ROBLOX-TOOLCHAIN.md
EXIT: test -s roblox/examples/StrictTypes.luau && ~/.system/bin/selene roblox/examples/StrictTypes.luau
EXIT: ~/.system/bin/rojo build roblox/examples/project/default.project.json -o /tmp/devpass-code/example.rbxlx && test -s /tmp/devpass-code/example.rbxlx
EXIT: git log -1 --format=%s | grep -qxF 'roblox: toolchain doc + verified example'

docs/ROBLOX-TOOLCHAIN.md: the two-machine architecture (primary-source verified
2026-09-05): Linux pipeline (source, types, style, lint, build, EXIT ceremony, all
non-premium seats) + operator's Studio machine running the BUILT-IN Studio MCP
(playtest, screen_capture, generate_mesh/material/procedural_model, insert_asset,
execute_luau) — Roblox's own studio-rust-mcp-server is deprecated in favor of it and
boshyxd's is archived; the maintained fork is Chrrxs/robloxstudio-mcp if a lighter
playtest-only loop is wanted. What is automated vs what needs the Studio machine; the
VERIFY-PACKET bridge; what remains gate-locked (publishing, asset uploads). Also:
working example roblox/examples/ (a tiny strict-typed Luau module + default.project.json
that builds with the phase-1 toolchain, selene-clean and stylua-clean) proving the
pipeline can go from brief -> verified build with zero Studio. The example is the
template future game plans copy. Stage docs/ROBLOX-TOOLCHAIN.md and roblox/examples/.
Commit exactly: `git commit -m 'roblox: toolchain doc + verified example'`. Do not push.

## Phase 5: approve and publish (gate)
GATE: /root/pipeline/plans/008-roblox/.approve-publish

## Phase 6: push (implementer)
TIMEOUT: 300
ATTEMPTS: 1
EXIT: git log -1 --format=%s | grep -qxF 'roblox: toolchain, skills, scaffolds published'
EXIT: git fetch -q origin main && git diff --quiet HEAD origin/main

Stage only the phase-1..4 files. Commit exactly:
`git commit -m 'roblox: toolchain, skills, scaffolds published'` then `git push origin
main`. Final lines: commit hash + pushed URL.

## Phase 7: finish toolchain doc + verified example (fast-worker)
AFTER: 4
TIMEOUT: 900
ATTEMPTS: 1
EXIT: test -s docs/ROBLOX-TOOLCHAIN.md
EXIT: test -f roblox/examples/project/default.project.json && ~/.system/bin/rojo build roblox/examples/project/default.project.json -o /tmp/devpass-code/example.rbxlx && test -s /tmp/devpass-code/example.rbxlx
EXIT: cd /root/pipeline && ~/.system/bin/selene --config roblox/selene.toml roblox/examples/StrictTypes.luau
EXIT: git log -1 --format=%s | grep -qxF 'roblox: toolchain doc + verified example'

Phase 4 timed out mid-investigation. Finish ONLY these deliverables, do not re-plan:
docs/ROBLOX-TOOLCHAIN.md (the two-machine architecture per DECISION two-machine-workflow
in this plan's preamble: Linux pipeline + MacBook Studio MCP, rojo serve bridge, SSH
reverse-forward or tailnet for remote, VERIFY-PACKET convention, gate-locked publishing;
cite create.roblox.com/docs/studio/mcp as the built-in MCP source). roblox/examples/:
a tiny strict-typed Luau module (roblox/examples/StrictTypes.luau, --!strict, typed
signature, exports a pure function; must pass selene) plus
roblox/examples/project/default.project.json mapping it to ServerScriptService, which
must build with ~/.system/bin/rojo. Selene resolves config from CWD: run it from the
repo root with --config roblox/selene.toml, or cd into roblox/. Stage only
docs/ROBLOX-TOOLCHAIN.md and roblox/examples/. Commit exactly:
`git commit -m 'roblox: toolchain doc + verified example'`. Do not push. The gate at
phase 5 then applies to phases 4+7 combined.
