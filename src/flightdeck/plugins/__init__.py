"""Flightdeck plugin manifests, installation lifecycle, and hook isolation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

API_VERSION = "1.0"
PROTOCOL_VERSION = "1.0"
MANIFEST_NAME = "flightdeck.plugin.json"
CAPABILITIES = frozenset({
    "network", "shell", "files.read", "files.write", "memory",
    "external.read", "external.write",
})
HOOKS = frozenset({
    "before_phase", "after_phase", "before_gate", "after_gate",
    "on_blocked", "report_section",
})
IMMUTABLE_EVENTS = frozenset({
    "approval_granted", "approval_bypassed", "gate_passed", "gate_skipped",
    "skip_gate", "requirement_removed", "brief_changed", "brief_replaced",
})
OUTWARD_EVENTS = frozenset({
    "external_write", "deploy", "publish", "message", "delete", "payment",
    "rewrite_history",
})
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")


class PluginError(RuntimeError):
    """A plugin failed validation, resolution, or isolated execution."""


@dataclass(frozen=True)
class ResolvedPlugin:
    source: str
    path: Path
    source_type: str
    resolved_commit: Optional[str]
    tree_hash: str
    sha256: str


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PluginError("invalid JSON at %s: %s" % (path, exc)) from exc
    if not isinstance(value, dict):
        raise PluginError("JSON object required at %s" % path)
    return value


def _safe_relative(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise PluginError("%s must be a relative path" % field)
    parts = Path(value).parts
    if ".." in parts:
        raise PluginError("%s contains path traversal" % field)
    return value


def validate(manifest: Mapping[str, Any], *, agent: Optional[str] = None,
             api_version: str = API_VERSION, root: Optional[Path] = None) -> Dict[str, Any]:
    """Validate and normalize a v1 plugin manifest before any execution."""
    if not isinstance(manifest, Mapping):
        raise PluginError("manifest must be an object")
    required = {"name", "version", "api_version", "entrypoint", "hooks", "capabilities"}
    missing = sorted(required - set(manifest))
    if missing:
        raise PluginError("manifest missing: %s" % ", ".join(missing))
    name = manifest["name"]
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise PluginError("invalid plugin name")
    version = manifest["version"]
    if not isinstance(version, str) or not _SEMVER.fullmatch(version):
        raise PluginError("version must be semver")
    plugin_api = manifest["api_version"]
    if not isinstance(plugin_api, str) or plugin_api.split(".", 1)[0] != api_version.split(".", 1)[0]:
        raise PluginError("incompatible api_version %r (runtime %s)" % (plugin_api, api_version))
    entrypoint = _safe_relative(manifest["entrypoint"], "entrypoint")
    if Path(entrypoint).suffix != ".py":
        raise PluginError("entrypoint must be a Python file")
    hooks = manifest["hooks"]
    if not isinstance(hooks, list) or any(not isinstance(v, str) or v not in HOOKS for v in hooks):
        raise PluginError("hooks contain unsupported values")
    capabilities = manifest["capabilities"]
    if not isinstance(capabilities, list) or any(not isinstance(v, str) or v not in CAPABILITIES for v in capabilities):
        raise PluginError("capabilities contain unsupported values")
    agents = manifest.get("agents", manifest.get("agent_compatibility", ["*"]))
    if not isinstance(agents, list) or not agents or any(not isinstance(v, str) for v in agents):
        raise PluginError("agents must be a non-empty string list")
    if agent and "*" not in agents and agent not in agents:
        raise PluginError("plugin is incompatible with agent %s" % agent)
    normalized = dict(manifest)
    normalized.update(entrypoint=entrypoint, hooks=list(dict.fromkeys(hooks)),
                      capabilities=list(dict.fromkeys(capabilities)), agents=agents)
    if root is not None:
        base = root.resolve()
        target = (base / entrypoint).resolve()
        try:
            target.relative_to(base)
        except ValueError as exc:
            raise PluginError("entrypoint escapes plugin root") from exc
        if not target.is_file():
            raise PluginError("entrypoint does not exist: %s" % entrypoint)
    return normalized


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    files = []
    for path in root.rglob("*"):
        relative_parts = path.relative_to(root).parts
        if ".git" in relative_parts:
            continue
        if path.is_symlink():
            raise PluginError("plugin tree contains symlink: %s" % path.relative_to(root))
        if path.is_file():
            files.append(path)
        elif not path.is_dir():
            raise PluginError("plugin tree contains special file: %s" % path.relative_to(root))
    files.sort()
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big")); digest.update(relative)
        data = path.read_bytes()
        digest.update(len(data).to_bytes(8, "big")); digest.update(data)
    return digest.hexdigest()


def resolve(source: str, *, ref: Optional[str] = None) -> ResolvedPlugin:
    """Resolve a local folder or Git source into immutable source evidence."""
    local = Path(source).expanduser()
    if local.is_dir() and (local / MANIFEST_NAME).is_file():
        path = local.resolve()
        tree_hash = _tree_digest(path)
        return ResolvedPlugin(str(path), path, "local", None, tree_hash, tree_hash)
    if source.startswith("-") or (ref is not None and (ref.startswith("-") or "\0" in ref)):
        raise PluginError("Git source/ref cannot begin with an option")
    temp = Path(tempfile.mkdtemp(prefix="flightdeck-git-"))
    try:
        clone = subprocess.run(["git", "clone", "--quiet", "--no-checkout", "--", source, str(temp / "repo")],
                               text=True, capture_output=True, timeout=60)
        if clone.returncode:
            raise PluginError("git clone failed: %s" % (clone.stderr.strip() or "unknown error"))
        repo = temp / "repo"
        checkout = subprocess.run(["git", "-C", str(repo), "checkout", "--quiet", "--detach", ref or "HEAD", "--"],
                                  text=True, capture_output=True, timeout=30)
        if checkout.returncode:
            raise PluginError("git checkout failed: %s" % (checkout.stderr.strip() or "unknown error"))
        commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True,
                                capture_output=True, timeout=10, check=True).stdout.strip()
        tree_hash = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"], text=True,
                                   capture_output=True, timeout=10, check=True).stdout.strip()
        stable = Path(tempfile.mkdtemp(prefix="flightdeck-resolved-")) / "plugin"
        shutil.copytree(repo, stable, ignore=shutil.ignore_patterns(".git"))
        return ResolvedPlugin(source, stable, "git", commit, tree_hash, _tree_digest(stable))
    except (OSError, subprocess.SubprocessError) as exc:
        raise PluginError("git resolution failed: %s" % exc) from exc
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True); stream.write("\n")
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _lock_integrity(lock: Mapping[str, Any]) -> str:
    unsigned = dict(lock)
    unsigned.pop("integrity_sha256", None)
    payload = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class PluginManager:
    """Manage a project's locked plugins and dispatch isolated hooks."""

    def __init__(self, project: Path, *, agent: str, api_version: str = API_VERSION):
        self.project = Path(project).resolve()
        self.agent = agent
        self.api_version = api_version
        self.base = self.project / ".flightdeck" / "plugins"
        self.lock_path = self.project / ".flightdeck" / "plugins.lock.json"

    def _lock(self) -> Dict[str, Any]:
        if not self.lock_path.exists():
            return {"schema_version": 1, "plugins": {}, "removed": {}, "inactive": {}, "consumed_approvals": []}
        lock = _read_json(self.lock_path)
        if lock.get("schema_version") != 1 or not isinstance(lock.get("plugins"), dict):
            raise PluginError("invalid plugin lock schema")
        expected = lock.get("integrity_sha256")
        if not isinstance(expected, str) or expected != _lock_integrity(lock):
            raise PluginError("plugin lock integrity check failed")
        lock.setdefault("removed", {})
        lock.setdefault("inactive", {})
        lock.setdefault("consumed_approvals", [])
        return lock

    def _save(self, lock: Mapping[str, Any]) -> None:
        signed = dict(lock)
        signed["integrity_sha256"] = _lock_integrity(signed)
        _atomic_json(self.lock_path, signed)

    def list(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._lock()["plugins"])

    def list_inactive(self, name: Optional[str] = None) -> Any:
        inactive = self._lock()["inactive"]
        return list(inactive.get(name, [])) if name is not None else dict(inactive)

    def install(self, source: str, *, ref: Optional[str] = None, replace: bool = False) -> Dict[str, Any]:
        resolved = resolve(source, ref=ref)
        raw_manifest = _read_json(resolved.path / MANIFEST_NAME)
        name = raw_manifest.get("name")
        version = raw_manifest.get("version")
        if not isinstance(name, str) or not _NAME.fullmatch(name) or not isinstance(version, str) or not _SEMVER.fullmatch(version):
            raise PluginError("candidate has invalid name or version")
        lock = self._lock(); current = lock["plugins"].get(name)
        try:
            manifest = validate(raw_manifest, agent=self.agent, api_version=self.api_version, root=resolved.path)
            incompatibility = None
        except PluginError as exc:
            manifest = dict(raw_manifest); incompatibility = str(exc)
        identity = (resolved.source, resolved.resolved_commit, resolved.tree_hash, resolved.sha256, manifest["version"])
        if current and tuple(current.get(k) for k in ("source", "resolved_commit", "tree_hash", "sha256", "version")) == identity:
            return current
        cache = self.base / "cache" / manifest["name"] / resolved.sha256
        if not cache.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            staging = cache.with_name(cache.name + ".tmp-%s" % os.getpid())
            if staging.exists(): shutil.rmtree(staging)
            shutil.copytree(resolved.path, staging); os.replace(staging, cache)
        entry: Dict[str, Any] = {
            "name": manifest["name"], "version": manifest["version"], "api_version": manifest["api_version"],
            "source": resolved.source, "source_type": resolved.source_type, "ref": ref,
            "resolved_commit": resolved.resolved_commit, "tree_hash": resolved.tree_hash,
            "sha256": resolved.sha256, "cache_path": str(cache), "enabled": True,
            "manifest": manifest, "history": [], "granted_capabilities": [],
        }
        if incompatibility or (current and not replace):
            entry["enabled"] = False
            entry["diagnostic"] = incompatibility or "same-name candidate requires explicit replace"
            lock["inactive"].setdefault(name, []).append(entry)
            self._save(lock)
            if incompatibility:
                raise PluginError(incompatibility)
            raise PluginError("plugin name conflict; use replace=True")
        if current:
            previous = dict(current); previous.pop("history", None)
            entry["history"] = list(current.get("history", [])) + [previous]
        lock["plugins"][manifest["name"]] = entry; self._save(lock)
        return entry

    def update(self, name: str, *, to: Optional[str] = None, replace: bool = False) -> Dict[str, Any]:
        current = self._required(name)
        ref = to if to is not None else current.get("ref")
        return self.install(current["source"], ref=ref, replace=replace)

    def grant(self, name: str, capabilities: Iterable[str]) -> Dict[str, Any]:
        lock = self._lock(); entry = lock["plugins"].get(name)
        if entry is None: raise PluginError("plugin not installed: %s" % name)
        requested = set(capabilities)
        invalid = requested - CAPABILITIES
        undeclared = requested - set(entry["manifest"]["capabilities"])
        if invalid or undeclared:
            raise PluginError("invalid granted permissions: %s" % ", ".join(sorted(invalid | undeclared)))
        entry["granted_capabilities"] = sorted(requested); self._save(lock)
        return entry

    def disable(self, name: str) -> Dict[str, Any]:
        lock = self._lock(); entry = lock["plugins"].get(name)
        if entry is None: raise PluginError("plugin not installed: %s" % name)
        if entry.get("enabled"):
            entry["enabled"] = False; self._save(lock)
        return entry

    def remove(self, name: str) -> Optional[Dict[str, Any]]:
        lock = self._lock(); entry = lock["plugins"].pop(name, None)
        if entry is not None:
            lock["removed"][name] = entry; self._save(lock)
        return entry

    def restore(self, name: str, *, replace: bool = False) -> Dict[str, Any]:
        lock = self._lock(); removed = lock["removed"].get(name)
        if removed is None: raise PluginError("removed plugin not found: %s" % name)
        if name in lock["plugins"] and not replace:
            raise PluginError("plugin name conflict; use replace=True")
        if name in lock["plugins"]:
            lock["inactive"].setdefault(name, []).append(lock["plugins"][name])
        lock["plugins"][name] = removed; del lock["removed"][name]; self._save(lock)
        return removed

    def rollback(self, name: str) -> Dict[str, Any]:
        lock = self._lock(); current = lock["plugins"].get(name)
        if current is None:
            return self.restore(name)
        history = list(current.get("history", []))
        if not history: return current
        previous = history.pop(); previous["history"] = history
        lock["plugins"][name] = previous; self._save(lock)
        return previous

    def _required(self, name: str) -> Dict[str, Any]:
        entry = self._lock()["plugins"].get(name)
        if entry is None: raise PluginError("plugin not installed: %s" % name)
        return entry

    def doctor(self) -> Dict[str, Any]:
        checks: List[Dict[str, Any]] = []
        checks.append({"check": "python", "ok": sys.version_info >= (3, 11), "detail": sys.version.split()[0]})
        try:
            from flightdeck.adapters import probe
            profile = probe(self.agent)
            adapter_ok = profile.get("agent") == self.agent and bool(profile.get("available_capabilities"))
            checks.append({"check": "adapter", "ok": adapter_ok, "detail": self.agent})
        except (ImportError, ValueError, TypeError) as exc:
            checks.append({"check": "adapter", "ok": False, "detail": str(exc)})
        try:
            lock = self._lock(); checks.append({"check": "lock", "ok": True, "detail": str(self.lock_path)})
        except PluginError as exc:
            return {"ok": False, "checks": [{"check": "lock", "ok": False, "detail": str(exc)}]}
        for name, entry in lock["plugins"].items():
            try:
                root = Path(entry["cache_path"])
                validate(_read_json(root / MANIFEST_NAME), agent=self.agent, api_version=self.api_version, root=root)
                digest_ok = _tree_digest(root) == entry["sha256"]
                granted = entry.get("granted_capabilities", [])
                permission_ok = (isinstance(granted, list) and set(granted) <= CAPABILITIES and
                                 set(granted) <= set(entry["manifest"]["capabilities"]))
                ok = bool(entry.get("enabled")) and digest_ok and permission_ok
                if ok: detail = "ok"
                elif not entry.get("enabled"): detail = "disabled"
                elif not digest_ok: detail = "checksum mismatch"
                else: detail = "invalid granted permissions"
            except (KeyError, PluginError, OSError) as exc:
                ok, detail = False, str(exc)
            checks.append({"check": "plugin:%s" % name, "ok": ok, "detail": detail})
        return {"ok": all(item["ok"] for item in checks), "checks": checks}

    def dispatch(self, name: str, hook: str, payload: Mapping[str, Any], *, run_id: str = "run",
                 requested_capabilities: Optional[Iterable[str]] = None, timeout: float = 10.0,
                 approval: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        lock = self._lock(); entry = lock["plugins"].get(name)
        if entry is None: raise PluginError("plugin not installed: %s" % name)
        if not entry.get("enabled"): raise PluginError("plugin is disabled: %s" % name)
        manifest = validate(entry["manifest"], agent=self.agent, api_version=self.api_version,
                            root=Path(entry["cache_path"]))
        if hook not in manifest["hooks"]: raise PluginError("plugin does not declare hook %s" % hook)
        requested = set(requested_capabilities or ())
        unknown = requested - CAPABILITIES
        if unknown: raise PluginError("unknown requested capabilities: %s" % ", ".join(sorted(unknown)))
        configured = set(entry.get("granted_capabilities", []))
        if not configured <= set(manifest["capabilities"]) or not configured <= CAPABILITIES:
            raise PluginError("invalid granted permissions in lock")
        granted = sorted(requested.intersection(configured))
        needs_approval = "external.write" in granted
        approval_id = self._validate_approval(approval, name, hook, lock) if needs_approval else None
        approval_consumed = False
        if approval_id is not None:
            lock["consumed_approvals"].append(approval_id); self._save(lock)
            approval_consumed = True
        request = {"protocol": PROTOCOL_VERSION, "hook": hook, "run_id": run_id,
                   "payload": dict(payload), "granted_capabilities": granted}
        root = Path(entry["cache_path"])
        executable = root / manifest["entrypoint"]
        runner = Path(__file__).with_name("_runner.py")
        command = [sys.executable, "-I", "-B", str(runner), str(executable), str(root), json.dumps(granted)]
        environment = {"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
                       "FLIGHTDECK_PLUGIN": name, "FLIGHTDECK_RUN_ID": run_id}
        try:
            process = subprocess.run(command, input=json.dumps(request) + "\n", text=True,
                                     capture_output=True, timeout=timeout, cwd=entry["cache_path"], env=environment)
        except subprocess.TimeoutExpired as exc:
            raise PluginError("hook timed out after %s seconds" % timeout) from exc
        except OSError as exc:
            raise PluginError("hook could not start: %s" % exc) from exc
        if process.returncode:
            raise PluginError("hook exited %d: %s" % (process.returncode, process.stderr.strip()))
        lines = process.stdout.splitlines()
        if len(lines) != 1:
            raise PluginError("hook must emit exactly one JSONL response")
        try:
            response = json.loads(lines[0])
        except json.JSONDecodeError as exc:
            raise PluginError("hook emitted invalid JSON") from exc
        if not isinstance(response, dict) or set(response) != {"ok", "output", "events", "error"}:
            raise PluginError("hook response schema is invalid")
        if not isinstance(response["ok"], bool) or not isinstance(response["events"], list):
            raise PluginError("hook response schema is invalid")
        outward = False
        for event in response["events"]:
            if not isinstance(event, dict) or _contains_immutable_event_data(event):
                raise PluginError("hook attempted protected event")
            outward = outward or _contains_outward_event_data(event)
        if outward and approval_id is None:
            approval_id = self._validate_approval(approval, name, hook, lock)
        if not response["ok"]:
            raise PluginError("hook failed: %s" % (response["error"] or "no diagnostic"))
        if approval_id is not None and not approval_consumed:
            lock["consumed_approvals"].append(approval_id); self._save(lock)
        return response

    @staticmethod
    def _validate_approval(approval: Optional[Mapping[str, Any]], name: str, hook: str,
                           lock: Mapping[str, Any]) -> str:
        expected = "plugin:%s:%s" % (name, hook)
        if not isinstance(approval, Mapping) or approval.get("actor") != "user" or approval.get("action") != expected:
            raise PluginError("fresh actor=user approval required for %s" % expected)
        approval_id = approval.get("id")
        if not isinstance(approval_id, str) or not approval_id or approval_id in lock["consumed_approvals"]:
            raise PluginError("fresh actor=user approval required for %s" % expected)
        return approval_id


def _contains_immutable_event_data(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("type") in IMMUTABLE_EVENTS:
            return True
        for key, nested in value.items():
            lowered = str(key).lower()
            if "brief" in lowered or "gate" in lowered or "approval" in lowered:
                return True
            if _contains_immutable_event_data(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_immutable_event_data(item) for item in value)
    return False


def _contains_outward_event_data(value: Any) -> bool:
    if isinstance(value, dict):
        event_type = _normalize_event_name(value.get("type"))
        action = _normalize_event_name(value.get("action"))
        if event_type in OUTWARD_EVENTS or action in OUTWARD_EVENTS:
            return True
        return any(_contains_outward_event_data(nested) for nested in value.values())
    if isinstance(value, list):
        return any(_contains_outward_event_data(item) for item in value)
    return False


def _normalize_event_name(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    return re.sub(r"[.\s-]+", "_", value.strip().lower())


def dispatch(plugin: Mapping[str, Any], hook: str, payload: Mapping[str, Any], **kwargs: Any) -> Dict[str, Any]:
    """Dispatch using a lock entry; convenience seam for embedders."""
    manager = PluginManager(Path(plugin.get("project", ".")), agent=str(plugin.get("agent", "codex")))
    return manager.dispatch(str(plugin["name"]), hook, payload, **kwargs)


__all__ = ["API_VERSION", "CAPABILITIES", "HOOKS", "PluginError", "PluginManager",
           "ResolvedPlugin", "dispatch", "resolve", "validate"]
