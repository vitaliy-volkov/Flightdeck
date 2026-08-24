import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from flightdeck.reporting import render


class ReportingTests(unittest.TestCase):
    def test_json_export_and_acceptance_are_canonical(self):
        exported = json.loads(render({"status":"complete", "phase":"acceptance", "secret":"hidden"}, "json"))
        self.assertEqual("complete", exported["status"])
        self.assertNotIn("secret", exported)
        report = {"blind":True, "ok":True, "requirements":["R01"]}
        self.assertEqual(report, json.loads(render(report, "acceptance")))

    def test_invalid_acceptance_is_rejected(self):
        with self.assertRaises(ValueError):
            render({"blind":False, "ok":True, "requirements":[]}, "acceptance")
