import tempfile
import unittest
from pathlib import Path

from flightdeck.control_plane import ControlPlane, ControlPlaneError


class ControlPlaneTest(unittest.TestCase):
    def test_cross_agent_handoff_approval_and_evidence_are_portable(self):
        with tempfile.TemporaryDirectory() as directory:
            plane = ControlPlane(Path(directory))
            plane.start("CP-01", "Кросс-агентная поставка")
            plane.join("CP-01", "codex", "codex-thread")
            plane.join("CP-01", "claude-code", "claude-session")
            handoff = plane.handoff("CP-01", "codex", "claude-code", "Реализован ledger", "Провести review", "Нужна проверка security")
            approval = plane.request_approval("CP-01", "publish", "Опубликовать проверенный коммит", "EVD-001")
            evidence = plane.add_evidence("CP-01", "test", "passed", "61 тест пройден", "scripts/quick_validate.py")
            report = ControlPlane.load(directory).summary("CP-01")["runs"][0]
            self.assertEqual("HND-001", handoff["id"])
            self.assertEqual("pending", approval["status"])
            self.assertEqual("EVD-001", evidence["id"])
            self.assertEqual(2, report["telemetry"]["participants"])
            self.assertEqual(1, report["telemetry"]["pending_approvals"])
            self.assertIn("Кросс-агентная поставка", ControlPlane.load(directory).export("CP-01", "markdown"))

    def test_handoff_and_approval_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            plane = ControlPlane(directory)
            plane.start("CP-01", "Безопасная миссия")
            plane.join("CP-01", "codex")
            with self.assertRaises(ControlPlaneError):
                plane.handoff("CP-01", "codex", "claude-code", "Передать", "Продолжить")
            with self.assertRaises(ControlPlaneError):
                plane.request_approval("CP-01", "run_command", "Обойти gate")
            with self.assertRaises(ControlPlaneError):
                plane.add_evidence("CP-01", "unknown", "passed", "Не доказательство")


if __name__ == "__main__":
    unittest.main()
