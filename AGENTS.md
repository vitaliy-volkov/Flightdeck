<!-- autopilot:start -->
# Flightdeck

MIT-лицензированный offline-first skill и CLI для возобновляемой разработки от исходного brief до blind acceptance. Поддерживаемый runtime — Python 3.11+ и только standard library; Node.js, npm и pip-пакеты не требуются.

## Команды

Запускайте из корня репозитория; skill-entrypoint сам добавляет `src/` в import path.

```sh
python3 skills/flightdeck/scripts/flightdeck.py --project . init --mode semi --depth normal
python3 skills/flightdeck/scripts/flightdeck.py --project . status
python3 skills/flightdeck/scripts/flightdeck.py --project . resume
python3 skills/flightdeck/scripts/flightdeck.py --project . validate
python3 skills/flightdeck/scripts/flightdeck.py --project . doctor --agent codex
python3 skills/flightdeck/scripts/flightdeck.py --project . mode --set full
python3 skills/flightdeck/scripts/flightdeck.py --help
```

Полная проверка checkout:

```sh
DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer python3 -m unittest discover -s tests -v
DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer python3 scripts/quick_validate.py
```

Текущий evidence: exact unittest suite — 57/57, quick validation — exit 0.

## Структура и архитектура

- `src/flightdeck/core.py` — чистый автомат фаз, режимов и safety gates; `next_actions(state, event, trusted=False)` принимает trusted user authority только от adapter/embedding boundary. `request_action` использует закрытый allowlist: шесть общих adapter actions разрешены непосредственно, outward/irreversible actions требуют свежего одноразового approval, неизвестные действия блокируются.
- `src/flightdeck/state.py` — schema v2, append-only event log и атомарная запись `.flightdeck/state.json`. `artifact_integrity` хранит SHA-256 и provenance для immutable brief, объединённых append-only additions и acceptance; загрузка проверяет tracked/untracked файлы и tampering. Валидный v1 без acceptance мигрирует в памяти, а v1 с acceptance без надёжной versioned provenance блокируется.
- `src/flightdeck/cli.py` — публичные команды `init`, `resume`, `status`, `validate`, `advance`, `artifact`, `doctor`, `plugin`, `mode`, `export`; глобальные `--project`, `--dry-run`. Generic event injection и approval JSON намеренно не доступны через CLI. `reporting.render(...)` — единый владелец redacted export и canonical acceptance shape.
- `src/flightdeck/adapters/__init__.py` — общий capability contract `run_command`, `edit_file`, `spawn_worker`, `open_preview`, `request_approval`, `report_result` для `codex`, `claude-code`, `cursor`; unsupported capability возвращает blocker/documented fallback и никогда не изображает успех.
- `src/flightdeck/plugins/__init__.py` и `src/flightdeck/plugins/_runner.py` — manifest/API validation, immutable source evidence, content-addressed cache, integrity-protected lock, lifecycle и изолированный JSONL hook subprocess. Plugin boundary default-deny: capabilities должны быть объявлены и выданы; `network`, `shell`, `files.write` не исполняются напрямую, а `external.write` проходит только как brokered intent со свежим одноразовым `actor=user` approval. Plugins не получают secrets родительского процесса и не могут менять brief, снимать requirement или обходить gate/approval.
- `skills/flightdeck/SKILL.md` — skill contract; `skills/flightdeck/references/phases.md` и `skills/flightdeck/references/modes.md` — progressive disclosure; `skills/flightdeck/scripts/flightdeck.py` — локальный entrypoint.
- `scripts/quick_validate.py` — dependency-free проверка metadata/links, полного unittest suite и CLI help.
- `examples/safe-plugin/` — рабочий пример `safe-report@1.0.0`; архитектура и модель безопасности описаны в `docs/architecture.md`, `docs/plugin-authoring.md`, `docs/security-model.md`.

Фазы: `preflight → manifest → briefing → spec → plan → build → review → acceptance`. Режимы: `full | semi | interview | manual`; запрошенная смена режима применяется со следующей фазы. Исходный brief неизменяем, дополнения append-only, а требование может снять только пользователь. Каждая смена фазы требует совпадающего успешного validator evidence.

## Conventions, tests и ограничения

- Основной тестовый шов — CLI black box во временном проекте; отдельно проверяются чистые core transitions, атомарность/corruption state, adapters, plugin lifecycle/isolation и end-to-end acceptance.
- `--dry-run` не должен менять файлы; повреждённое состояние диагностируется без перезаписи.
- Недоступная capability возвращает `BLOCKED`/blocker. Не устанавливайте зависимости самовольно.
- Не выполняйте push, deploy, release, публикацию, оплату, отправку сообщений, удаление данных, переписывание истории или иной внешний/необратимый action без отдельного подтверждения пользователя.
- Поддерживаемая среда — Python 3.11+. `doctor` проверяет версию Python, adapter и plugin lock/grants; его отрицательная диагностика неподдерживаемого runtime является ожидаемым fail-closed поведением.
- Python audit hooks в plugin runner — defense-in-depth, а не полноценная OS sandbox; не доверяйте им как единственной границе для враждебного кода.
- На POSIX artifact lock использует kernel advisory lock и освобождается при завершении writer. На non-POSIX fallback основан на exclusive lock file: stale lock после аварийного завершения требует ручного восстановления.
- Запись artifact и state — две отдельные атомарные операции, не единая filesystem transaction. Прерывание между ними оставляет обнаруживаемое несоответствие; следующая загрузка намеренно fail closed до ручного восстановления.
<!-- autopilot:end -->
