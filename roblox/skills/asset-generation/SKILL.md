---
name: asset-generation
description: Use when authoring 3D asset generation specs (mesh, material, procedural model, asset insert) for execution via Roblox Studio MCP — file-based spec contracts, style anchor adherence, and Studio-side capture checks.
---

# Asset generation

Pipeline workers author 3D asset generation requests as structured files under `src/assets/specs/<name>.md` for execution by the operator's Studio MCP session.

## The spec file

1. Every generation request is a file under `src/assets/specs/<name>.md`, never chat text.
2. Every spec file requires nine exact fields:
   - `kind`: `mesh` | `material` | `procedural_model` | `asset_insert`
   - `name`: kebab-case asset identifier matching the filename.
   - `purpose`: one sentence, what the player sees.
   - `prompt`: the generation prompt: concrete nouns, lighting, art-style anchor; no vague adjectives.
   - `reference_images`: paths under `src/assets/refs/` + one line each on what aspect it anchors.
   - `part_schema`: for `procedural_model`: the primitive list with counts/sizes — blocks, spheres, cylinders, wedges. For other kinds, write `none`.
   - `target_parent`: dot-path in the datamodel.
   - `capture_check`: what a `screen_capture` of the result must show to count as done, written as a verifiable sentence.
   - `failure_notes`: what to do if the result looks wrong — one line.

## Naming

3. Name spec files with kebab-case matching the asset name (for example, `src/assets/specs/stone-pillar.md`).
4. Write one asset per spec file. Do not combine multiple generation requests into one file.
5. Do not use "final_v2" suffixes or numbered draft suffixes in filenames.
6. Revisions create a new spec file that supersedes by naming the old one in a `supersedes:` line, or edit the spec file directly in place.

## Art-style anchor

7. Every prompt references the project's style anchor file (`src/assets/style.md`, the project's art direction: palette, silhouette rules, material rules).
8. A prompt without the style anchor is a defect.

## Division of labor

9. Pipeline workers write specs and wiring (code that positions/anchors the asset) and NEVER claim visual verification.
10. Visual verification happens Studio-side via the spec's `capture_check`, executed by the operator's session using `screen_capture`.
11. Premium or interactive models may judge captures Studio-side; the pipeline never dispatches them.

## The loop

12. The generation loop runs in six discrete stages:
    - Spec: pipeline worker writes and commits `src/assets/specs/<name>.md`.
    - Generation: Studio executes built-in Studio MCP generation tools (`generate_mesh`, `generate_material`, `generate_procedural_model`, or `store_image`).
    - Capture: Studio executes `screen_capture` on the created instance.
    - Review: operator (or their premium session) judges against `capture_check`.
    - Remediation: if wrong, inspect `failure_notes` then revise the prompt field in the spec file (never verbal fixes).
    - Re-run: regenerate the asset with the updated spec.
13. Revisions land as spec edits so the history is diffable.

## Insert assets

14. Specs using `kind: asset_insert` execute via the Studio MCP `insert_asset` tool instead of generation tools.
15. `insert_asset` specs must include the asset ID (`rbxassetid://...` or numeric asset ID).
16. `insert_asset` specs must include the reason it beats generating (license-safe, from Creator Store, matches style anchor).
17. `insert_asset` specs must include the `target_parent` dot-path.

## Honesty rule

18. A spec is a request, not a result.
19. Workers end their dispatch by naming the spec files written, never by describing how the asset looks.
