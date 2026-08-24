"""Minimal audit-hook runner for untrusted Flightdeck Python plugins."""

import json
import os
import runpy
import sys
from pathlib import Path


def main():
    entrypoint = Path(sys.argv[1]).resolve()
    root = Path(sys.argv[2]).resolve()
    granted = frozenset(json.loads(sys.argv[3]))
    allowed_reads = (root, Path(sys.base_prefix).resolve())

    def within(path, parents):
        try:
            resolved = Path(path).resolve()
        except (OSError, TypeError, ValueError):
            return False
        return any(resolved == parent or parent in resolved.parents for parent in parents)

    def audit(event, args):
        if event.startswith("socket.") and "network" not in granted:
            raise PermissionError("Flightdeck denied network capability")
        process_events = ("subprocess.", "os.exec", "os.spawn", "os.fork", "pty.spawn", "ctypes.dlopen")
        if (event.startswith(process_events) or event in {"os.system", "os.posix_spawn"}) and "shell" not in granted:
            raise PermissionError("Flightdeck denied shell capability")
        write_events = {"os.remove", "os.rename", "os.replace", "os.rmdir", "os.mkdir", "os.link", "os.symlink", "shutil.copyfile"}
        if event in write_events and "files.write" not in granted:
            raise PermissionError("Flightdeck denied files.write capability")
        if event in {"os.listdir", "os.scandir"} and args and "files.read" not in granted and not within(args[0], allowed_reads):
            raise PermissionError("Flightdeck denied files.read capability")
        if event == "open" and args:
            mode = args[1] if len(args) > 1 else "r"
            flags = args[2] if len(args) > 2 else mode
            writing = (
                isinstance(mode, str) and any(flag in mode for flag in "wax+")
            ) or (
                isinstance(flags, int)
                and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC))
            )
            if writing and "files.write" not in granted:
                raise PermissionError("Flightdeck denied files.write capability")
            if not writing and "files.read" not in granted and not within(args[0], allowed_reads):
                raise PermissionError("Flightdeck denied files.read capability")

    os.chdir(root)
    sys.addaudithook(audit)
    runpy.run_path(str(entrypoint), run_name="__main__")


if __name__ == "__main__":
    main()
