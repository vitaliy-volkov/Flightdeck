import json
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from flightdeck.dashboard import DashboardServer, snapshot, start, status, stop
from flightdeck.state import StateStore


class DashboardTest(unittest.TestCase):
    def make_project(self, root):
        project = Path(root) / "demo"
        state = StateStore.new("full")
        state.apply({"type": "run_initialized"})
        state.save(project / ".flightdeck" / "state.json")
        artifacts = project / ".flightdeck" / "artifacts"
        artifacts.mkdir()
        (artifacts / "manifest.json").write_text(
            json.dumps({"requirements": [{"id": "R01", "status": "in-spec"}], "coverage": {"R01": ["spec"]}}),
            encoding="utf-8",
        )
        return project

    def test_snapshot_contains_computed_progress_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(directory)
            payload = snapshot(project)
            self.assertEqual("demo", payload["dashboard"]["project"])
            self.assertEqual(0, payload["dashboard"]["phase_index"])
            self.assertEqual("R01", payload["dashboard"]["requirements"][0]["id"])
            self.assertEqual(payload["dashboard"]["updated_at"], snapshot(project)["dashboard"]["updated_at"])
            state_path = project / ".flightdeck" / "state.json"
            state = StateStore.load(state_path)
            state.apply({"type": "artifact_stored", "kind": "spec", "phase": "preflight"})
            time.sleep(0.01)
            state.save(state_path)
            self.assertNotEqual(payload["dashboard"]["updated_at"], snapshot(project)["dashboard"]["updated_at"])

    def test_http_endpoints_are_fixed_read_only_and_sse_emits_state(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(directory)
            server = DashboardServer(("127.0.0.1", 0), project)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = "http://127.0.0.1:%s" % server.server_port
            try:
                with urllib.request.urlopen(base + "/") as response:
                    html = response.read().decode("utf-8")
                    self.assertIn("FLIGHTDECK", html)
                    self.assertIn("EventSource('/events')", html)
                    self.assertIn("flightdeck-language", html)
                    self.assertIn("flightdeck-theme", html)
                    self.assertIn("Центр управления", html)
                    self.assertIn('/dashboard.js', html)
                    self.assertEqual("no-store, max-age=0", response.headers["Cache-Control"])
                with urllib.request.urlopen(base + "/api/state") as response:
                    self.assertEqual("preflight", json.load(response)["phase"])
                with urllib.request.urlopen(base + "/dashboard.js") as response:
                    self.assertIn("live-changed", response.read().decode("utf-8"))
                with urllib.request.urlopen(base + "/events", timeout=3) as response:
                    lines = [response.readline().decode("utf-8") for _ in range(3)]
                    self.assertEqual("event: state\n", lines[0])
                    self.assertTrue(lines[1].startswith("data: {") and '"phase":"preflight"' in lines[1])
                with self.assertRaises(urllib.error.HTTPError) as rejected:
                    urllib.request.urlopen(base + "/README.md")
                self.assertEqual(404, rejected.exception.code)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_background_lifecycle_reuses_and_stops_server(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(directory)
            first = start(project, open_browser=False)
            try:
                self.assertEqual("running", first["status"])
                self.assertFalse(first["reused"])
                second = start(project, open_browser=False)
                self.assertTrue(second["reused"])
                self.assertEqual(first["pid"], second["pid"])
                self.assertEqual("running", status(project)["status"])
            finally:
                stopped = stop(project)
            self.assertTrue(stopped["was_running"])
            deadline = time.monotonic() + 2
            while status(project)["status"] != "stopped" and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual("stopped", status(project)["status"])


if __name__ == "__main__":
    unittest.main()
