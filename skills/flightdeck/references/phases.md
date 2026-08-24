# Phases and gates

Flightdeck advances only after the current phase's validator supplies successful evidence:

1. `preflight` — inspect the workspace and available capabilities.
2. `manifest` — preserve the brief and assign stable requirement IDs.
3. `briefing` — resolve material ambiguity without rewriting the brief.
4. `spec` — define observable behavior, boundaries, and traceability.
5. `plan` — split work at public seams with acceptance criteria.
6. `build` — implement and test the approved scope.
7. `review` — independently inspect the result against manifest and spec.
8. `acceptance` — perform blind, evidence-based acceptance and report the result.

A successful phase produces an artifact, validator identity, and evidence. Unsupported agent operations must use a documented fallback or stop as `BLOCKED`. Do not infer completion from a progress indicator, generated report, or command exit alone.

In `manual` mode, entering `spec` and `plan` additionally requires user approval. A mode change never alters the current phase's gate: persist it and apply it when entering the next phase.
