import json
import os
import subprocess
import sys
import tempfile
import threading
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
            self.assertEqual(2, json.loads(self.run_cli(project, "export").stdout)["schema_version"])

    def test_validate_without_plugin_state_is_noop_success(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            self.assertEqual(0, self.run_cli(project, "init").returncode)
            result = self.run_cli(project, "validate")
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("not-configured", json.loads(result.stdout)["plugins"]["status"])

    def test_control_plane_cli_persists_a_cross_agent_run(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            self.assertEqual(0, self.run_cli(project, "init").returncode)
            self.assertEqual(0, self.run_cli(project, "control", "start", "--run", "CP-01", "--title", "Передача между агентами").returncode)
            self.assertEqual(0, self.run_cli(project, "control", "join", "--run", "CP-01", "--agent", "codex").returncode)
            self.assertEqual(0, self.run_cli(project, "control", "join", "--run", "CP-01", "--agent", "claude-code").returncode)
            handoff = self.run_cli(project, "control", "handoff", "--run", "CP-01", "--from-agent", "codex", "--to-agent", "claude-code", "--summary", "Состояние сохранено", "--next-action", "Продолжить review")
            self.assertEqual(0, handoff.returncode, handoff.stderr)
            status = json.loads(self.run_cli(project, "control", "status", "--run", "CP-01").stdout)
            self.assertEqual(1, status["runs"][0]["telemetry"]["handoffs"])

    def test_control_plane_serializes_concurrent_agent_joins(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            self.assertEqual(0, self.run_cli(project, "init").returncode)
            self.assertEqual(0, self.run_cli(project, "control", "start", "--run", "CP-01", "--title", "Общий run").returncode)
            commands = (("codex", "thread-a"), ("cursor", "thread-b"))
            results = []
            threads = [threading.Thread(target=lambda agent=agent, session=session: results.append(self.run_cli(project, "control", "join", "--run", "CP-01", "--agent", agent, "--session", session))) for agent, session in commands]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertTrue(all(result.returncode == 0 for result in results))
            status = json.loads(self.run_cli(project, "control", "status", "--run", "CP-01").stdout)
            self.assertEqual(2, status["runs"][0]["telemetry"]["participants"])

    def test_control_plane_dry_run_does_not_create_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            self.assertEqual(0, self.run_cli(project, "init").returncode)
            result = self.run_cli(project, "--dry-run", "control", "start", "--run", "CP-01", "--title", "Не сохранять")
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("dry-run", json.loads(result.stdout)["status"])
            self.assertFalse((project / ".flightdeck" / "control-plane.json").exists())

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

    def test_generic_event_is_not_a_public_cli(self):
        help_result = self.run_cli(Path("."), "--help")
        self.assertEqual(0, help_result.returncode)
        self.assertNotIn("event", help_result.stdout)
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            self.assertEqual(0, self.run_cli(project, "init").returncode)
            result = self.run_cli(project, "event", "--json", '{"type":"approval_granted","actor":"user"}')
            self.assertEqual(2, result.returncode)
            plugin_help = self.run_cli(project, "plugin", "dispatch", "--help")
            self.assertEqual(0, plugin_help.returncode)
            self.assertNotIn("--approval", plugin_help.stdout)

    def test_brief_is_immutable_and_additions_are_append_only(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); project = base / "project"
            first = base / "first.md"; first.write_text("First brief\n", encoding="utf-8")
            second = base / "second.md"; second.write_text("Replacement\n", encoding="utf-8")
            addition = base / "addition.md"; addition.write_text("New constraint\n", encoding="utf-8")
            self.assertEqual(0, self.run_cli(project, "init").returncode)
            self.assertEqual(0, self.run_cli(project, "artifact", "--kind", "brief", "--input", str(first)).returncode)
            rejected = self.run_cli(project, "artifact", "--kind", "brief", "--input", str(second))
            self.assertEqual(2, rejected.returncode)
            self.assertEqual("First brief\n", (project / ".flightdeck" / "artifacts" / "brief.md").read_text())
            self.assertEqual(0, self.run_cli(project, "artifact", "--kind", "addition", "--input", str(addition)).returncode)
            self.assertEqual("New constraint\n", (project / ".flightdeck" / "artifacts" / "brief-additions.md").read_text())

    def test_acceptance_cannot_be_predeclared(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); project = base / "project"
            report = base / "acceptance.json"; report.write_text('{"blind":true,"ok":true,"requirements":[]}', encoding="utf-8")
            self.assertEqual(0, self.run_cli(project, "init").returncode)
            result = self.run_cli(project, "artifact", "--kind", "acceptance", "--input", str(report))
            self.assertEqual(2, result.returncode)
            self.assertFalse((project / ".flightdeck" / "artifacts" / "acceptance.json").exists())

    def test_concurrent_brief_claim_and_additions_are_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); project = base / "project"
            sources = []
            for index in range(2):
                source = base / ("brief%d.md" % index); source.write_bytes(("brief-%d\n" % index).encode()); sources.append(source)
            self.assertEqual(0, self.run_cli(project, "init").returncode)
            results = []
            threads = [threading.Thread(target=lambda source=source: results.append(self.run_cli(project, "artifact", "--kind", "brief", "--input", str(source)))) for source in sources]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertEqual([0, 2], sorted(result.returncode for result in results))
            final = (project / ".flightdeck" / "artifacts" / "brief.md").read_bytes()
            self.assertIn(final, [source.read_bytes() for source in sources])

            additions = []
            for index in range(2):
                source = base / ("add%d.md" % index); source.write_bytes(("add-%d\n" % index).encode()); additions.append(source)
            results = []
            threads = [threading.Thread(target=lambda source=source: results.append(self.run_cli(project, "artifact", "--kind", "addition", "--input", str(source)))) for source in additions]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertEqual(
                [0, 0],
                sorted(result.returncode for result in results),
                [(result.returncode, result.stderr) for result in results],
            )
            combined = (project / ".flightdeck" / "artifacts" / "brief-additions.md").read_text()
            self.assertEqual(1, combined.count("add-0\n")); self.assertEqual(1, combined.count("add-1\n"))

    def test_state_boundary_detects_artifact_tampering_and_preseeded_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); project = base / "project"; source = base / "brief.md"
            source.write_text("original\n", encoding="utf-8")
            self.assertEqual(0, self.run_cli(project, "init").returncode)
            self.assertEqual(0, self.run_cli(project, "artifact", "--kind", "brief", "--input", str(source)).returncode)
            state = json.loads((project / ".flightdeck" / "state.json").read_text())
            self.assertIn("sha256", state["artifact_integrity"]["brief"])
            (project / ".flightdeck" / "artifacts" / "brief.md").write_text("tampered\n")
            self.assertEqual(2, self.run_cli(project, "status").returncode)

        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            self.assertEqual(0, self.run_cli(project, "init").returncode)
            root = project / ".flightdeck" / "artifacts"; root.mkdir()
            (root / "acceptance.json").write_text('{"blind":true,"ok":true,"requirements":[]}')
            self.assertEqual(2, self.run_cli(project, "export").returncode)


if __name__ == "__main__":
    unittest.main()
