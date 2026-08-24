"""Command-line interface for the Flightdeck runtime."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from .adapters import probe
from .core import PHASES, next_actions
from .plugins import PluginError, PluginManager
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
    commands.add_parser("validate")
    advance = commands.add_parser("advance")
    advance.add_argument("--evidence", default="cli-artifact-validator")
    event = commands.add_parser("event")
    event.add_argument("--json", required=True)
    artifact = commands.add_parser("artifact")
    artifact.add_argument("--kind", choices=("brief", "manifest", "spec", "plan", "review", "acceptance"), required=True)
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
    dispatch.add_argument("--approval")
    mode = commands.add_parser("mode")
    mode.add_argument("--set", dest="requested_mode", choices=("full", "semi", "interview", "manual"), required=True)
    export = commands.add_parser("export")
    export.add_argument("--output")
    return parser


_ARTIFACTS = {
    "brief": "brief.md", "manifest": "manifest.json", "spec": "spec.md",
    "plan": "plan.md", "review": "review.md", "acceptance": "acceptance.json",
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
        if not isinstance(acceptance, dict) or acceptance.get("blind") is not True or acceptance.get("ok") is not True:
            raise StateError("acceptance report must record blind=true and ok=true")
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
    try:
        store = StateStore.load(path)
    except (CorruptState, UnsupportedSchema, StateError) as error:
        print(str(error), file=sys.stderr)
        return 2

    if arguments.command in ("resume", "status"):
        _emit(_summary(store))
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
        if not arguments.dry_run: store.save(path)
        _emit({"status":"dry-run" if arguments.dry_run else "advanced", **_summary(store)})
    elif arguments.command == "event":
        event = json.loads(arguments.json)
        if event.get("type") in ("approval_granted", "requirement_removed", "request_action"):
            store.data = next_actions(store.data, event).state
        else:
            store.apply(event)
        if not arguments.dry_run: store.save(path)
        _emit({"status":"dry-run" if arguments.dry_run else "recorded", "event":event["type"]})
    elif arguments.command == "artifact":
        source = Path(arguments.input)
        content = source.read_bytes()
        target = _artifact_root(path) / _ARTIFACTS[arguments.kind]
        if not arguments.dry_run: _atomic_bytes(target, content)
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
            approval = json.loads(arguments.approval) if arguments.approval else None
            result = manager.dispatch(arguments.name, arguments.hook, json.loads(arguments.payload), requested_capabilities=arguments.capability, approval=approval)
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
        content = json.dumps(store.data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
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
    except (StateError, PluginError, OSError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 2
