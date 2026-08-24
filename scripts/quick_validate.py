#!/usr/bin/env python3
"""Fast, dependency-free Flightdeck checkout validation."""
import subprocess
import sys
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
skill = (root / "skills/flightdeck/SKILL.md").read_text(encoding="utf-8")
if not re.match(r"^---\nname: flightdeck\ndescription: .+\n---\n", skill):
    raise SystemExit("invalid SKILL.md frontmatter")
for relative in ("references/phases.md", "references/modes.md", "../../docs/architecture.md", "../../docs/plugin-authoring.md"):
    if relative not in skill or not (root / "skills/flightdeck" / relative).resolve().is_file():
        raise SystemExit("invalid progressive-disclosure link: " + relative)
agent = (root / "skills/flightdeck/agents/openai.yaml").read_text(encoding="utf-8")
for field in ("interface:", "display_name:", "short_description:", "default_prompt:", "policy:", "allow_implicit_invocation:"):
    if field not in agent:
        raise SystemExit("openai.yaml missing " + field)
commands = [
    [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
    [sys.executable, "skills/flightdeck/scripts/flightdeck.py", "--help"],
]
for command in commands:
    completed = subprocess.run(command, cwd=root, check=False)
    if completed.returncode:
        raise SystemExit(completed.returncode)
