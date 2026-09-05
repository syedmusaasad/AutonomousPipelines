# Recommended Roblox Development Workflow

## Role Recommendation: Reuse Existing Agents
For the smallest viable workflow with low token overhead, **reuse the existing `implementer` role** with tailored Roblox/Luau project instructions. Use the **`researcher`** for API questions. The current `frontend-worker` is web-oriented and unsuitable for Roblox UI out-of-the-box. Introduce a specialized "Roblox Developer" seat only if measured repeated tasks warrant the overhead.

## Architecture & Verification Realities
**Headless vs. Engine Constraints:** 
The pipeline host runs Linux, but Roblox Studio only officially supports Windows and macOS (https://create.roblox.com/docs/studio/setup). Therefore, the pipeline **cannot run Roblox Studio headlessly** for end-to-end integration or playtesting. 

**What can be checked headlessly:**
- **Static Analysis & Type Checking:** Luau supports static typing (https://luau.org/typecheck). The pipeline can headlessly verify syntax and types, catching many logic errors before deployment.
- **Project Structure:** Using Rojo (https://rojo.space/docs/v7/), the pipeline can maintain file structures, build `.rbxlx` files, and validate synchronization maps.

**What requires Studio on a supported OS & Human Review:**
- **Engine Runtime & Gameplay Validation:** Luau execution in a generic environment cannot validate Roblox engine APIs, physics, or rendering. Framework verification improves correctness but guarantees neither frontier-model reasoning nor "fun" gameplay. 
- **Multiplayer & Persistence Testing:** Validating DataStores and multi-client replication requires Studio's local server testing or live game environments.
- **Client-Server Boundary Verification:** While an agent can write server-authoritative code validating client inputs via `RemoteEvents` (https://create.roblox.com/docs/scripting/security/client-server-boundary), actual exploit-resistance testing requires live execution.

## Small Vertical-Slice Workflow
1. **Plan & Research (`researcher`):** Identify required Roblox APIs and define the client-server boundaries for the feature.
2. **Implement (`implementer`):** Write strict Luau code and Rojo project files (`default.project.json`) in the Linux pipeline.
3. **Headless Verification:** Run Luau static analysis/type checks and Rojo build validations locally.
4. **Human Review Gate:** A human developer syncs the code via Rojo into Roblox Studio on Windows/macOS, playtests the vertical slice, and approves/rejects.
5. **Publishing:** Handled manually by a human through an approval gate.

*(Note: Pipeline seats operate automatically without premium models, and model configurations must not be changed in this workflow.)*

## Out-of-Scope & Next Steps
**Out of Scope:**
- Automatic gameplay validation or end-to-end engine testing.
- Automatic publishing to Roblox.
- Broad evaluation campaigns or complex framework installations.

**Next Facts Needed (When Starting a Specific Game):**
- Target audience and genre constraints.
- Core game loop and data persistence requirements.
- Expected client-server interactions to map `RemoteEvents`/`RemoteFunctions`.