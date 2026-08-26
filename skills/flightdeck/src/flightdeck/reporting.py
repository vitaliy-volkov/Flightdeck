"""Canonical, redacted rendering for exports and acceptance evidence."""

import json


_REDACTED_KEYS = {"secret", "secrets", "token", "password", "api_key", "private_key"}


def _redact(value):
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items() if str(key).lower() not in _REDACTED_KEYS}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def render(state, format):
    """Render a state export or validated blind-acceptance report as JSON text."""
    if not isinstance(state, dict):
        raise ValueError("report input must be an object")
    if format == "acceptance":
        if state.get("blind") is not True or state.get("ok") is not True:
            raise ValueError("acceptance report must record blind=true and ok=true")
        requirements = state.get("requirements")
        if not isinstance(requirements, list) or any(not isinstance(item, str) for item in requirements):
            raise ValueError("acceptance requirements must be a list of IDs")
        value = {"blind": True, "ok": True, "requirements": requirements}
    elif format == "json":
        value = _redact(state)
    else:
        raise ValueError("unsupported report format: %s" % format)
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


__all__ = ["render"]
