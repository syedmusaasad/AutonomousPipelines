---
name: animation-assets
description: Use when wiring up Roblox animations — Animator:LoadAnimation, animation IDs/marketplace assets, KeyframeSequence authoring — and to know what a headless worker can script versus what only Roblox Studio can author.
---

# Animation assets

## Animations are assets, not code

1. An animation is a `KeyframeSequence` uploaded to Roblox and referenced by
   an asset ID (`rbxassetid://...`), wrapped in an `Animation` instance whose
   `AnimationId` points at it. You do not write keyframe data as Luau; you
   reference an asset ID that already exists (a marketplace animation, or one
   the operator authored and published from Studio).
2. If a brief needs a *new* animation (new keyframes, new motion), that
   requires Roblox Studio's animation editor — a headless worker cannot
   author `KeyframeSequence` data. Say so explicitly in `VERIFY-PACKET.md` or
   NOTES.md rather than inventing a fake asset ID or stubbing motion in code.
3. `insert_asset` (Studio MCP, Studio-machine only) is how a marketplace
   animation gets pulled into a place. A headless worker names the asset ID
   it wants inserted and leaves the actual insertion as a Studio-side step.

## What you CAN do headlessly

4. Script the wiring: `Animator:LoadAnimation(animation): AnimationTrack`,
   `track:Play()/:Stop()/:AdjustSpeed()`, weight/fade parameters, and the
   state machine that decides which animation plays when (idle/walk/attack
   transitions). This is ordinary typed Luau and belongs in strict-mode
   source files like anything else — see luau-conventions.
5. Author timing tables headlessly: marker names, expected durations,
   priority levels (`Enum.AnimationPriority`), looping flags, and the
   data-driven table mapping an action name to `{id, priority, looped}` — as
   long as the asset IDs referenced already exist (are known, published, or
   explicitly marked TODO with the exact ID needed).
6. Write the server-side load path: `Animator:LoadAnimation` should be called
   server-side for anything that needs to replicate to other clients
   (combat moves, emotes visible to others) — do not load and play
   server-relevant animations only on the firing client and assume it
   replicates; it does not without a server-driven `AnimationTrack` or an
   explicit replication event.

## What you CANNOT do headlessly

7. Cannot author or edit `KeyframeSequence` keyframes — no code path exists
   for that outside Studio's animation editor.
8. Cannot upload a new animation asset to Roblox (asset upload requires
   Studio/website auth flows this host does not have and this pipeline does
   not automate — see the plan's publishing gate-lock).
9. Cannot confirm an animation *looks* right (timing, blending, foot sliding)
   without a playtest and screen capture on the Studio machine — write that
   requirement into `VERIFY-PACKET.md` per studio-verify; do not claim visual
   correctness from reading the timing table alone.
