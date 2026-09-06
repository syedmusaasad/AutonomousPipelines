# Roblox Toolchain: Two Machines, One Source of Truth

## Architecture

The Roblox toolchain separates headless pipeline execution on Linux from interactive engine operations in Roblox Studio on macOS. The Git repository serves as the single source of truth for all place code and configurations. The Linux pipeline host runs headless workers on non-premium seats using tool binaries in `~/.system/bin`, including Rojo 7.7.0, Selene 0.31.0, StyLua 2.5.2, Lune 0.10.5, and luau-lsp 1.69.0. These Linux workers execute all headless verification tasks: static type analysis, linting, style formatting, Rojo place builds, and EXIT ceremonies. The MacBook host runs Roblox Studio with the built-in Studio MCP server (`create.roblox.com/docs/studio/mcp`), which handles script tools, procedural generation (`generate_mesh`, `generate_material`, `generate_procedural_model`), asset insertion, Luau execution, input simulation, screen capture, and live playtesting. Roblox deprecated its official `studio-rust-mcp-server` in favor of this built-in MCP, and `boshyxd/robloxstudio-mcp` reached archive status in June 2026.

## The Live Bridge (rojo serve)

The development workflow relies on `rojo serve --address 0.0.0.0` running in the project directory on the Linux host. The official Rojo Studio plugin on the MacBook connects to `host:34848` over the network. This connection maintains two-way live synchronization across both machines. Edits saved in Roblox Studio immediately convert into filesystem changes on the Linux host, and repository commits sync back into Studio without manual file imports. When both devices share a local network, the plugin connects directly to the Linux IP address. For remote environments, the operator routes traffic through a Tailscale network or an SSH reverse port forward using `ssh -R 34848:localhost:34848` from the MacBook.

## What Runs Where

The pipeline maintains a strict separation of responsibilities between the Linux host and the MacBook. Headless tasks run on Linux to provide fast, automated feedback during code authoring and pre-commit checks. Interactive engine capabilities run on macOS inside Roblox Studio through the Studio MCP interface. The following matrix details the operational boundaries and execution methods for each category of work.

| Task | Where | How |
| --- | --- | --- |
| Source code editing | Linux host | Luau files edited in repository tree |
| Type checking | Linux host | `luau-lsp` static analysis |
| Code linting | Linux host | `selene` linter in `~/.system/bin` |
| Code formatting | Linux host | `stylua --check` formatting checks |
| Place file builds | Linux host | `rojo build default.project.json --output place.rbxlx` |
| Pipeline EXIT checks | Linux host | Headless bash scripts and git commands |
| Live playtesting | MacBook | Studio MCP `playtest` tool |
| Screen capture | MacBook | Studio MCP `screen_capture` tool |
| Mesh generation | MacBook | Studio MCP `generate_mesh` tool |
| Material generation | MacBook | Studio MCP `generate_material` tool |
| Procedural model generation | MacBook | Studio MCP `generate_procedural_model` tool |
| Asset insertion | MacBook | Studio MCP `insert_asset` tool |
| Animation authoring | MacBook | Studio Animation Editor and KeyframeSequence tools |

## VERIFY-PACKET Convention

Headless workers cannot launch Roblox Studio or run live engine playtests. Every worker that modifies gameplay code must write a `VERIFY-PACKET.md` file alongside its code deliverable. This verification packet specifies exact playtest steps, expected console output strings, visual requirements for screen captures, and rollback instructions. The packet also includes the explicit Studio MCP tool calls that the operator executes in the Studio session. Workers must never claim verification results from live environments that they cannot execute directly.

## Gate-Locked Actions

The toolchain enforces explicit plan gates on sensitive engine and account operations. Publishing place files to live Roblox experiences requires an operator approval gate. Uploading marketplace assets and spending Robux account balances also remain strictly behind plan gates. No automated worker or headless dispatch may execute these destructive or financial actions without operator sign-off.

## Getting Started

Operators configure the bridge and start local synchronization through a four-step initialization process.

1. Install the Rojo plugin inside Roblox Studio on the MacBook.
2. Enable the built-in Studio MCP server in the Roblox Studio settings menu.
3. Execute `rojo serve --address 0.0.0.0` in the target project directory on the Linux host.
4. Connect to host port 34848 from the Rojo plugin panel inside Roblox Studio.

The Rojo plugin then synchronizes project files directly into the open Studio place.
