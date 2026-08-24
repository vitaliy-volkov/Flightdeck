# Modes and depth

Modes control who resolves reversible workflow decisions; they never relax safety gates.

- `full`: decide remaining reversible matters automatically and record each material decision in `assumptions`.
- `semi`: proceed autonomously when evidence supports one reasonable choice; ask on consequential ambiguity.
- `interview`: elicit product choices during briefing before advancing.
- `manual`: require user approval before entering both `spec` and `plan`.

Schedule a change with `python3 skills/flightdeck/scripts/flightdeck.py --project . mode --set <mode>`. The CLI records `pending_mode` immediately but keeps the current phase behavior unchanged. The core consumes it on transition to the next phase. Switching to `full` automates only remaining reversible decisions; external or irreversible actions still need fresh approval.

Depth is independent: `strict` minimizes optional exploration, `normal` uses proportionate checks, and `deep` expands analysis and verification. `polish` is an optional finish pass after required acceptance evidence, not a substitute for it.
