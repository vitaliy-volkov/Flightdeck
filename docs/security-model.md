# Security model

Flightdeck is offline-first and emits no telemetry by default. Plugins receive no network, shell, file, memory, or external-service capability unless explicitly declared and granted.

Capability is not authorization. External writes, publication, deployment, deletion, payments, messages, and history rewriting require a fresh approval event from the user immediately before execution. Full mode cannot bypass this gate. Missing tools, permissions, or evidence produce a fallback or `BLOCKED`, never a fabricated success.

State writes are versioned and atomic; corrupt or unsupported state must be reported without overwrite. Reports must redact secrets. See [SECURITY.md](../SECURITY.md) for private vulnerability reporting.
