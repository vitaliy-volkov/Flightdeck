#!/usr/bin/env python3
"""Repository-local entry point for the Flightdeck standard-library CLI."""

import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from flightdeck.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
