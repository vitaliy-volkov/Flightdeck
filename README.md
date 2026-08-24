# Flightdeck

Flightdeck is an MIT-licensed, offline-first skill and Python 3.11+ CLI for resumable software delivery: brief → manifest → specification → plan → build → review → blind acceptance. It uses only the Python standard library; Node.js, npm, and pip packages are not required.

## Быстрый старт (RU)

```sh
git clone https://github.com/vitaliy-volkov/Flightdeck.git
cd Flightdeck
python3 skills/flightdeck/scripts/flightdeck.py --project . init --mode semi --depth normal
python3 skills/flightdeck/scripts/flightdeck.py --project . status
python3 skills/flightdeck/scripts/flightdeck.py --project . validate
```

Для продолжения существующего запуска используйте `resume`. Режимы: `full`, `semi`, `interview`, `manual`; команда `python3 skills/flightdeck/scripts/flightdeck.py --project . mode --set full` планирует смену режима со следующей фазы. `full` записывает автоматические решения как assumptions, но не разрешает push, deploy, публикацию и другие внешние или необратимые действия — для них всегда нужно отдельное подтверждение пользователя.

## Quick start (EN)

```sh
git clone https://github.com/vitaliy-volkov/Flightdeck.git
cd Flightdeck
python3 skills/flightdeck/scripts/flightdeck.py --project . init --mode semi --depth normal
python3 skills/flightdeck/scripts/flightdeck.py --project . status
python3 skills/flightdeck/scripts/flightdeck.py --project . validate
```

Use `resume` for an existing run. Modes are `full`, `semi`, `interview`, and `manual`; `python3 skills/flightdeck/scripts/flightdeck.py --project . mode --set full` schedules a mode change for the next phase. Full mode records automatic decisions as assumptions, but never authorizes push, deployment, publication, or another external or irreversible action. Those always require separate user approval.

See [architecture](docs/architecture.md), [plugin authoring](docs/plugin-authoring.md), [security model](docs/security-model.md), and the [comparison ADR](docs/adr/0001-autopilot-comparison.md). Run tests with `python3 -m unittest discover -s tests -v`.

Target repository naming is documented for installation only; cloning or creating a repository does not imply permission to push or publish.
