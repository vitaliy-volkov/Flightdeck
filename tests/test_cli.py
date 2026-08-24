import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class CliTest(unittest.TestCase):
    def run_cli(self, project, *arguments):
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(ROOT / "src")
        return subprocess.run(
            [sys.executable, "-m", "flightdeck", "--project", str(project), *arguments],
            text=True,
            capture_output=True,
            env=environment,
            check=False,
        )

    def test_init_resume_status_validate_and_export(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            initialized = self.run_cli(project, "init", "--mode", "full")
            self.assertEqual(0, initialized.returncode, initialized.stderr)
            state_path = project / ".flightdeck" / "state.json"
            self.assertTrue(state_path.exists())

            for command in ("resume", "status", "validate", "export"):
                result = self.run_cli(project, command)
                self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("preflight", json.loads(self.run_cli(project, "status").stdout)["phase"])
            self.assertEqual("valid", json.loads(self.run_cli(project, "validate").stdout)["status"])
            self.assertEqual(1, json.loads(self.run_cli(project, "export").stdout)["schema_version"])

    def test_validate_without_plugin_state_is_noop_success(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            self.assertEqual(0, self.run_cli(project, "init").returncode)
            result = self.run_cli(project, "validate")
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("not-configured", json.loads(result.stdout)["plugins"]["status"])

    def test_dry_run_does_not_change_files_and_corruption_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            dry = self.run_cli(project, "--dry-run", "init")
            self.assertEqual(0, dry.returncode, dry.stderr)
            self.assertFalse((project / ".flightdeck").exists())

            state_path = project / ".flightdeck" / "state.json"
            state_path.parent.mkdir()
            state_path.write_text("broken", encoding="utf-8")
            before = state_path.read_bytes()
            result = self.run_cli(project, "validate")
            self.assertEqual(2, result.returncode)
            self.assertIn(str(state_path), result.stderr)
            self.assertEqual(before, state_path.read_bytes())

    def test_mode_change_is_persisted_for_the_next_phase(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            self.assertEqual(0, self.run_cli(project, "init", "--mode", "semi").returncode)

            changed = self.run_cli(project, "mode", "--set", "full")
            self.assertEqual(0, changed.returncode, changed.stderr)
            output = json.loads(changed.stdout)
            self.assertEqual("semi", output["mode"])
            self.assertEqual("full", output["pending_mode"])

            status = json.loads(self.run_cli(project, "status").stdout)
            self.assertEqual("semi", status["mode"])
            self.assertEqual("full", status["pending_mode"])


if __name__ == "__main__":
    unittest.main()
