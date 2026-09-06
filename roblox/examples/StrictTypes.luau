--!strict
-- Tiny strict-typed, side-effect-free module for headless toolchain checks.

local function clamp(value: number, minimum: number, maximum: number): number
	if value < minimum then
		return minimum
	elseif value > maximum then
		return maximum
	end
	return value
end

return clamp
