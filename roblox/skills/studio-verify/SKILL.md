---
name: studio-verify
description: Use whenever a task touches gameplay-affecting Luau, UI, or anything that needs a playtest, screen capture, or console output to confirm — the two-machine split between this headless pipeline and the operator's Studio-side MCP session, and the VERIFY-PACKET.md contract for handing off live verification.
---

# Studio-verify: the two-machine reality

## What this host can and cannot do

1. This pipeline host is Linux with no Roblox Studio and no Studio MCP
   installed. You can: write/edit Luau, run `selene`, `stylua --check`,
   `luau-lsp`, and `rojo build`, and reason statically about the code. You
   cannot: launch a playtest, capture a screenshot, read the in-game console,
   simulate input, or confirm anything that only exists once the game is
   actually running in Studio.
2. The operator's Studio machine (a separate machine) runs Roblox Studio with
   the built-in Studio MCP (`create.roblox.com/docs/studio/mcp`): `playtest`,
   `screen_capture`, `get_console_output`, `character_navigation`,
   `execute_luau`, `insert_asset`, `generate_mesh`/`generate_material`/
   `generate_procedural_model`, input simulation. Those tools exist on that
   machine's session, not here.
3. Never claim you ran a playtest, took a screenshot, or read console output.
   If your transcript did not literally invoke a Studio MCP tool on the
   Studio machine, you did not verify it live — say so plainly instead of
   describing an imagined result.

## The handoff: VERIFY-PACKET.md

4. Any worker whose deliverable is gameplay-affecting (new/changed Luau that
   runs in-game, a UI change, an animation wiring, a data-persistence change)
   MUST write `VERIFY-PACKET.md` in the working directory (or the path the
   brief names) before finishing. This is the deliverable's live-verification
   spec for the operator's Studio-side session to execute — not optional
   documentation.
5. `VERIFY-PACKET.md` structure, every section required:
   - **What changed**: one or two lines, file paths.
   - **Playtest steps**: numbered, concrete actions in Studio
     (e.g. "1. Start a playtest as a single client. 2. Walk to the NPC at
     spawn. 3. Press E."). Not "test the feature."
   - **Expected console output**: exact strings or patterns
     `get_console_output` should show, and what output would mean it failed
     (e.g. an error, a warning, silence where a print was expected).
   - **Expected screen capture**: what `screen_capture` should show at which
     step (a UI element visible/hidden, a specific HUD value, an animation
     playing) — describe it precisely enough that a mismatch is obvious.
   - **Rollback**: how to undo the change in Studio if the packet fails
     (revert the Rojo sync, or the specific instance/property to restore).
6. If the brief does not name a target path, write `VERIFY-PACKET.md` at the
   repo root or the project root you touched — pick the one an operator would
   look in first, and say which you chose in your final lines.

## Division of labor

7. Static checks (`selene`, `stylua --check`, `luau-lsp`, `rojo build`) are
   yours to run and must pass before you write the packet — do not hand the
   operator a packet for code that does not even build.
8. Anything requiring Studio-only surfaces (procedural mesh/material
   generation, marketplace asset insertion, KeyframeSequence authoring,
   actual playtesting) is out of scope for you to perform; script the wiring
   and leave the Studio-only step named explicitly in `VERIFY-PACKET.md`.
9. A deliverable is complete when its static checks pass and its
   `VERIFY-PACKET.md` exists and is concrete. It is not complete when you
   have merely described what a playtest "should" show without a packet the
   operator can execute step by step.
