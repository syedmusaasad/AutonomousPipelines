---
name: ui-loop
description: Use when building or editing Roblox UI (ScreenGui, Frame, UIGridLayout/UIListLayout, buttons) — scale-not-offset sizing, layout instance conventions, the code-playtest-screen_capture-adjust loop, and gamepad/accessibility support.
---

# UI loop

## Layout conventions

1. Prefer `UIListLayout` for a single-axis stack (a menu list, a leaderboard
   column) and `UIGridLayout` for a uniform grid (an inventory, a shop grid).
   Do not hand-position siblings with absolute offsets when a layout instance
   would keep them consistent as content changes.
2. Every layout instance sets `SortOrder` explicitly (`LayoutOrder` on
   children, not insertion order) — insertion order is not a stable contract.
3. Use `UIPadding` for internal spacing instead of baking padding into a
   Frame's `Size`/`Position`. Padding as position math breaks the moment a
   parent resizes.
4. `UIAspectRatioConstraint` for anything that must not stretch/squash
   (icons, portraits) — do not rely on a fixed pixel size to keep an aspect
   ratio.

## Scale, not offset

5. Default to `Scale` for `Size` and `Position` on anything meant to work
   across screen sizes/aspect ratios (phone, tablet, desktop, console). Use
   `Offset` only for a fixed-pixel detail inside an already-scaled container
   (a 2px border, an icon at a fixed size within a scaled frame).
6. A UI element with `Size = UDim2.new(0, 300, 0, 200)` at the top level of a
   ScreenGui is a defect, not a style choice — it will be the wrong size on
   any device that isn't the one it was eyeballed on. Wrap it in a
   scale-sized container or convert to `Scale`.
7. `AnchorPoint` (0.5, 0.5) plus `Scale` position is the default way to
   center something; do not hand-compute a pixel offset to center.

## The loop

8. The only loop that proves a UI change works is: write the Luau/UI change
   here -> hand off via `VERIFY-PACKET.md` (see studio-verify) naming exactly
   what to screenshot at what state -> operator's Studio session playtests
   and calls `screen_capture` -> you read the capture description/feedback
   the operator returns -> adjust and repeat. Do not skip the packet step and
   describe how the UI "should look."
9. `VERIFY-PACKET.md` for a UI change must name: which resolution/aspect
   ratio to test at minimum (e.g. a narrow mobile-like viewport if the layout
   claims to be responsive), which state to be in when the capture is taken
   (e.g. "inventory open, 3 items owned"), and what would look wrong (e.g.
   "icons overlapping" / "text clipped").

## Accessibility

10. Every actionable UI element (button, selectable list entry) that a mouse
    click can trigger must also work via gamepad: connect
    `GuiButton.SelectionGained`/`SelectionLost` for visual focus state, and
    handle `GamepadActivated` — do not wire `MouseButton1Click` alone and
    call the control done in a game meant to support gamepad or console
    input.
11. Set `Selectable = true` and a sane `NextSelection*` chain (or let
    `GuiService` autodetect it) on interactive elements inside a custom grid
    so gamepad navigation does not dead-end.
12. Text must have real contrast against its background (do not rely on a
    background image alone to make white-on-white readable) and hit targets
    should be large enough for a controller/touch input, not just a precise
    mouse pointer.
