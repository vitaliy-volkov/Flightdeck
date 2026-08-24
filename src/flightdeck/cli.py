"""Command-line interface for the Flightdeck runtime."""

import argparse
import json
import sys
from pathlib import Path

from .core import PHASES
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
    mode = commands.add_parser("mode")
    mode.add_argument("--set", dest="requested_mode", choices=("full", "semi", "interview", "manual"), required=True)
    export = commands.add_parser("export")
    export.add_argument("--output")
    return parser


def main(argv=None):
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
        _emit({"status": "valid", "path": str(path), "schema_version": store.data["schema_version"]})
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
