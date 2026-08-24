"""Versioned state and atomic persistence."""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .core import DEPTHS, MODES, PHASES


SCHEMA_VERSION = 1


class StateError(ValueError):
    pass


class CorruptState(StateError):
    pass


class UnsupportedSchema(StateError):
    pass


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
        if data.get("schema_version") != SCHEMA_VERSION:
            raise UnsupportedSchema(
                "%s: unsupported schema version %r (expected %s)"
                % (path, data.get("schema_version"), SCHEMA_VERSION)
            )
        try:
            return cls(data)
        except StateError as error:
            raise CorruptState("%s: %s" % (path, error)) from error

    def validate(self):
        required = {"schema_version", "phase", "status", "mode", "depth", "events"}
        missing = sorted(required.difference(self.data))
        if missing:
            raise StateError("missing fields: %s" % ", ".join(missing))
        if not isinstance(self.data["events"], list):
            raise StateError("events must be a list")
        if self.data["phase"] not in PHASES:
            raise StateError("unknown phase: %r" % self.data["phase"])
        if self.data["mode"] not in MODES:
            raise StateError("unknown mode: %r" % self.data["mode"])
        if self.data["depth"] not in DEPTHS:
            raise StateError("unknown depth: %r" % self.data["depth"])
        return True

    def apply(self, event):
        if not isinstance(event, dict) or not event.get("type"):
            raise StateError("event must be an object with type")
        recorded = dict(event)
        recorded.setdefault("at", datetime.now(timezone.utc).isoformat())
        self.data["events"].append(recorded)
        if event["type"] == "assumption_added":
            self.data.setdefault("assumptions", []).append(event["text"])
        elif event["type"] == "scope_deferred":
            self.data.setdefault("deferred", []).append(event["requirement_id"])
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
