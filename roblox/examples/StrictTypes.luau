--!strict
-- Tiny strict-typed Luau module: proves the headless pipeline can produce
-- selene-clean, stylua-clean, rojo-buildable Roblox source with zero Studio.
-- Copy this file + roblox/examples/project/ as the starting point for a new
-- game plan's first script (see docs/ROBLOX-TOOLCHAIN.md).

export type Cooldown = {
	seconds: number,
	lastFiredAt: number,
}

local CooldownGate = {}

function CooldownGate.new(seconds: number): Cooldown
	assert(seconds >= 0, "cooldown seconds must be non-negative")
	return { seconds = seconds, lastFiredAt = 0 }
end

-- Re-derives elapsed time from the server clock rather than trusting a
-- caller-supplied timestamp (see roblox/skills/luau-conventions/SKILL.md,
-- rule 8: never trust client-supplied state).
function CooldownGate.tryFire(gate: Cooldown, now: number): boolean
	if now - gate.lastFiredAt < gate.seconds then
		return false
	end
	gate.lastFiredAt = now
	return true
end

return CooldownGate
