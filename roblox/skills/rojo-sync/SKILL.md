---
name: rojo-sync
description: Use when a Roblox project has a default.project.json — src/ layout, service mapping, and rojo build as the pre-commit check; never hand-edit a place file when Rojo owns the tree.
---

# Rojo sync

## The project file is the source of truth

1. If `default.project.json` exists anywhere at or above the file you are
   editing, that Rojo project owns the DataModel tree. Never open or edit a
   `.rbxl`/`.rbxlx` place file directly to make a source change — edit the
   `src/` files Rojo maps, then sync. A hand-edited place file and the Rojo
   tree will silently diverge; the next sync overwrites one or the other.
2. `default.project.json` conventions for this pipeline:
   - `$path` entries map a directory straight onto a service:
     `ServerScriptService` -> `src/server`, `ServerStorage` -> `src/server/storage`,
     `StarterGui` -> `src/client/ui`, `StarterPlayer.StarterPlayerScripts` ->
     `src/StarterPlayerScripts`, `StarterPlayer.StarterCharacterScripts` ->
     `src/StarterCharacterScripts`, `ReplicatedStorage` -> `src/shared` (when
     present).
   - Keep the mapping mechanical: one service subtree per top-level `$path`.
     Do not nest unrelated services under a shared `$path`.
   - Name the project `name` field after the game/repo, not "game" or "place".

## src/ layout

3. Directory names under `src/` mirror the service they map to
   (`src/server`, `src/client`, `src/shared`) so the mapping in
   `default.project.json` is legible without cross-referencing.
4. ModuleScripts live under the service subtree they are consumed from
   (a server-only module lives under `src/server`, never under `src/client`
   even if the code looks reusable — client code cannot require a
   `ServerScriptService` module and shared code that both sides need belongs
   under `src/shared` / `ReplicatedStorage`, not duplicated).
5. One Lua/Luau file per Roblox `Script`/`LocalScript`/`ModuleScript` instance.
   Rojo derives the instance name from the filename; do not rely on manual
   renaming inside Studio to fix a mismatch — fix the filename.

## rojo build is the pre-commit check

6. Before registering any deliverable that touches files under a Rojo
   project's `src/`, run `rojo build -o /tmp/devpass-code/<name>.rbxlx` (or
   the project's own build target) from the project root and confirm it
   exits 0 and produces a non-empty file. A source change that does not build
   is not done, regardless of how correct the Luau reads.
7. `rojo build` failing on a syntax error or a bad `$path` reference is not a
   Rojo problem to route around — fix the source or the project file, rerun
   the build, and only then continue.
8. Never commit `.rbxl`/`.rbxlx` build output as part of a source change; it
   is a build artifact, not a deliverable. Build to `/tmp/devpass-code/` (or
   another scratch path), verify, and let the artifact go stale.
9. If you rename or move a file under `src/`, check whether
   `default.project.json` needs an updated `$path` before you run the build
   check — a silently-succeeding build after a rename usually means the old
   path is still being served from somewhere unexpected; investigate rather
   than assume.
