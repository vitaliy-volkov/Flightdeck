# Plugin authoring

A plugin declares `name`, `version`, `api_version`, executable Python `entrypoint`, hooks, compatible agents, and requested capabilities in `flightdeck.plugin.json`. Capabilities are denied unless explicitly granted: `network`, `shell`, `files.read`, `files.write`, `memory`, `external.read`, and `external.write`.

The entrypoint is a separate process using one JSON object per line. Requests contain `protocol`, `hook`, `run_id`, `payload`, and `granted_capabilities`; responses contain `ok`, `output`, `events`, and `error`. Non-zero exit, timeout, extra stdout, or an invalid response fails that hook. A plugin cannot skip gates, edit the original brief, or turn missing capability into success.

Supported hooks are `before_phase`, `after_phase`, `before_gate`, `after_gate`, `on_blocked`, and `report_section`. External writes and irreversible actions still require runtime user approval even when a capability is granted.

Local paths and Git URL/ref sources are supported by the v1 contract. Installation records a canonical source, resolved commit or local tree hash, version, and SHA-256 in the lock file. A central marketplace is outside v1.
