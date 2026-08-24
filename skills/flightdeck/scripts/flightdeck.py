#!/usr/bin/env python3
"""Repository-local entry point for the Flightdeck standard-library CLI."""

import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve()
SOURCE_ROOTS = (SCRIPT.parents[3] / "src", SCRIPT.parents[1] / "src")
for source_root in SOURCE_ROOTS:
    if (source_root / "flightdeck").is_dir():
        sys.path.insert(0, str(source_root))
        break
else:
    raise SystemExit("Flightdeck runtime not found; install src/ beside the skill or run from a checkout")

from flightdeck.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
