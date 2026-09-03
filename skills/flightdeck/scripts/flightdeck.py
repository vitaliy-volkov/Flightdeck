#!/usr/bin/env python3
"""Repository-local entry point for the Flightdeck standard-library CLI."""

import os
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve()


def _source_roots():
    roots = []
    configured = os.environ.get("FLIGHTDECK_SRC")
    if configured:
        roots.append(Path(configured))
    for parent in SCRIPT.parents:
        roots.append(parent / "src")
    return roots


for source_root in _source_roots():
    if (source_root / "flightdeck").is_dir():
        sys.path.insert(0, str(source_root))
        break
else:
    raise SystemExit(
        "Flightdeck runtime not found; keep src/ in the checkout or package the skill with scripts/package_skill.py"
    )

from flightdeck.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
