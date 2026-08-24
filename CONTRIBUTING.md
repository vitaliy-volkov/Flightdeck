# Contributing

Flightdeck requires Python 3.11+ and has no mandatory third-party runtime dependency.

1. Fork and clone the repository.
2. Create a focused branch and keep unrelated changes out.
3. Preserve public contracts, requirement traceability, and explicit approval gates.
4. Run `python3 -m unittest discover -s tests -v`.
5. Validate the skill with `python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" skills/flightdeck`, or the equivalent bundled `skill-creator` validator in your environment.
6. Submit a pull request explaining behavior, evidence, and security impact.

Tests should assert observable behavior at the CLI or documented public seam. Never commit secrets or generated local state. Opening a contribution does not authorize maintainers or automation to deploy, publish, or mutate an external service.
