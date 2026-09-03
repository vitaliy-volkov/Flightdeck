#!/usr/bin/env python3
"""Copy the skill plus the canonical runtime into a standalone folder."""

import argparse
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "flightdeck"
RUNTIME = ROOT / "src" / "flightdeck"


def package_skill(destination):
    destination = Path(destination).resolve()
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(SKILL, destination, ignore=shutil.ignore_patterns("__pycache__", "src"))
    shutil.copytree(RUNTIME, destination / "src" / "flightdeck", ignore=shutil.ignore_patterns("__pycache__"))
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(prog="package_skill")
    parser.add_argument("destination")
    arguments = parser.parse_args(argv)
    if not RUNTIME.is_dir():
        raise SystemExit("canonical runtime missing: %s" % RUNTIME)
    path = package_skill(arguments.destination)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
