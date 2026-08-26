"""Dependency-free local dashboard server and lifecycle helpers."""

import argparse
import hashlib
import json
import os
import secrets
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .core import PHASES
from .control_plane import ControlPlane
from .reporting import render as render_report
from .state import StateStore


class DashboardError(ValueError):
    pass


def _runtime_root(project):
    return Path(project).resolve() / ".flightdeck"


def _metadata_path(project):
    return _runtime_root(project) / "dashboard.json"


def _asset():
    html = (Path(__file__).parent / "assets" / "dashboard.html").read_text(encoding="utf-8")
    html = html.replace("</head>", '<link rel="stylesheet" href="/dashboard.css"></head>')
    html = html.replace("</body>", '<script src="/dashboard.js"></script></body>')
    return html.encode("utf-8")


def _static(name):
    return (Path(__file__).parent / "assets" / name).read_bytes()


def snapshot(project):
    project = Path(project).resolve()
    state_path = _runtime_root(project) / "state.json"
    state = json.loads(render_report(StateStore.load(state_path).data, "json"))
    phase_index = PHASES.index(state["phase"])
    state["dashboard"] = {
        "phases": list(PHASES),
        "phase_index": phase_index,
        "progress": round((phase_index + (1 if state["status"] == "complete" else 0)) / len(PHASES) * 100),
        "project": project.name,
        "updated_at": datetime.fromtimestamp(state_path.stat().st_mtime, timezone.utc).isoformat(),
    }
    manifest_path = _runtime_root(project) / "artifacts" / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        state["dashboard"]["requirements"] = manifest.get("requirements", [])
        state["dashboard"]["coverage"] = manifest.get("coverage", {})
    except (OSError, UnicodeError, json.JSONDecodeError):
        state["dashboard"]["requirements"] = []
        state["dashboard"]["coverage"] = {}
    try:
        state["dashboard"]["control_plane"] = ControlPlane.load(project).summary()
    except (OSError, ValueError):
        state["dashboard"]["control_plane"] = {"runs": []}
    return state


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, project, nonce=None):
        self.project = Path(project).resolve()
        self.nonce = nonce or secrets.token_urlsafe(24)
        super().__init__(address, DashboardHandler)

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


class DashboardHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        return

    def _send(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            self._send(200, "text/html; charset=utf-8", _asset())
            return
        if path == "/dashboard.css":
            self._send(200, "text/css; charset=utf-8", _static("dashboard.css"))
            return
        if path == "/dashboard.js":
            self._send(200, "text/javascript; charset=utf-8", _static("dashboard.js"))
            return
        if path == "/api/health":
            body = json.dumps({"ok": True, "pid": os.getpid(), "project": str(self.server.project), "nonce": self.server.nonce}).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", body)
            return
        if path == "/api/state":
            try:
                body = json.dumps(snapshot(self.server.project), ensure_ascii=False).encode("utf-8")
                self._send(200, "application/json; charset=utf-8", body)
            except (OSError, ValueError) as error:
                self._send(503, "application/json; charset=utf-8", json.dumps({"error": str(error)}).encode("utf-8"))
            return
        if path == "/events":
            self._events()
            return
        self._send(404, "application/json; charset=utf-8", b'{"error":"not found"}')

    def _events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        previous = None
        started = time.monotonic()
        while time.monotonic() - started < 300:
            try:
                payload = snapshot(self.server.project)
                digest = _digest(payload)
                if digest != previous:
                    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                    self.wfile.write(("event: state\ndata: %s\n\n" % encoded).encode("utf-8"))
                    previous = digest
                else:
                    self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            except (OSError, ValueError) as error:
                try:
                    encoded = json.dumps({"error": str(error)})
                    self.wfile.write(("event: error\ndata: %s\n\n" % encoded).encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
            time.sleep(1)


def _read_metadata(project):
    try:
        return json.loads(_metadata_path(project).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _health(url, timeout=0.35):
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None


def status(project):
    metadata = _read_metadata(project)
    if not isinstance(metadata, dict) or not metadata.get("url"):
        return {"status": "stopped"}
    parsed = urlsplit(metadata["url"])
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        return {"status": "stale", "url": metadata.get("url"), "pid": metadata.get("pid")}
    health = _health(metadata["url"])
    if (not health or health.get("project") != str(Path(project).resolve())
            or health.get("pid") != metadata.get("pid") or health.get("nonce") != metadata.get("nonce")):
        return {"status": "stale", "url": metadata.get("url"), "pid": metadata.get("pid")}
    return {"status": "running", "url": metadata["url"], "pid": health.get("pid")}


def serve(project, host="127.0.0.1", port=0):
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise DashboardError("dashboard must bind to a loopback address")
    project = Path(project).resolve()
    StateStore.load(_runtime_root(project) / "state.json")
    nonce = secrets.token_urlsafe(24)
    server = DashboardServer((host, port), project, nonce)
    actual_host, actual_port = server.server_address[:2]
    if actual_host == "0.0.0.0":
        actual_host = "127.0.0.1"
    url = "http://%s:%s" % (actual_host, actual_port)
    metadata = {"pid": os.getpid(), "url": url, "project": str(project), "nonce": nonce, "started_at": datetime.now(timezone.utc).isoformat()}
    path = _metadata_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        current = _read_metadata(project)
        if current and current.get("pid") == os.getpid():
            path.unlink(missing_ok=True)


def start(project, host="127.0.0.1", port=0, open_browser=True):
    current = status(project)
    if current["status"] == "running":
        if open_browser:
            webbrowser.open(current["url"])
        return {**current, "reused": True, "opened": bool(open_browser)}
    metadata_path = _metadata_path(project)
    metadata_path.unlink(missing_ok=True)
    source_root = str(Path(__file__).resolve().parents[1])
    environment = dict(os.environ)
    environment["PYTHONPATH"] = source_root + os.pathsep + environment.get("PYTHONPATH", "")
    command = [sys.executable, "-m", "flightdeck.dashboard", "serve", "--project", str(Path(project).resolve()), "--host", host, "--port", str(port)]
    with open(os.devnull, "rb") as stdin, open(os.devnull, "ab") as output:
        process = subprocess.Popen(command, stdin=stdin, stdout=output, stderr=output, env=environment, start_new_session=True)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        current = status(project)
        if current["status"] == "running":
            if open_browser:
                webbrowser.open(current["url"])
            process.returncode = 0  # Detached child is tracked through dashboard metadata.
            return {**current, "reused": False, "opened": bool(open_browser)}
        if process.poll() is not None:
            break
        time.sleep(0.05)
    raise DashboardError("dashboard server did not become ready")


def stop(project):
    current = status(project)
    if current["status"] != "running":
        _metadata_path(project).unlink(missing_ok=True)
        return {"status": "stopped", "was_running": False}
    os.kill(int(current["pid"]), signal.SIGTERM)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and _health(current["url"]):
        time.sleep(0.05)
    _metadata_path(project).unlink(missing_ok=True)
    return {"status": "stopped", "was_running": True}


def _parser():
    parser = argparse.ArgumentParser(prog="flightdeck-dashboard")
    commands = parser.add_subparsers(dest="command", required=True)
    serve_command = commands.add_parser("serve")
    serve_command.add_argument("--project", required=True)
    serve_command.add_argument("--host", default="127.0.0.1")
    serve_command.add_argument("--port", type=int, default=0)
    return parser


def main(argv=None):
    arguments = _parser().parse_args(argv)
    if arguments.command == "serve":
        serve(arguments.project, arguments.host, arguments.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
