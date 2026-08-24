import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from flightdeck.core import GateBlocked, MODES, PHASES, next_actions


class CoreTransitionsTest(unittest.TestCase):
    def test_gate_must_pass_before_phase_advances(self):
        state = {"phase": "manifest", "mode": "full", "gates": {}}

        with self.assertRaises(GateBlocked):
            next_actions(state, {"type": "advance"})

        result = next_actions(state, {"type": "gate_passed", "phase": "manifest", "evidence": {"validator": "manifest", "ok": True}})
        self.assertEqual("briefing", result.state["phase"])
        self.assertEqual(("create_briefing",), result.actions)

    def test_safety_gate_and_requirement_removal_require_user_approval(self):
        state = {
            "phase": "build",
            "mode": "full",
            "gates": {},
            "requirements": [{"id": "R01", "status": "in-spec"}],
        }
        with self.assertRaises(GateBlocked):
            next_actions(state, {"type": "request_action", "action": "publish"})
        with self.assertRaises(GateBlocked):
            next_actions(state, {"type": "requirement_removed", "id": "R01", "actor": "agent"})

        approved = next_actions(state, {"type": "requirement_removed", "id": "R01", "actor": "user"})
        self.assertEqual("dropped", approved.state["requirements"][0]["status"])

    def test_manual_mode_requires_approval_for_spec_and_plan(self):
        state = {"phase": "briefing", "mode": "manual", "gates": {}, "approvals": []}
        with self.assertRaises(GateBlocked):
            next_actions(state, {"type": "gate_passed", "phase": "briefing"})
        approved = next_actions(state, {"type": "approval_granted", "action": "phase:spec", "actor": "user"})
        result = next_actions(approved.state, {"type": "gate_passed", "phase": "briefing", "evidence": {"validator": "briefing", "ok": True}})
        self.assertEqual("spec", result.state["phase"])

    def test_every_phase_requires_matching_successful_validator_evidence(self):
        for phase in PHASES:
            with self.subTest(phase=phase):
                state = {"phase": phase, "mode": "full", "gates": {}}
                for evidence in (None, {"validator": phase, "ok": False}, {"validator": "other", "ok": True}):
                    with self.assertRaises(GateBlocked):
                        next_actions(state, {"type": "gate_passed", "phase": phase, "evidence": evidence})
                result = next_actions(state, {"type": "gate_passed", "phase": phase, "evidence": {"validator": phase, "ok": True}})
                expected = "complete" if phase == "acceptance" else PHASES[PHASES.index(phase) + 1]
                self.assertEqual(expected, result.state.get("status") if phase == "acceptance" else result.state["phase"])

    def test_every_mode_is_accepted_and_unknown_mode_is_rejected(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                state = {"phase": "preflight", "mode": mode, "gates": {}}
                result = next_actions(state, {"type": "gate_passed", "phase": "preflight", "evidence": {"validator": "preflight", "ok": True}})
                self.assertEqual("manifest", result.state["phase"])
        with self.assertRaises(ValueError):
            next_actions({"phase": "preflight", "mode": "magic"}, {"type": "advance"})

    def test_external_approval_must_come_from_user_and_is_consumed(self):
        state = {"phase": "build", "mode": "full", "gates": {}}
        with self.assertRaises(GateBlocked):
            next_actions(state, {"type": "approval_granted", "action": "publish", "actor": "agent"})
        approved = next_actions(state, {"type": "approval_granted", "action": "publish", "actor": "user"})
        performed = next_actions(approved.state, {"type": "request_action", "action": "publish"})
        self.assertNotIn("publish", performed.state["approvals"])
        with self.assertRaises(GateBlocked):
            next_actions(performed.state, {"type": "request_action", "action": "publish"})


if __name__ == "__main__":
    unittest.main()
