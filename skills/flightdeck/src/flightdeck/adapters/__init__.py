"""Declarative agent capability adapters for Flightdeck."""

from copy import deepcopy
from typing import Any, Dict, Mapping


ACTIONS = (
    "run_command",
    "edit_file",
    "spawn_worker",
    "open_preview",
    "request_approval",
    "report_result",
)


def _mechanism(invocation: str, evidence: str) -> Dict[str, str]:
    return {"invocation": invocation, "evidence": evidence}


_PROFILES: Mapping[str, Mapping[str, Mapping[str, str]]] = {
    "codex": {
        "run_command": _mechanism("exec_command", "exit code and captured output"),
        "edit_file": _mechanism("apply_patch", "resulting file diff"),
        "spawn_worker": _mechanism("spawn_agent", "worker status and final response"),
        "open_preview": _mechanism("open local preview", "preview URL or rendered artifact"),
        "request_approval": _mechanism("request user approval", "user approval event"),
        "report_result": _mechanism("final response", "delivered response"),
    },
    "claude-code": {
        "run_command": _mechanism("Bash", "exit code and captured output"),
        "edit_file": _mechanism("Edit", "resulting file diff"),
        "spawn_worker": _mechanism("Task", "worker status and final response"),
        "open_preview": _mechanism("open local preview", "preview URL or rendered artifact"),
        "request_approval": _mechanism("AskUserQuestion", "user approval event"),
        "report_result": _mechanism("final response", "delivered response"),
    },
    "cursor": {
        "run_command": _mechanism("terminal command", "exit code and captured output"),
        "edit_file": _mechanism("workspace edit", "resulting file diff"),
        "open_preview": _mechanism("open local preview", "preview URL or rendered artifact"),
        "request_approval": _mechanism("chat approval request", "user approval event"),
        "report_result": _mechanism("chat response", "delivered response"),
    },
}

_FALLBACKS: Mapping[str, Mapping[str, str]] = {
    "cursor": {
        "spawn_worker": "Run the work sequentially in the current agent; parallel worker execution is unavailable."
    }
}


def probe(name: str) -> Dict[str, Any]:
    """Return the complete, serializable capability profile for an agent."""
    if name not in _PROFILES:
        known = ", ".join(sorted(_PROFILES))
        raise ValueError(f"unknown agent {name!r}; expected one of: {known}")
    profile = _PROFILES[name]
    available = [action for action in ACTIONS if action in profile]
    unavailable = [action for action in ACTIONS if action not in profile]
    return {
        "agent": name,
        "available_capabilities": available,
        "unavailable_capabilities": unavailable,
        "fallbacks": deepcopy(dict(_FALLBACKS.get(name, {}))),
    }


def render(action: str, capabilities: Mapping[str, Any]) -> Dict[str, Any]:
    """Translate an action without ever presenting an unsupported action as success."""
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    if not isinstance(capabilities, Mapping):
        raise TypeError("capabilities must be the mapping returned by probe()")
    agent = capabilities.get("agent")
    if agent not in _PROFILES:
        raise ValueError("capabilities contain an unknown agent")

    mechanism = _PROFILES[agent].get(action)
    advertised = action in capabilities.get("available_capabilities", ())
    if mechanism is not None and advertised:
        return {
            "supported": True,
            "invocation": mechanism["invocation"],
            "evidence": mechanism["evidence"],
            "blocker": None,
        }

    fallback = capabilities.get("fallbacks", {}).get(action)
    blocker = fallback or f"{agent} does not provide capability {action}"
    return {
        "supported": False,
        "invocation": None,
        "evidence": None,
        "blocker": blocker,
    }


__all__ = ["ACTIONS", "probe", "render"]
