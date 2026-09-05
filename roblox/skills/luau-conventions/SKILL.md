---
name: luau-conventions
description: Use when writing or editing Luau (.lua/.luau) source for a Roblox project — strict typing, hot-path discipline, and client-server security rules that gate every Roblox deliverable before it is registered.
---

# Luau conventions

Rules, not style suggestions. A deliverable that violates any of these is not done.

## Strict mode and typing

1. Every new file starts with `--!strict`. `--!nonstrict` requires a NOTES.md
   line explaining why strict mode cannot apply here; do not default to it.
2. Public functions (module exports, remote handlers, anything called across a
   file boundary) carry typed signatures: parameter types and a return type.
   `function Foo(x): any` is not typed — `any` defeats strict mode's purpose.
3. Module return types are declared with `export type` when the module returns
   a table shape other code depends on. Do not let callers infer structure
   from usage.
4. `--!strict` catching nothing is a signal the module has no meaningful types
   yet, not that strict mode is working. If selene/luau-lsp report zero
   diagnostics on a file with only `any`-typed locals, treat that as
   under-typed, not clean.

## Hot-path discipline

5. No `spawn()`, `delay()`, or `wait()` inside `RenderStepped`, `Heartbeat`,
   `PreSimulation`/`PostSimulation` connections, or any per-frame/per-tick
   loop. Use `task.spawn`/`task.defer` outside the hot path to hand off work,
   never inside it.
6. No table allocation, string concatenation, or `Instance.new` inside a
   per-frame callback. Preallocate outside the loop; mutate in place inside it.
7. `pairs`/`ipairs` over unbounded, unpooled collections inside a per-frame
   callback is a red flag — say in NOTES.md why the collection is bounded, or
   restructure.

## Client-server boundary security

8. Every `RemoteEvent:OnServerEvent` / `RemoteFunction.OnServerInvoke` handler
   validates its arguments before acting: type-check, range-check, and
   re-derive any value the server already tracks (position, currency, cooldown
   state) rather than trusting the payload. A client argument is evidence, not
   fact.
9. Never let a remote handler branch on a client-supplied identity, ownership,
   or economy claim (userId-of-target, "I own this", "my yen is X") without
   re-checking it server-side against server state.
10. Rate-limit or cooldown-gate remote handlers that can be called in a tight
    client loop (e.g. an ability-fire event) — the client can call
    `FireServer` as fast as it wants; the server decides how often it counts.
11. `RemoteEvent`/`RemoteFunction` instances live under `ReplicatedStorage` and
    are named for the action they perform (`SaveRequest`, not `Event1`).

## Lint gate

12. `selene <path>` and `stylua --check <path>` must both pass (roblox/selene.toml,
    roblox/stylua.toml at repo root) before a deliverable naming Luau files is
    registered. Run them yourself; do not rely on a later phase to catch
    failures.
13. A failing selene/stylua run is not a style nit to defer — fix it or, if the
    rule is wrong for this code, say why in NOTES.md and stop (gates only
    ratchet; you do not get to silently loosen roblox/selene.toml or
    roblox/stylua.toml to make a file pass).
14. luau-lsp (roblox/luau-lsp-config.json) is the type-checking source of
    truth when available; a clean selene run does not substitute for a clean
    luau-lsp run when both are runnable in this environment.
