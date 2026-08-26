"""Portable cross-agent control plane kept separate from a single agent run."""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .core import SAFETY_ACTIONS
from .reporting import render as render_report


AGENTS = ("codex", "claude-code", "cursor")
EVIDENCE_KINDS = ("test", "review", "ci", "runtime", "release")
EVIDENCE_STATUSES = ("passed", "failed", "pending", "blocked")
SCHEMA_VERSION = 1


class ControlPlaneError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".%s." % path.name, dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ControlPlane:
    def __init__(self, project, data=None):
        self.project = Path(project).resolve()
        self.path = self.project / ".flightdeck" / "control-plane.json"
        self.data = data or {"schema_version": SCHEMA_VERSION, "runs": {}}
        self.validate()

    @classmethod
    def load(cls, project):
        project = Path(project).resolve()
        path = project / ".flightdeck" / "control-plane.json"
        if not path.exists():
            return cls(project)
        try:
            return cls(project, json.loads(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise ControlPlaneError("%s: invalid control-plane state: %s" % (path, error)) from error

    def validate(self):
        if self.data.get("schema_version") != SCHEMA_VERSION or not isinstance(self.data.get("runs"), dict):
            raise ControlPlaneError("control-plane schema is invalid")
        for run_id, run in self.data["runs"].items():
            if not isinstance(run_id, str) or not run_id or run.get("id") != run_id:
                raise ControlPlaneError("control-plane run identity is invalid")
            for key in ("title", "created_at", "participants", "handoffs", "approvals", "evidence"):
                if key not in run:
                    raise ControlPlaneError("control-plane run is missing %s" % key)
            if not all(isinstance(run[key], list) for key in ("participants", "handoffs", "approvals", "evidence")):
                raise ControlPlaneError("control-plane run collections are invalid")
        return True

    def save(self):
        self.validate(); _atomic_json(self.path, self.data)

    def _run(self, run_id):
        try:
            return self.data["runs"][run_id]
        except KeyError as error:
            raise ControlPlaneError("unknown control-plane run: %s" % run_id) from error

    def start(self, run_id, title):
        if not run_id or not title:
            raise ControlPlaneError("run id and title are required")
        if run_id in self.data["runs"]:
            raise ControlPlaneError("control-plane run already exists: %s" % run_id)
        run = {"id": run_id, "title": title, "created_at": _now(), "participants": [], "handoffs": [], "approvals": [], "evidence": []}
        self.data["runs"][run_id] = run; self.save(); return run

    def join(self, run_id, agent, session=None):
        if agent not in AGENTS:
            raise ControlPlaneError("unsupported agent: %s" % agent)
        run = self._run(run_id)
        record = {"agent": agent, "session": session or None, "joined_at": _now()}
        if any(item["agent"] == agent and item.get("session") == record["session"] for item in run["participants"]):
            raise ControlPlaneError("agent session is already registered")
        run["participants"].append(record); self.save(); return record

    def handoff(self, run_id, from_agent, to_agent, summary, next_action, risk=None):
        if from_agent not in AGENTS or to_agent not in AGENTS or from_agent == to_agent:
            raise ControlPlaneError("handoff requires two distinct supported agents")
        if not summary or not next_action:
            raise ControlPlaneError("handoff summary and next action are required")
        run = self._run(run_id)
        joined = {item["agent"] for item in run["participants"]}
        if not {from_agent, to_agent}.issubset(joined):
            raise ControlPlaneError("both handoff agents must join the run first")
        record = {"id": "HND-%03d" % (len(run["handoffs"]) + 1), "from": from_agent, "to": to_agent, "summary": summary, "next_action": next_action, "risk": risk or None, "at": _now()}
        run["handoffs"].append(record); self.save(); return record

    def request_approval(self, run_id, action, summary, evidence_ref=None):
        if action not in SAFETY_ACTIONS:
            raise ControlPlaneError("approval action is not an outward Flightdeck action: %s" % action)
        if not summary:
            raise ControlPlaneError("approval summary is required")
        run = self._run(run_id)
        record = {"id": "APR-%03d" % (len(run["approvals"]) + 1), "action": action, "summary": summary, "evidence_ref": evidence_ref or None, "status": "pending", "requested_at": _now()}
        run["approvals"].append(record); self.save(); return record

    def add_evidence(self, run_id, kind, status, summary, reference=None):
        if kind not in EVIDENCE_KINDS or status not in EVIDENCE_STATUSES or not summary:
            raise ControlPlaneError("evidence kind, status, and summary are required")
        run = self._run(run_id)
        record = {"id": "EVD-%03d" % (len(run["evidence"]) + 1), "kind": kind, "status": status, "summary": summary, "reference": reference or None, "at": _now()}
        run["evidence"].append(record); self.save(); return record

    def summary(self, run_id=None):
        runs = [self._run(run_id)] if run_id else list(self.data["runs"].values())
        return {"schema_version": SCHEMA_VERSION, "runs": [self._summary(run) for run in runs]}

    def _summary(self, run):
        evidence = run["evidence"]
        return {**run, "telemetry": {"participants": len(run["participants"]), "handoffs": len(run["handoffs"]), "pending_approvals": sum(item["status"] == "pending" for item in run["approvals"]), "evidence": len(evidence), "passed_evidence": sum(item["status"] == "passed" for item in evidence)}}

    def export(self, run_id, format="json"):
        report = self.summary(run_id)
        if format == "json":
            return render_report(report, "json")
        if format != "markdown":
            raise ControlPlaneError("unsupported control-plane export format: %s" % format)
        run = report["runs"][0]; telemetry = run["telemetry"]
        lines = ["# %s" % run["title"], "", "- Run: `%s`" % run["id"], "- Participants: %s" % telemetry["participants"], "- Handoffs: %s" % telemetry["handoffs"], "- Pending approvals: %s" % telemetry["pending_approvals"], "- Evidence: %s passed / %s total" % (telemetry["passed_evidence"], telemetry["evidence"])]
        return "\n".join(lines) + "\n"
