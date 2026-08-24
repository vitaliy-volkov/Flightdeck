"""Pure phase-transition rules for Flightdeck."""

from dataclasses import dataclass
from typing import Any, Dict, Tuple


PHASES = ("preflight", "manifest", "briefing", "spec", "plan", "build", "review", "acceptance")
MODES = ("full", "semi", "interview", "manual")
DEPTHS = ("strict", "normal", "deep")
SAFETY_ACTIONS = ("deploy", "publish", "pay", "message", "delete", "rewrite_history", "external_write")


class TransitionError(ValueError):
    pass


class GateBlocked(TransitionError):
    pass


@dataclass(frozen=True)
class TransitionResult:
    state: Dict[str, Any]
    actions: Tuple[str, ...]


def _copy_state(state):
    result = dict(state)
    result["gates"] = dict(state.get("gates", {}))
    result["approvals"] = list(state.get("approvals", []))
    result["requirements"] = [dict(item) for item in state.get("requirements", [])]
    return result


def next_actions(state, event):
    """Apply one event without mutating state and return requested next work."""
    phase = state.get("phase")
    if phase not in PHASES:
        raise TransitionError("unknown phase: %r" % phase)
    mode = state.get("mode", "semi")
    if mode not in MODES:
        raise TransitionError("unknown mode: %r" % mode)
    updated = _copy_state(state)
    event_type = event.get("type")

    if event_type == "request_action":
        action = event.get("action")
        if action in SAFETY_ACTIONS and action not in updated["approvals"]:
            raise GateBlocked("action requires explicit approval: %s" % action)
        if action in SAFETY_ACTIONS:
            updated["approvals"].remove(action)
        return TransitionResult(updated, ("perform_%s" % action,))

    if event_type == "approval_granted":
        if event.get("actor") != "user":
            raise GateBlocked("approval must come from the user")
        action = event.get("action")
        if action not in updated["approvals"]:
            updated["approvals"].append(action)
        return TransitionResult(updated, ())

    if event_type == "requirement_removed":
        if event.get("actor") != "user":
            raise GateBlocked("only the user may remove a requirement")
        requirement_id = event.get("id")
        for requirement in updated["requirements"]:
            if requirement.get("id") == requirement_id:
                requirement["status"] = "dropped"
                requirement["removed_by"] = "user"
                return TransitionResult(updated, ())
        raise TransitionError("unknown requirement: %r" % requirement_id)

    if event_type == "gate_passed":
        if event.get("phase") != phase:
            raise TransitionError("gate phase does not match current phase")
        evidence = event.get("evidence")
        if not isinstance(evidence, dict) or evidence.get("ok") is not True or evidence.get("validator") != phase:
            raise GateBlocked("verified validator evidence required for phase: %s" % phase)
        updated["gates"][phase] = True
    elif event_type != "advance":
        raise TransitionError("unknown event: %r" % event_type)

    if not updated["gates"].get(phase):
        raise GateBlocked("gate has not passed for phase: %s" % phase)
    if phase == "acceptance":
        updated["status"] = "complete"
        return TransitionResult(updated, ("report_result",))
    next_phase = PHASES[PHASES.index(phase) + 1]
    if mode == "manual" and next_phase in ("spec", "plan"):
        approval = "phase:%s" % next_phase
        if approval not in updated["approvals"]:
            raise GateBlocked("manual mode requires approval: %s" % next_phase)
        updated["approvals"].remove(approval)
    updated["phase"] = next_phase
    return TransitionResult(updated, ("create_%s" % next_phase,))
