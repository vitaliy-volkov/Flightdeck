import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from flightdeck.adapters import ACTIONS, probe, render


class AdapterContractTests(unittest.TestCase):
    def test_every_profile_maps_every_common_action(self):
        for agent in ("codex", "claude-code", "cursor"):
            with self.subTest(agent=agent):
                capabilities = probe(agent)
                self.assertEqual(agent, capabilities["agent"])
                self.assertEqual(
                    set(ACTIONS),
                    set(capabilities["available_capabilities"])
                    | set(capabilities["unavailable_capabilities"]),
                )
                self.assertEqual(
                    set(capabilities),
                    {
                        "agent",
                        "available_capabilities",
                        "unavailable_capabilities",
                        "fallbacks",
                    },
                )
                for action in ACTIONS:
                    rendered = render(action, capabilities)
                    self.assertEqual(
                        set(rendered),
                        {"supported", "invocation", "evidence", "blocker"},
                    )
                    self.assertIsInstance(rendered["supported"], bool)

    def test_supported_actions_supply_invocation_and_evidence(self):
        for agent in ("codex", "claude-code", "cursor"):
            capabilities = probe(agent)
            for action in capabilities["available_capabilities"]:
                with self.subTest(agent=agent, action=action):
                    rendered = render(action, capabilities)
                    self.assertTrue(rendered["supported"])
                    self.assertTrue(rendered["invocation"])
                    self.assertTrue(rendered["evidence"])
                    self.assertIsNone(rendered["blocker"])

    def test_unsupported_action_has_fallback_or_blocker_and_never_succeeds(self):
        capabilities = probe("cursor")
        rendered = render("spawn_worker", capabilities)

        self.assertFalse(rendered["supported"])
        self.assertIsNone(rendered["invocation"])
        self.assertIsNone(rendered["evidence"])
        self.assertIn("sequentially", rendered["blocker"])

    def test_claimed_capability_cannot_create_a_missing_mechanism(self):
        capabilities = probe("cursor")
        capabilities["available_capabilities"].append("spawn_worker")

        self.assertFalse(render("spawn_worker", capabilities)["supported"])

    def test_unknown_agent_and_action_are_rejected(self):
        with self.assertRaises(ValueError):
            probe("other")
        with self.assertRaises(ValueError):
            render("other", probe("codex"))

    def test_available_and_unavailable_are_disjoint_and_unavailable_never_succeeds(self):
        for agent in ("codex", "claude-code", "cursor"):
            capabilities = probe(agent)
            available = set(capabilities["available_capabilities"])
            unavailable = set(capabilities["unavailable_capabilities"])
            self.assertFalse(available & unavailable)
            for action in unavailable:
                self.assertFalse(render(action, capabilities)["supported"])


if __name__ == "__main__":
    unittest.main()
