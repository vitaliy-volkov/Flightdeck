"""Versioned state and atomic persistence."""

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .core import DEPTHS, MODES, PHASES
SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 1


class StateError(ValueError):
    pass


class CorruptState(StateError):
    pass


class UnsupportedSchema(StateError):
    pass


def _legacy_record(path, source, *, phase=None):
    record = {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "provenance": [{"source": source, "phase": phase}],
    }
    if phase is not None:
        record["created_phase"] = phase
    return record


def _migrate_v1(data, state_path):
    migrated = dict(data)
    root = Path(state_path).parent / "artifacts"
    integrity = {"brief": None, "additions": None, "acceptance": None}
    brief = root / "brief.md"
    additions = root / "brief-additions.md"
    acceptance = root / "acceptance.json"
    if brief.is_file():
        integrity["brief"] = _legacy_record(brief, "migration:v1")
    if additions.is_file():
        integrity["additions"] = _legacy_record(additions, "migration:v1")
    if acceptance.exists():
        raise StateError("legacy acceptance lacks trustworthy versioned provenance")
    migrated["artifact_integrity"] = integrity
    migrated["schema_version"] = SCHEMA_VERSION
    return migrated


class StateStore:
    def __init__(self, data):
        self.data = data
        self.validate()

    @classmethod
    def new(cls, mode="semi", depth="normal", polish=False):
        store = cls({
            "schema_version": SCHEMA_VERSION,
            "phase": "preflight",
            "status": "active",
            "mode": mode,
            "depth": depth,
            "polish": bool(polish),
            "gates": {},
            "approvals": [],
            "requirements": [],
            "assumptions": [],
            "deferred": [],
            "events": [],
            "artifact_integrity": {"brief": None, "additions": None, "acceptance": None},
        })
        if mode == "full":
            store.apply({
                "type": "automatic_decision",
                "text": "Remaining decisions run automatically in full mode",
            })
            store.data["assumptions"].append("Remaining decisions run automatically in full mode")
        return store

    @classmethod
    def load(cls, path):
        path = Path(path)
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CorruptState("%s: %s" % (path, error)) from error
        if not isinstance(data, dict):
            raise CorruptState("%s: state root must be an object" % path)
        if data.get("schema_version") == LEGACY_SCHEMA_VERSION:
            try:
                data = _migrate_v1(data, path)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
                raise CorruptState("%s: legacy migration failed: %s" % (path, error)) from error
        if data.get("schema_version") != SCHEMA_VERSION:
            raise UnsupportedSchema(
                "%s: unsupported schema version %r (expected %s)"
                % (path, data.get("schema_version"), SCHEMA_VERSION)
            )
        try:
            store = cls(data)
            store.verify_artifacts(path)
            return store
        except StateError as error:
            raise CorruptState("%s: %s" % (path, error)) from error

    def validate(self):
        required = {"schema_version", "phase", "status", "mode", "depth", "events", "artifact_integrity"}
        missing = sorted(required.difference(self.data))
        if missing:
            raise StateError("missing fields: %s" % ", ".join(missing))
        if not isinstance(self.data["events"], list):
            raise StateError("events must be a list")
        if self.data["phase"] not in PHASES:
            raise StateError("unknown phase: %r" % self.data["phase"])
        if self.data["mode"] not in MODES:
            raise StateError("unknown mode: %r" % self.data["mode"])
        if self.data.get("pending_mode") is not None and self.data["pending_mode"] not in MODES:
            raise StateError("unknown pending mode: %r" % self.data["pending_mode"])
        if self.data["depth"] not in DEPTHS:
            raise StateError("unknown depth: %r" % self.data["depth"])
        integrity = self.data["artifact_integrity"]
        if not isinstance(integrity, dict) or set(integrity) != {"brief", "additions", "acceptance"}:
            raise StateError("artifact_integrity schema is invalid")
        return True

    def verify_artifacts(self, state_path):
        root = Path(state_path).parent / "artifacts"
        integrity = self.data["artifact_integrity"]
        paths = {"brief": root / "brief.md", "additions": root / "brief-additions.md", "acceptance": root / "acceptance.json"}
        for kind, path in paths.items():
            record = integrity[kind]
            if record is None:
                if path.exists():
                    raise StateError("untracked %s artifact detected" % kind)
                continue
            if not isinstance(record, dict) or not path.is_file():
                raise StateError("%s artifact integrity record is invalid" % kind)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != record.get("sha256"):
                raise StateError("%s artifact integrity mismatch" % kind)
            if not isinstance(record.get("provenance"), list) or not record["provenance"]:
                raise StateError("%s artifact provenance is missing" % kind)
        acceptance = integrity["acceptance"]
        if acceptance is not None and acceptance.get("created_phase") != "acceptance":
            raise StateError("acceptance provenance phase is invalid")
        return True

    def apply(self, event):
        if not isinstance(event, dict) or not event.get("type"):
            raise StateError("event must be an object with type")
        if event["type"] == "mode_change_requested" and event.get("mode") not in MODES:
            raise StateError("unknown mode: %r" % event.get("mode"))
        recorded = dict(event)
        recorded.setdefault("at", datetime.now(timezone.utc).isoformat())
        self.data["events"].append(recorded)
        if event["type"] == "assumption_added":
            self.data.setdefault("assumptions", []).append(event["text"])
        elif event["type"] == "scope_deferred":
            self.data.setdefault("deferred", []).append(event["requirement_id"])
        elif event["type"] == "mode_change_requested":
            self.data["pending_mode"] = event["mode"]
        return self

    def save(self, path):
        self.validate()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".%s." % path.name, dir=str(path.parent))
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise


def load(path):
    return StateStore.load(path)
