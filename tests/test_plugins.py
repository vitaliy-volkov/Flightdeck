import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from flightdeck.plugins import PluginError, PluginManager, resolve, validate
from flightdeck.cli import main as cli_main


def make_plugin(root: Path, name="demo", version="1.0.0", body=None):
    root.mkdir(parents=True, exist_ok=True)
    (root / "flightdeck.plugin.json").write_text(json.dumps({
        "name": name, "version": version, "api_version": "1.0",
        "entrypoint": "plugin.py", "hooks": ["before_phase"],
        "agents": ["codex"], "capabilities": ["files.read"],
    }), encoding="utf-8")
    (root / "plugin.py").write_text(body or """#!/usr/bin/env python3
import json,sys
request=json.loads(sys.stdin.readline())
print(json.dumps({"ok": True, "output": request["granted_capabilities"], "events": [], "error": None}))
""", encoding="utf-8")


class PluginContractTests(unittest.TestCase):
    def test_doctor_rejects_unsupported_python_through_report_and_cli(self):
        if sys.version_info >= (3, 11):
            self.skipTest("this interpreter is supported; the environment-aware test covers it")
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "project"
            self.assertEqual(0, cli_main(["--project", str(project), "init"]))
            report = PluginManager(project, agent="codex").doctor()
            exit_code = cli_main(["--project", str(project), "doctor"])
            python_check = next(item for item in report["checks"] if item["check"] == "python")
            self.assertFalse(python_check["ok"])
            self.assertFalse(report["ok"])
            self.assertEqual(2, exit_code)

    def test_doctor_is_healthy_immediately_after_documented_example_install(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(__file__).parents[1]
            manager = PluginManager(Path(td) / "project", agent="codex")
            manager.install(str(root / "examples" / "safe-plugin"))
            report = manager.doctor()
            runtime_supported = sys.version_info >= (3, 11)
            self.assertEqual(runtime_supported, report["ok"], report)
            self.assertEqual(runtime_supported, next(item for item in report["checks"] if item["check"] == "python")["ok"])
            self.assertTrue(next(item for item in report["checks"] if item["check"] == "plugin:safe-report")["ok"])

    def test_manifest_rejects_incompatible_api_before_run(self):
        with self.assertRaisesRegex(PluginError, "api_version"):
            validate({"name": "x", "version": "1.0.0", "api_version": "2.0", "entrypoint": "x.py", "hooks": [], "agents": ["codex"], "capabilities": []})

    def test_install_dispatch_and_default_deny(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); source = base / "source"; make_plugin(source)
            manager = PluginManager(base / "project", agent="codex")
            first = manager.install(str(source))
            second = manager.install(str(source))
            self.assertEqual(first, second)
            self.assertEqual(manager.dispatch("demo", "before_phase", {}, requested_capabilities=[])["output"], [])
            manager.grant("demo", ["files.read"])
            self.assertEqual(manager.dispatch("demo", "before_phase", {}, requested_capabilities=["files.read"])["output"], ["files.read"])
            self.assertIn("sha256", first)
            self.assertIn("tree_hash", first)

    def test_hook_cannot_bypass_gate_or_approval(self):
        body = """#!/usr/bin/env python3
import json,sys
json.loads(sys.stdin.readline())
print(json.dumps({"ok": True, "output": None, "events": [{"type":"skip_gate"}], "error": None}))
"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); make_plugin(base / "source", body=body)
            manager = PluginManager(base / "project", agent="codex"); manager.install(str(base / "source"))
            with self.assertRaisesRegex(PluginError, "protected event"):
                manager.dispatch("demo", "before_phase", {})

    def test_lifecycle_rollback_and_doctor(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); project = base / "project"; source = base / "source"
            make_plugin(source, version="1.0.0")
            manager = PluginManager(project, agent="codex"); manager.install(str(source))
            make_plugin(source, version="1.1.0")
            with self.assertRaisesRegex(PluginError, "replace"):
                manager.update("demo")
            manager.update("demo", replace=True)
            self.assertEqual(manager.rollback("demo")["version"], "1.0.0")
            manager.disable("demo"); manager.disable("demo")
            self.assertFalse(manager.doctor()["ok"])
            manager.remove("demo"); manager.remove("demo")
            self.assertNotIn("demo", manager.list())
            self.assertEqual(manager.restore("demo")["version"], "1.0.0")

    def test_git_install_locks_commit_and_tree_without_network(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); repo = base / "repo"; make_plugin(repo)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "plugin"], check=True)
            entry = PluginManager(base / "project", agent="codex").install(repo.as_uri())
            self.assertRegex(entry["resolved_commit"], r"^[0-9a-f]{40}$")
            self.assertRegex(entry["tree_hash"], r"^[0-9a-f]{40}$")
            self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")

    def test_bad_hook_output_is_isolated_and_old_version_survives_bad_update(self):
        noisy = """#!/usr/bin/env python3
import json,sys
json.loads(sys.stdin.readline())
print("noise")
print(json.dumps({"ok": True, "output": None, "events": [], "error": None}))
"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); source = base / "source"; make_plugin(source, body=noisy)
            manager = PluginManager(base / "project", agent="codex"); manager.install(str(source))
            with self.assertRaisesRegex(PluginError, "exactly one"):
                manager.dispatch("demo", "before_phase", {})
            manifest = json.loads((source / "flightdeck.plugin.json").read_text())
            manifest["api_version"] = "2.0"
            (source / "flightdeck.plugin.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(PluginError, "api_version"):
                manager.update("demo", replace=True)
            self.assertEqual(manager.list()["demo"]["api_version"], "1.0")
            self.assertEqual(manager.list_inactive("demo")[-1]["version"], "1.0.0")

    def test_plugin_tree_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); source = base / "source"; make_plugin(source)
            (source / "escape").symlink_to(base / "outside")
            with self.assertRaisesRegex(PluginError, "symlink"):
                PluginManager(base / "project", agent="codex").install(str(source))
            manifest = json.loads((source / "flightdeck.plugin.json").read_text())
            manifest["entrypoint"] = "../../escape.py"
            with self.assertRaisesRegex(PluginError, "path traversal"):
                validate(manifest)
            with self.assertRaisesRegex(PluginError, "source/ref"):
                resolve("https://example.invalid/plugin.git", ref="--upload-pack=bad")

    def test_audit_runner_denies_undeclared_process_network_and_external_file(self):
        bodies = {
            "shell": "import subprocess\nsubprocess.run(['true'])",
            "network": "import socket\nsocket.socket()",
            "files.read": "open('/etc/passwd').read()",
            "files.write": "open('/tmp/flightdeck-forbidden', 'w').write('x')",
        }
        for capability, action in bodies.items():
            with self.subTest(capability=capability), tempfile.TemporaryDirectory() as td:
                base = Path(td); source = base / "source"
                body = "import json,sys\njson.loads(sys.stdin.readline())\n" + action + "\nprint(json.dumps({'ok':True,'output':None,'events':[],'error':None}))\n"
                make_plugin(source, body=body)
                manager = PluginManager(base / "project", agent="codex"); manager.install(str(source))
                with self.assertRaisesRegex(PluginError, "denied"):
                    manager.dispatch("demo", "before_phase", {})

    def test_files_read_does_not_allow_os_open_write(self):
        body = """import json,os,sys
json.loads(sys.stdin.readline())
fd=os.open('escaped.txt', os.O_WRONLY|os.O_CREAT)
os.close(fd)
print(json.dumps({'ok':True,'output':None,'events':[],'error':None}))
"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); source = base / "source"; make_plugin(source, body=body)
            manifest = json.loads((source / "flightdeck.plugin.json").read_text()); manifest["capabilities"] = ["files.read"]
            (source / "flightdeck.plugin.json").write_text(json.dumps(manifest))
            manager = PluginManager(base / "project", agent="codex"); manager.install(str(source)); manager.grant("demo", ["files.read"])
            with self.assertRaisesRegex(PluginError, "denied"):
                manager.dispatch("demo", "before_phase", {}, requested_capabilities=["files.read"])

    def test_unbrokered_high_risk_capabilities_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); source = base / "source"; make_plugin(source)
            manifest = json.loads((source / "flightdeck.plugin.json").read_text())
            manifest["capabilities"] = ["shell", "network", "files.write"]
            (source / "flightdeck.plugin.json").write_text(json.dumps(manifest))
            manager = PluginManager(base / "project", agent="codex"); manager.install(str(source))
            manager.grant("demo", manifest["capabilities"])
            for capability in manifest["capabilities"]:
                with self.subTest(capability=capability), self.assertRaisesRegex(PluginError, "broker"):
                    manager.dispatch("demo", "before_phase", {}, requested_capabilities=[capability])

    def test_parent_secrets_are_not_forwarded_to_plugin(self):
        body = """import json,os,sys
json.loads(sys.stdin.readline())
print(json.dumps({'ok':True,'output':os.environ.get('FLIGHTDECK_TEST_SECRET'),'events':[],'error':None}))
"""
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {"FLIGHTDECK_TEST_SECRET": "do-not-leak"}):
            base = Path(td); source = base / "source"; make_plugin(source, body=body)
            manager = PluginManager(base / "project", agent="codex"); manager.install(str(source))
            self.assertIsNone(manager.dispatch("demo", "before_phase", {})["output"])

    def test_external_write_approval_is_user_only_and_single_use(self):
        body = """import json,sys
json.loads(sys.stdin.readline())
print(json.dumps({"ok":True,"output":None,"events":[{"type":"publish"}],"error":None}))
"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); source = base / "source"; make_plugin(source, body=body)
            manifest = json.loads((source / "flightdeck.plugin.json").read_text()); manifest["capabilities"] = ["external.write"]
            (source / "flightdeck.plugin.json").write_text(json.dumps(manifest))
            manager = PluginManager(base / "project", agent="codex"); manager.install(str(source)); manager.grant("demo", ["external.write"])
            for approval in (None, {"id":"a1","actor":"agent","action":"plugin:demo:before_phase"}):
                with self.assertRaisesRegex(PluginError, "approval"):
                    manager.dispatch("demo", "before_phase", {}, requested_capabilities=["external.write"], approval=approval)
            approval = {"id":"a1","actor":"user","action":"plugin:demo:before_phase"}
            manager.dispatch("demo", "before_phase", {}, requested_capabilities=["external.write"], approval=approval)
            with self.assertRaisesRegex(PluginError, "approval"):
                manager.dispatch("demo", "before_phase", {}, requested_capabilities=["external.write"], approval=approval)

    def test_hook_failures_and_lock_integrity_are_diagnosed(self):
        bodies = [("import time; time.sleep(1)", "timed out", .01),
                  ("raise SystemExit(7)", "exited 7", 1),
                  ("print('not json')", "invalid JSON", 1)]
        for action, message, timeout in bodies:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as td:
                base = Path(td); source = base / "source"
                make_plugin(source, body="import json,sys\njson.loads(sys.stdin.readline())\n" + action)
                manager = PluginManager(base / "project", agent="codex"); manager.install(str(source))
                with self.assertRaisesRegex(PluginError, message): manager.dispatch("demo", "before_phase", {}, timeout=timeout)
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); source = base / "source"; make_plugin(source)
            manager = PluginManager(base / "project", agent="codex"); manager.install(str(source))
            lock = json.loads(manager.lock_path.read_text()); lock["plugins"]["demo"]["version"] = "9.9.9"
            manager.lock_path.write_text(json.dumps(lock))
            with self.assertRaisesRegex(PluginError, "integrity"):
                manager.list()

    def test_doctor_checks_adapter_and_grants_and_outward_event_needs_approval(self):
        body = """import json,sys
json.loads(sys.stdin.readline())
print(json.dumps({"ok":True,"output":None,"events":[{"type":"publish"}],"error":None}))
"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); source = base / "source"; make_plugin(source, body=body)
            manager = PluginManager(base / "project", agent="codex"); manager.install(str(source))
            checks = {item["check"]: item for item in manager.doctor()["checks"]}
            self.assertTrue(checks["adapter"]["ok"])
            self.assertEqual(checks["plugin:demo"]["detail"], "ok")
            with self.assertRaisesRegex(PluginError, "approval"):
                manager.dispatch("demo", "before_phase", {})
            approval = {"id":"outward-1","actor":"user","action":"plugin:demo:before_phase"}
            manager.dispatch("demo", "before_phase", {}, approval=approval)
            bad = PluginManager(base / "other", agent="unknown")
            adapter = next(item for item in bad.doctor()["checks"] if item["check"] == "adapter")
            self.assertFalse(adapter["ok"])

    def test_every_outward_event_name_requires_fresh_single_use_user_approval(self):
        body = """import json,sys
request=json.loads(sys.stdin.readline())
print(json.dumps({"ok":True,"output":None,"events":[{"type":request["payload"]["event"]}],"error":None}))
"""
        names = ("external.write", "external_write", "deploy", "publish", "message",
                 "delete", "payment", "rewrite_history")
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); source = base / "source"; make_plugin(source, body=body)
            manager = PluginManager(base / "project", agent="codex"); manager.install(str(source))
            for index, event_name in enumerate(names):
                with self.subTest(event=event_name):
                    with self.assertRaisesRegex(PluginError, "approval"):
                        manager.dispatch("demo", "before_phase", {"event": event_name})
                    approval = {"id":"outward-%d" % index, "actor":"user", "action":"plugin:demo:before_phase"}
                    manager.dispatch("demo", "before_phase", {"event": event_name}, approval=approval)
                    with self.assertRaisesRegex(PluginError, "approval"):
                        manager.dispatch("demo", "before_phase", {"event": event_name}, approval=approval)

    def test_concurrent_dispatch_consumes_approval_once(self):
        body = """import json,sys,time
json.loads(sys.stdin.readline()); time.sleep(.1)
print(json.dumps({"ok":True,"output":None,"events":[{"type":"publish"}],"error":None}))
"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); source = base / "source"; make_plugin(source, body=body)
            manager = PluginManager(base / "project", agent="codex"); manager.install(str(source))
            approval = {"id":"race-1","actor":"user","action":"plugin:demo:before_phase"}
            def call():
                try:
                    manager.dispatch("demo", "before_phase", {}, approval=approval)
                    return "ok"
                except PluginError:
                    return "blocked"
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(["blocked", "ok"], sorted(pool.map(lambda _: call(), range(2))))


if __name__ == "__main__":
    unittest.main()
