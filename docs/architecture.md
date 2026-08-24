# Architecture

Flightdeck separates a declarative workflow from agent-specific execution. `core` owns phases, modes, requirements, and gates; `state` owns versioned atomic JSON storage; `adapters` map common actions to Codex, Claude Code, or Cursor capabilities; `plugins` validate and dispatch isolated hooks; `cli` owns commands and exit codes; `reporting` renders redacted evidence.

State lives at `.flightdeck/state.json`. The original brief is immutable, later additions are append-only, and stable requirement IDs trace specification, work item, verification, and acceptance. Each transition requires validator evidence. A capability probe may choose a declared fallback; it may not simulate success.

The runtime is Python 3.11+ standard library. Optional agent capabilities affect execution strategy, not workflow meaning. See the skill's [phase contract](../skills/flightdeck/references/phases.md) and [mode contract](../skills/flightdeck/references/modes.md).
