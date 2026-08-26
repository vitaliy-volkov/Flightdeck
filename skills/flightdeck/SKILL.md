---
name: flightdeck
description: Run or resume a traceable software-delivery workflow from brief through blind acceptance, with explicit gates for external or irreversible actions.
---

# Flightdeck

Use Flightdeck when the user wants an autonomous, resumable development cycle with requirements traceability and evidence-based acceptance.

## Start or resume

1. If `.flightdeck/state.json` exists, run `python3 skills/flightdeck/scripts/flightdeck.py --project . resume` and continue from its `phase`; do not repeat the brief.
2. Otherwise preserve the user's brief verbatim, choose the requested mode and depth, and run `python3 skills/flightdeck/scripts/flightdeck.py --project . init --mode <mode> --depth <depth>`.
3. Read [phases](references/phases.md). Read [modes](references/modes.md) when selecting or changing mode.

Treat validator output and artifacts as evidence. A missing capability or failed check is `BLOCKED`, never success. Only the user may remove a requirement. Record automatic decisions as assumptions and excluded work as deferred scope.

External writes, publication, deployment, deletion, payment, messaging, and history rewriting always require a fresh user approval immediately before the action. Full mode does not grant that approval.

For adapter or plugin work, read [architecture](references/architecture.md) or [plugin authoring](references/plugin-authoring.md), respectively. For permission and isolation decisions, read the [security model](references/security-model.md).
