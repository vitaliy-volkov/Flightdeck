"""Command-line interface for the Flightdeck runtime."""

import argparse
import contextlib
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised on non-POSIX Python
    fcntl = None

from .adapters import probe
from .core import PHASES, next_actions
from .control_plane import AGENTS, ControlPlane, ControlPlaneError
from .dashboard import DashboardError, start as start_dashboard, status as dashboard_status, stop as stop_dashboard
from .plugins import PluginError, PluginManager
from .reporting import render as render_report
from .state import CorruptState, StateError, StateStore, UnsupportedSchema


def _emit(value, stream=sys.stdout):
    print(json.dumps(value, ensure_ascii=False, sort_keys=True), file=stream)


def _state_path(arguments):
    return Path(arguments.project).resolve() / ".flightdeck" / "state.json"


def _summary(store):
    data = store.data
    phase = data["phase"]
    index = PHASES.index(phase)
    next_phase = PHASES[index + 1] if index + 1 < len(PHASES) else None
    return {
        "status": data["status"],
        "phase": phase,
        "next_phase": next_phase,
        "mode": data["mode"],
        "pending_mode": data.get("pending_mode"),
        "assumptions": data.get("assumptions", []),
        "deferred": data.get("deferred", []),
    }


def _parser():
    parser = argparse.ArgumentParser(prog="flightdeck")
    parser.add_argument("--project", default=".")
    parser.add_argument("--dry-run", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--mode", choices=("full", "semi", "interview", "manual"), default="semi")
    init.add_argument("--depth", choices=("strict", "normal", "deep"), default="normal")
    init.add_argument("--polish", action="store_true")
    commands.add_parser("resume")
    commands.add_parser("status")
    control = commands.add_parser("control")
    control_commands = control.add_subparsers(dest="control_command", required=True)
    control_start = control_commands.add_parser("start"); control_start.add_argument("--run", required=True); control_start.add_argument("--title", required=True)
    control_join = control_commands.add_parser("join"); control_join.add_argument("--run", required=True); control_join.add_argument("--agent", choices=AGENTS, required=True); control_join.add_argument("--session")
    control_handoff = control_commands.add_parser("handoff"); control_handoff.add_argument("--run", required=True); control_handoff.add_argument("--from-agent", choices=AGENTS, required=True); control_handoff.add_argument("--to-agent", choices=AGENTS, required=True); control_handoff.add_argument("--summary", required=True); control_handoff.add_argument("--next-action", required=True); control_handoff.add_argument("--risk")
    control_approval = control_commands.add_parser("approval-request"); control_approval.add_argument("--run", required=True); control_approval.add_argument("--action", required=True); control_approval.add_argument("--summary", required=True); control_approval.add_argument("--evidence-ref")
    control_evidence = control_commands.add_parser("evidence-add"); control_evidence.add_argument("--run", required=True); control_evidence.add_argument("--kind", required=True); control_evidence.add_argument("--status", required=True); control_evidence.add_argument("--summary", required=True); control_evidence.add_argument("--reference")
    control_status = control_commands.add_parser("status"); control_status.add_argument("--run")
    control_export = control_commands.add_parser("export"); control_export.add_argument("--run", required=True); control_export.add_argument("--format", choices=("json", "markdown"), default="json")
    dashboard = commands.add_parser("dashboard")
    dashboard_commands = dashboard.add_subparsers(dest="dashboard_command", required=True)
    dashboard_start = dashboard_commands.add_parser("start")
    dashboard_start.add_argument("--host", default="127.0.0.1")
    dashboard_start.add_argument("--port", type=int, default=0)
    dashboard_start.add_argument("--no-open", action="store_true")
    dashboard_commands.add_parser("status")
    dashboard_commands.add_parser("stop")
    commands.add_parser("validate")
    advance = commands.add_parser("advance")
    advance.add_argument("--evidence", default="cli-artifact-validator")
    artifact = commands.add_parser("artifact")
    artifact.add_argument("--kind", choices=("brief", "addition", "manifest", "spec", "plan", "review", "acceptance"), required=True)
    artifact.add_argument("--input", required=True)
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--agent", choices=("codex", "claude-code", "cursor"), default="codex")
    plugin = commands.add_parser("plugin")
    plugin.add_argument("--agent", choices=("codex", "claude-code", "cursor"), default="codex")
    plugin_commands = plugin.add_subparsers(dest="plugin_command", required=True)
    install = plugin_commands.add_parser("install"); install.add_argument("source"); install.add_argument("--replace", action="store_true")
    update = plugin_commands.add_parser("update"); update.add_argument("name"); update.add_argument("--to"); update.add_argument("--replace", action="store_true")
    plugin_commands.add_parser("list")
    rollback = plugin_commands.add_parser("rollback"); rollback.add_argument("name")
    grant = plugin_commands.add_parser("grant"); grant.add_argument("name"); grant.add_argument("capabilities", nargs="+")
    disable = plugin_commands.add_parser("disable"); disable.add_argument("name")
    remove = plugin_commands.add_parser("remove"); remove.add_argument("name")
    restore = plugin_commands.add_parser("restore"); restore.add_argument("name"); restore.add_argument("--replace", action="store_true")
    dispatch = plugin_commands.add_parser("dispatch"); dispatch.add_argument("name"); dispatch.add_argument("hook")
    dispatch.add_argument("--payload", default="{}"); dispatch.add_argument("--capability", action="append", default=[])
    mode = commands.add_parser("mode")
    mode.add_argument("--set", dest="requested_mode", choices=("full", "semi", "interview", "manual"), required=True)
    export = commands.add_parser("export")
    export.add_argument("--output")
    return parser


_ARTIFACTS = {
    "brief": "brief.md", "manifest": "manifest.json", "spec": "spec.md",
    "plan": "plan.md", "review": "review.md", "acceptance": "acceptance.json",
    "addition": "brief-additions.md",
}


def _atomic_bytes(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".%s." % path.name, dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _artifact_root(path):
    return path.parent / "artifacts"


@contextlib.contextmanager
def _artifact_lock(path, timeout=30.0, lock_name="artifact.lock"):
    lock_path = path.parent / lock_name
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    descriptor = None
    exclusive_file = fcntl is None
    if exclusive_file:
        while descriptor is None:
            try:
                descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise StateError("timed out waiting for artifact lock")
                time.sleep(0.01)
    else:
        descriptor = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(descriptor)
                    raise StateError("timed out waiting for artifact lock")
                time.sleep(0.01)
    try:
        os.ftruncate(descriptor, 0)
        os.write(descriptor, str(os.getpid()).encode("ascii")); os.fsync(descriptor)
        yield
    finally:
        if not exclusive_file:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
        if exclusive_file:
            try: os.unlink(lock_path)
            except FileNotFoundError: pass


def _artifact_record(content, source, *, phase=None, provenance=None):
    sources = list(provenance or [])
    sources.append({"source": str(source.resolve()), "phase": phase})
    record = {"sha256": hashlib.sha256(content).hexdigest(), "provenance": sources}
    if phase is not None:
        record["created_phase"] = phase
    return record


def _read_manifest(root):
    value = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    requirements = value.get("requirements") if isinstance(value, dict) else None
    coverage = value.get("coverage") if isinstance(value, dict) else None
    if not isinstance(requirements, list) or not requirements or not isinstance(coverage, dict):
        raise StateError("manifest requires non-empty requirements and coverage")
    identifiers = [item.get("id") for item in requirements if isinstance(item, dict)]
    if len(identifiers) != len(requirements) or any(not isinstance(item, str) or not item for item in identifiers):
        raise StateError("manifest requirement IDs are invalid")
    if len(set(identifiers)) != len(identifiers):
        raise StateError("manifest requirement IDs must be unique")
    if set(coverage) != set(identifiers) or any(not isinstance(coverage[item], list) or not coverage[item] for item in identifiers):
        raise StateError("manifest coverage must map every requirement ID")
    return identifiers, coverage


def _validate_artifacts(path, *, complete=False):
    root = _artifact_root(path)
    if not root.exists() and not complete:
        return {"artifacts": "not-started"}
    brief = root / "brief.md"
    if not brief.is_file() or not brief.read_text(encoding="utf-8").strip():
        raise StateError("brief artifact is missing or empty")
    identifiers, coverage = _read_manifest(root)
    for name in ("spec", "plan", "review"):
        artifact = root / (name + ".md")
        if complete and (not artifact.is_file() or not artifact.read_text(encoding="utf-8").strip()):
            raise StateError("%s artifact is missing or empty" % name)
    acceptance_path = root / "acceptance.json"
    if complete:
        try:
            acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise StateError("acceptance report is missing or invalid: %s" % error) from error
        try:
            acceptance = json.loads(render_report(acceptance, "acceptance"))
        except ValueError as error:
            raise StateError(str(error)) from error
        if set(acceptance.get("requirements", [])) != set(identifiers):
            raise StateError("acceptance report must cover every requirement ID")
        required = {"spec", "plan", "review", "acceptance"}
        if any(not required.issubset(set(coverage[item])) for item in identifiers):
            raise StateError("manifest coverage is incomplete")
    return {"artifacts": "complete" if complete else "valid", "requirements": len(identifiers)}


def _phase_evidence(path, phase):
    root = _artifact_root(path)
    if phase == "preflight": return True
    if phase in ("manifest", "briefing"):
        _validate_artifacts(path, complete=False); return True
    required = {"spec": "spec.md", "plan": "plan.md", "build": "plan.md", "review": "review.md"}
    if phase in required:
        artifact = root / required[phase]
        if not artifact.is_file() or not artifact.read_text(encoding="utf-8").strip():
            raise StateError("artifact required for phase %s" % phase)
        return True
    _validate_artifacts(path, complete=True)
    return True


def _main(argv=None):
    arguments = _parser().parse_args(argv)
    path = _state_path(arguments)
    if arguments.command == "init":
        if path.exists():
            print("state already exists: %s" % path, file=sys.stderr)
            return 2
        store = StateStore.new(arguments.mode, arguments.depth, arguments.polish)
        if not arguments.dry_run:
            store.apply({"type": "run_initialized"})
            store.save(path)
        _emit({"status": "dry-run" if arguments.dry_run else "initialized", "path": str(path)})
        return 0
    store = None
    if arguments.command != "artifact":
        try:
            store = StateStore.load(path)
        except (CorruptState, UnsupportedSchema, StateError) as error:
            print(str(error), file=sys.stderr)
            return 2

    if arguments.command in ("resume", "status"):
        _emit(_summary(store))
    elif arguments.command == "control":
        if arguments.dry_run and arguments.control_command in ("start", "join", "handoff", "approval-request", "evidence-add"):
            _emit({"status": "dry-run", "command": arguments.control_command, "run": arguments.run})
            return 0
        with _artifact_lock(path, lock_name="control-plane.lock"):
            control = ControlPlane.load(arguments.project)
            if arguments.control_command == "start": result = control.start(arguments.run, arguments.title)
            elif arguments.control_command == "join": result = control.join(arguments.run, arguments.agent, arguments.session)
            elif arguments.control_command == "handoff": result = control.handoff(arguments.run, arguments.from_agent, arguments.to_agent, arguments.summary, arguments.next_action, arguments.risk)
            elif arguments.control_command == "approval-request": result = control.request_approval(arguments.run, arguments.action, arguments.summary, arguments.evidence_ref)
            elif arguments.control_command == "evidence-add": result = control.add_evidence(arguments.run, arguments.kind, arguments.status, arguments.summary, arguments.reference)
            elif arguments.control_command == "status": result = control.summary(arguments.run)
            else:
                print(control.export(arguments.run, arguments.format), end=""); return 0
        _emit(result)
    elif arguments.command == "dashboard":
        if arguments.dashboard_command == "start":
            _emit(start_dashboard(arguments.project, arguments.host, arguments.port, not arguments.no_open))
        elif arguments.dashboard_command == "status":
            _emit(dashboard_status(arguments.project))
        else:
            _emit(stop_dashboard(arguments.project))
    elif arguments.command == "validate":
        store.validate()
        artifacts = _validate_artifacts(path, complete=store.data["status"] == "complete")
        manager = PluginManager(Path(arguments.project), agent="codex")
        plugins = manager.doctor() if manager.lock_path.exists() else {"ok": True, "checks": [], "status": "not-configured"}
        if not plugins["ok"]: raise StateError("plugin validation failed")
        _emit({"status": "valid", "path": str(path), "schema_version": store.data["schema_version"], "artifacts": artifacts, "plugins": plugins})
    elif arguments.command == "advance":
        phase = store.data["phase"]
        _phase_evidence(path, phase)
        store.data = next_actions(store.data, {"type":"gate_passed", "phase":phase, "evidence":{"validator":phase, "ok":True, "source":arguments.evidence}}).state
        store.apply({"type": "phase_advanced", "from_phase": phase, "phase": store.data["phase"], "source": arguments.evidence})
        if not arguments.dry_run: store.save(path)
        _emit({"status":"dry-run" if arguments.dry_run else "advanced", **_summary(store)})
    elif arguments.command == "artifact":
        source = Path(arguments.input)
        content = source.read_bytes()
        target = _artifact_root(path) / _ARTIFACTS[arguments.kind]
        if arguments.dry_run:
            StateStore.load(path)
        else:
            with _artifact_lock(path):
                store = StateStore.load(path)
                integrity = store.data["artifact_integrity"]
                if arguments.kind == "brief" and (target.exists() or integrity["brief"] is not None):
                    raise StateError("original brief is immutable; use kind=addition")
                if arguments.kind == "acceptance":
                    if store.data["phase"] != "acceptance" or not (_artifact_root(path) / "review.md").is_file():
                        raise StateError("acceptance may be recorded only after review in acceptance phase")
                    content = render_report(json.loads(content.decode("utf-8")), "acceptance").encode("utf-8")
                if arguments.kind == "addition" and target.exists():
                    content = target.read_bytes() + content
                _atomic_bytes(target, content)
                if arguments.kind in ("brief", "addition", "acceptance"):
                    previous = integrity["additions"] if arguments.kind == "addition" else None
                    provenance = previous.get("provenance", []) if previous else []
                    integrity["additions" if arguments.kind == "addition" else arguments.kind] = _artifact_record(
                        content, source, phase=store.data["phase"] if arguments.kind == "acceptance" else None,
                        provenance=provenance)
                store.apply({"type": "artifact_stored", "kind": arguments.kind, "phase": store.data["phase"], "source": str(source.resolve())})
                store.save(path)
        _emit({"status":"dry-run" if arguments.dry_run else "stored", "kind":arguments.kind, "path":str(target)})
    elif arguments.command == "doctor":
        adapter = probe(arguments.agent)
        plugins = PluginManager(Path(arguments.project), agent=arguments.agent).doctor()
        _emit({"ok":plugins["ok"], "adapter":adapter, "plugins":plugins})
        return 0 if plugins["ok"] else 2
    elif arguments.command == "plugin":
        manager = PluginManager(Path(arguments.project), agent=arguments.agent)
        if arguments.plugin_command == "install": result = manager.install(arguments.source, replace=arguments.replace)
        elif arguments.plugin_command == "update": result = manager.update(arguments.name, to=arguments.to, replace=arguments.replace)
        elif arguments.plugin_command == "list": result = manager.list()
        elif arguments.plugin_command == "rollback": result = manager.rollback(arguments.name)
        elif arguments.plugin_command == "grant": result = manager.grant(arguments.name, arguments.capabilities)
        elif arguments.plugin_command == "disable": result = manager.disable(arguments.name)
        elif arguments.plugin_command == "remove": result = manager.remove(arguments.name)
        elif arguments.plugin_command == "restore": result = manager.restore(arguments.name, replace=arguments.replace)
        else:
            result = manager.dispatch(arguments.name, arguments.hook, json.loads(arguments.payload), requested_capabilities=arguments.capability)
        _emit(result)
    elif arguments.command == "mode":
        store.apply({"type": "mode_change_requested", "mode": arguments.requested_mode})
        if not arguments.dry_run:
            store.save(path)
        _emit({
            "status": "dry-run" if arguments.dry_run else "scheduled",
            "mode": store.data["mode"],
            "pending_mode": store.data["pending_mode"],
        })
    elif arguments.command == "export":
        content = render_report(store.data, "json")
        if arguments.output:
            output = Path(arguments.output)
            if not arguments.dry_run:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(content, encoding="utf-8")
            _emit({"status": "dry-run" if arguments.dry_run else "exported", "path": str(output)})
        else:
            print(content, end="")
    return 0


def main(argv=None):
    try:
        return _main(argv)
    except (StateError, PluginError, DashboardError, ControlPlaneError, OSError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 2
