---
name: data-persistence
description: Use when writing or reviewing player-data persistence in Roblox — server-authoritative stats, DataStore reads/writes with pcall + retry + session locking, and never trusting a client's claimed currency, kills, or progress.
---

# Data persistence

Distilled from this operator's own dbz-cell-arena reference (ServerScriptService
DataStore usage) — same discipline, corrected where the reference cut corners.

## Server-authoritative stats

1. Player-facing stats (currency/yen, kills, damage, owned items, playtime)
   live server-side as the source of truth — a `NumberValue`/`IntValue`
   under the player in `ServerScriptService`-owned state, or a plain Lua
   table keyed by `UserId`, never a value the client sets directly.
2. Never trust a client's claimed value for currency, kills, ownership, or
   any progress stat. If a client fires a remote claiming "I have 500 yen,
   spend 100," the server re-reads its own tracked yen value and computes
   the result — it does not subtract from whatever number the client sent.
3. Client-visible copies of stats (a `leaderstats` `IntValue`, a HUD number)
   are a *projection* the server writes to, not a source the server reads
   from. Writing server -> client display is fine; reading client display ->
   server logic is the bug.
4. Any remote handler that grants currency, items, or progress
   (`DailyRewardRemoteEvent`-style flows, purchase callbacks) re-validates
   eligibility server-side every time (cooldown elapsed? product actually
   purchased via `MarketplaceService` receipt/callback, not a client "I
   bought it" message?) before writing the change.

## DataStore writes: pcall + retry + session locking

5. Every `DataStore`/`OrderedDataStore` call (`GetAsync`, `SetAsync`,
   `UpdateAsync`) is wrapped in `pcall`. An unwrapped DataStore call that
   throws (rate limit, outage) must not crash the calling script or silently
   lose the player's data — catch it, log it, and retry.
6. Retry with backoff, not a tight loop: on failure, wait (increasing delay)
   and retry a bounded number of times (e.g. 3-5 attempts), then give up and
   log loudly (this is the corner the dbz-cell-arena reference cuts — a bare
   `YenStore:SetAsync(...)` with no pcall is exactly what this rule forbids).
7. Prefer `UpdateAsync` over read-then-`SetAsync` for any value another
   session could concurrently touch (a global leaderboard entry, anything
   not exclusively owned by one player's live session) — `UpdateAsync` gets
   you an atomic read-modify-write; a separate `GetAsync` + `SetAsync` pair
   races.
8. Session locking: use `DataStore:GetAsync`/`UpdateAsync` metadata (or a
   dedicated lock key) to detect a player whose data is already
   locked/owned by another live server (rejoin-before-leave-save-finished
   is the classic dupe/rollback bug) — do not let two servers write the same
   player's save concurrently without detecting the collision.
9. Save on `PlayerRemoving` (bounded by `game:BindToClose` on server
   shutdown too) — a save that only happens on a timer risks losing the
   final minutes of a session; a save that only happens on shutdown risks
   losing everything if the server crashes instead of shutting down
   cleanly. Do both.
10. Never call `SetAsync`/`UpdateAsync` from a client-triggered remote
    without server-side rate limiting — a save-spam remote can burn a
    DataStore's request budget for the whole game.

## What a headless worker can verify

11. You can review/write the Luau enforcing all of the above and confirm it
    with `selene`/`stylua`/`rojo build` (see luau-conventions, rojo-sync).
    You cannot confirm an actual DataStore round-trip behaves correctly in
    live Roblox (DataStore only runs in a real Roblox server context) —
    that goes in `VERIFY-PACKET.md` per studio-verify: name the exact save
    condition to trigger, and the exact `get_console_output` line that
    proves the write succeeded (or the retry path fired on a simulated
    failure).
