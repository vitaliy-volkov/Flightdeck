# ADR 0001: Relationship to Autopilot

Status: accepted

Flightdeck was designed with the public [nick-vels/skills](https://github.com/nick-vels/skills) repository and its Autopilot workflow as a behavioral reference: staged delivery, durable run artifacts, review, and acceptance are useful ideas worth retaining. The upstream project is distributed under the [MIT License](https://github.com/nick-vels/skills/blob/main/LICENSE); copyright and attribution remain with nick-vels/skills contributors. Flightdeck does not claim upstream source code or text as original work.

Flightdeck is an independent implementation. Its distinguishing choices are a versioned atomic JSON state machine, stable machine-checkable transition evidence, portable adapter contracts for Codex, Claude Code, and Cursor, a permissioned plugin protocol, dry-run/resume/export behavior, and default-deny safety boundaries. Python 3.11+ standard library replaces any mandatory Node.js runtime.

The comparison is conceptual, not a compatibility or capability claim. Each adapter must prove supported actions, and unsupported actions remain blocked. The reference project's license and notices govern any material copied from it; none is intentionally vendored by this ADR.
