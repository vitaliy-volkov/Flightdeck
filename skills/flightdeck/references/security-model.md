# Security model

Flightdeck is offline-first and emits no telemetry by default. Plugins receive no network, shell, file, memory, or external-service capability unless explicitly declared and granted.

Capability is not authorization. External writes, publication, deployment, deletion, payments, messages, and history rewriting require a fresh approval event from the user immediately before execution. Full mode cannot bypass this gate. Missing tools, permissions, or evidence produce a fallback or `BLOCKED`, never a fabricated success.

State writes are versioned and atomic; corrupt or unsupported state must be reported without overwrite. Reports must redact secrets. See the repository [SECURITY.md](https://github.com/vitaliy-volkov/Flightdeck/blob/main/SECURITY.md) for private vulnerability reporting.

## Plugin trust boundary

Python audit hooks are defense in depth, not an OS-grade sandbox: code running in the plugin process must still be treated as untrusted. Flightdeck v1 therefore fails closed for direct `network`, `shell`, and `files.write` grants because the stdlib cannot reliably contain child processes or kernel-level I/O. `external.write` is a brokered intent only: a plugin may return an outward event, but Flightdeck validates and consumes a fresh user approval and does not execute the effect itself. `files.read` is constrained by the audit runner and never grants write access, including through `os.open` numeric flags. Deployments that need direct high-risk access must supply a separately reviewed OS sandbox/broker; absence of that broker is an explicit error, never success.
