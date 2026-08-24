import json
import os
import subprocess
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

from flightdeck.adapters import ACTIONS, probe, render
FIXTURES = ROOT / "tests" / "fixtures"


class IntegrationAcceptanceTests(unittest.TestCase):
    def run_cli(self, project, *arguments, env=None):
        environment = dict(os.environ if env is None else env)
        environment["PYTHONPATH"] = str(ROOT / "src")
        return subprocess.run(
            [sys.executable, "-m", "flightdeck", "--project", str(project), *arguments],
            cwd=ROOT, env=environment, text=True, capture_output=True, check=False,
        )

    def test_e2e_init_all_phases_validate_export_resume(self):
        fixture = json.loads((FIXTURES / "canonical_run.json").read_text())
        for mode in ("full", "semi", "interview", "manual"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as td:
                base = Path(td); project = base / "project"; inputs = base / "inputs"; inputs.mkdir()
                self.assertEqual(0, self.run_cli(project, "init", "--mode", mode).returncode)
                for kind, value in fixture["artifacts"].items():
                    source = inputs / (kind + (".json" if isinstance(value, dict) else ".md"))
                    source.write_text(json.dumps(value) if isinstance(value, dict) else value, encoding="utf-8")
                    stored = self.run_cli(project, "artifact", "--kind", kind, "--input", str(source))
                    self.assertEqual(0, stored.returncode, stored.stderr)
                observed = []
                for phase in fixture["phases"]:
                    status = json.loads(self.run_cli(project, "status").stdout)
                    observed.append(status["phase"])
                    if mode == "manual" and phase in ("briefing", "spec"):
                        target = "spec" if phase == "briefing" else "plan"
                        event = json.dumps({"type":"approval_granted", "actor":"user", "action":"phase:" + target})
                        self.assertEqual(0, self.run_cli(project, "event", "--json", event).returncode)
                    advanced = self.run_cli(project, "advance")
                    self.assertEqual(0, advanced.returncode, advanced.stderr)
                self.assertEqual(fixture["phases"], observed)
                exported = project / "result" / "state.json"
                validated = self.run_cli(project, "validate")
                self.assertEqual(0, validated.returncode, validated.stderr)
                self.assertEqual(1, json.loads(validated.stdout)["artifacts"]["requirements"])
                self.assertEqual(0, self.run_cli(project, "export", "--output", str(exported)).returncode)
                self.assertEqual("complete", json.loads(exported.read_text())["status"])
                self.assertEqual("complete", json.loads(self.run_cli(project, "resume").stdout)["status"])

    def test_same_scenario_uses_all_adapter_profiles(self):
        expected = json.loads((FIXTURES / "adapter_profiles.json").read_text())
        for agent, expected_actions in expected.items():
            profile = probe(agent)
            for action in ACTIONS:
                rendered = render(action, profile)
                self.assertEqual(expected_actions[action][0], rendered["supported"])
                self.assertEqual(expected_actions[action][1], rendered["invocation"])

    def test_validate_rejects_missing_manifest_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); project = base / "project"
            brief = base / "brief.md"; brief.write_text("Brief\n")
            manifest = base / "manifest.json"
            manifest.write_text(json.dumps({"requirements":[{"id":"R01"}], "coverage":{}}))
            self.assertEqual(0, self.run_cli(project, "init").returncode)
            self.assertEqual(0, self.run_cli(project, "artifact", "--kind", "brief", "--input", str(brief)).returncode)
            self.assertEqual(0, self.run_cli(project, "artifact", "--kind", "manifest", "--input", str(manifest)).returncode)
            result = self.run_cli(project, "validate")
            self.assertEqual(2, result.returncode)
            self.assertIn("coverage", result.stderr)

    def test_example_plugin_install_dispatch_update_and_rollback(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); project = base / "project"; source = base / "plugin"
            shutil.copytree(ROOT / "examples" / "safe-plugin", source)
            self.assertEqual(0, self.run_cli(project, "init").returncode)
            installed = self.run_cli(project, "plugin", "install", str(source))
            self.assertEqual(0, installed.returncode, installed.stderr)
            response = self.run_cli(project, "plugin", "dispatch", "safe-report", "report_section", "--payload", '{"source":"e2e"}')
            self.assertEqual(0, response.returncode, response.stderr)
            self.assertEqual("run", json.loads(response.stdout)["output"]["run_id"])
            listed = self.run_cli(project, "plugin", "list")
            self.assertIn("safe-report", json.loads(listed.stdout))
            doctor = self.run_cli(project, "doctor", "--agent", "codex")
            report = json.loads(doctor.stdout)
            runtime_supported = sys.version_info >= (3, 11)
            self.assertEqual(0 if runtime_supported else 2, doctor.returncode, doctor.stderr)
            self.assertEqual(runtime_supported, report["ok"])
            checks = report["plugins"]["checks"]
            self.assertEqual(runtime_supported, next(item for item in checks if item["check"] == "python")["ok"])
            self.assertTrue(next(item for item in checks if item["check"] == "plugin:safe-report")["ok"])
            manifest_path = source / "flightdeck.plugin.json"
            manifest = json.loads(manifest_path.read_text()); manifest["version"] = "1.1.0"
            manifest_path.write_text(json.dumps(manifest))
            replaced = self.run_cli(project, "plugin", "install", str(source), "--replace")
            self.assertEqual("1.1.0", json.loads(replaced.stdout)["version"])
            rolled = self.run_cli(project, "plugin", "rollback", "safe-report")
            self.assertEqual(0, rolled.returncode, rolled.stderr)
            self.assertEqual("1.0.0", json.loads(rolled.stdout)["version"])

    def test_dry_run_export_and_clean_home_stdlib_smoke(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); project = base / "project"; home = base / "home"; home.mkdir()
            clean = {"HOME": str(home), "PATH": "/usr/bin:/bin", "PYTHONIOENCODING": "utf-8"}
            installed = home / ".agents" / "skills" / "flightdeck"
            shutil.copytree(ROOT / "skills" / "flightdeck", installed)
            shutil.copytree(ROOT / "src", installed / "src")
            entry = installed / "scripts" / "flightdeck.py"
            initialized = subprocess.run([sys.executable, str(entry), "--project", str(project), "init"], env=clean, text=True, capture_output=True)
            self.assertEqual(0, initialized.returncode, initialized.stderr)
            target = base / "never" / "state.json"
            result = self.run_cli(project, "--dry-run", "export", "--output", str(target), env=clean)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse(target.parent.exists())


if __name__ == "__main__":
    unittest.main()
