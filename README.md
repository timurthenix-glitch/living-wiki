# Living Wiki — живая вики и автоматизация саморазвития

> **In English:** a Claude Code plugin (also usable standalone with any file-reading agent) that sets up and maintains a personal, self-improving knowledge base and journal on plain markdown — `wiki/`, `self/`, `journal/` folders linked with Obsidian-style `[[wikilinks]]`, no database, no API keys, no embeddings. Install with `/plugin marketplace add timurthenix-glitch/living-wiki` then `/plugin install living-wiki@living-wiki` in Claude Code. The rest of this README, and the instructions the agent itself follows (`AGENTS.md`, `skills/living-wiki/SKILL.md`), are in Russian — that's the author's language and the language the resulting vault is written in. The one field that actually needs to be in English for Claude Code to match the skill correctly (`description` in `SKILL.md`'s frontmatter) already is.

Персональная база знаний и дневник саморазвития, которую ведёт AI-агент: обычные markdown-файлы, связанные `[[wikilinks]]`, без базы данных, без API-ключей, без привязки к одному инструменту. Открой папку проекта как vault в Obsidian — увидишь растущий граф.

Идея основана на «LLM wiki» Андрея Карпати (см. [его пост](https://x.com/karpathy)) — вместо векторной базы и embeddings агент сам ходит по markdown-файлам через `[[ссылки]]` и index-файлы, и этого достаточно, пока речь не о тысячах документов.

## Что внутри

- `AGENTS.md` — полный набор правил, самодостаточный файл. Это универсальный fallback: подходит для **любого** агента, который умеет читать файлы проекта (Claude Code, Cursor, Windsurf, Antigravity, Codex, Gemini CLI, обычный чат с доступом к файлам — что угодно).
- `skills/living-wiki/SKILL.md` + `.claude-plugin/` — та же логика, упакованная как устанавливаемый плагин для Claude Code (см. установку ниже). Ради экономии контекста ядро правил в `SKILL.md` компактное, а детали, нужные не в каждой сессии (разовая настройка, еженедельные обзоры, приём данных со стороны, работа с несколькими агентами), вынесены в `skills/living-wiki/references/*.md` и читаются агентом только по ситуации.
- `.agents/rules/`, `.cursor/rules/`, `.windsurf/rules/`, `.clinerules/`, `.qoder/rules/`, `.kiro/steering/`, `.junie/guidelines.md`, `.github/copilot-instructions.md` — короткие файлы-указатели на `AGENTS.md` в форматах, которые эти инструменты понимают из коробки. Все восемь — копии одного `templates/rule-pointer.md`; после правки указателя запусти `scripts/sync-rule-pointers.sh`, CI (`scripts/check-rule-pointers.sh`) проверяет, что копии не разошлись. Antigravity (`.agents/rules/living-wiki.md`) не читает файл-указатель сам по себе — его нужно один раз включить в Rules-панели Antigravity (Settings → Rules) с активацией «Always On», иначе он просто лежит в проекте, не подключённый.
- `hooks/` — два необязательных `SessionStart`-хука. `check-review-due.sh` смотрит на реальные файлы в `self/Reviews/` и, если Weekly/Monthly обзор просрочен, добавляет короткое напоминание в контекст сессии — ускоритель поверх раздела 4.7/6.4 `AGENTS.md`. `vault-lint.sh` сканирует `wiki/` и `self/` на битые `[[wikilinks]]`, страницы-сироты (<2 связей), разросшиеся страницы и файлы вне `index.md` своей темы — молчит, если всё чисто, иначе оставляет короткую сводку; с флагом `--list` печатает файл-за-файлом отчёт, по которому агент делает «Полную уборку» (раздел 4.6 `AGENTS.md`) точечно, не открывая весь vault. Оба хука — ускорители поверх правил `AGENTS.md`, а не замена: агент обязан делать ту же проверку сам, даже если хук не сработал.
- `scripts/` — единый контур максимальной эффективности (`engine.py`, `storage.py`, `text.py`): локальная семантическая память SQLite (порог >= 0.88, 3-граммы, Жаккар, гардрайлы отрицаний), сверхбыстрый Атлас хранилища (`engine.py status`, ~150 токенов вместо слепого чтения файлов), верифицированное сжатие логов (`reduce`), упаковка наблюдений (`pack`/`recall`) и Action Fusion (`fuse`) по принципам SoL-Pi (NVIDIA Research). Работает в любом агенте без сторонних библиотек.
- `examples/demo-vault/` — заполненный пример структуры (wiki/self/journal/Reviews), просто для наглядности формата; не устанавливается и не используется агентом.
- `LICENSE` — MIT.

Все файлы правил синхронизированы по смыслу и версии (`bootstrap_version`, сейчас `v1`, см. `CHANGELOG.md`), а CI (`.github/workflows/checks.yml`) при каждом push/PR проверяет: синхронность указателей, соотношение версий (см. «Версионирование» ниже) и что JSON-манифесты (`plugin.json`, `marketplace.json`, `hooks/hooks.json`) валидны.

## Установка

### Claude Code (одна команда, работает сразу в любом проекте)

```
/plugin marketplace add timurthenix-glitch/living-wiki
/plugin install living-wiki@living-wiki
```

После установки просто скажи в любом проекте что-то вроде «настрой мне живую вики» — Claude Code сам подхватит skill и создаст структуру. Устанавливать заново в каждом новом проекте не нужно.

### Любой другой агент/IDE (Cursor, Windsurf, Cline, Antigravity, Codex, Gemini CLI, GitHub Copilot и т.д.)

Готового «одна команда — и работает везде» тут нет: большинство таких инструментов читают файлы только из конкретного проекта, а не из глобально установленных плагинов. Поэтому в каждый новый проект, который хочешь превратить в живую вики:

1. Скопируй `AGENTS.md` из этого репозитория в корень проекта.
2. Если пользуешься инструментом с собственной конвенцией правил (Cursor, Windsurf, Cline, Qoder, Kiro, Junie, GitHub Copilot, Antigravity) — скопируй туда же и соответствующий файл-указатель из этого репозитория (`.cursor/rules/living-wiki.md` и т.д.) — это подстраховка на случай, если инструмент ещё не читает `AGENTS.md` напрямую (многие уже читают). У Antigravity (`.agents/rules/living-wiki.md`) этого недостаточно — файл ещё нужно один раз включить в Settings → Rules с активацией «Always On», у него нет автоматического подхвата файлов из папки без этого шага.
3. В начале сессии на всякий случай скажи агенту: «прочитай AGENTS.md, дальше следуй ему».

Дальше агент сам создаёт структуру и ведёт её по правилам из `AGENTS.md` — см. содержимое файла.

### Просто вставить в чат, без репозитория

Можно по-прежнему обойтись без установки чего-либо: скопировать содержимое `AGENTS.md` и вставить в чат с любым агентом внутри нужной папки со словами «настрой это». Плагин/репозиторий нужен только для того, чтобы не делать это вручную каждый раз в новом проекте.

## Версионирование

В проекте действует единая финальная версия **v1**.

- **`bootstrap_version`** (`v1`, в frontmatter `AGENTS.md` и в заголовке `SKILL.md`) — версия схемы vault и правил. Новые улучшения вносятся непосредственно в этот файл. Раздел 2.1 описывает, как агент поддерживает актуальность существующего проекта, никогда не трогая накопленные данные пользователя (`wiki/`, `self/`, `journal/`, `raw/`, `attachments/`, `log.md`).
- **`version` в `.claude-plugin/plugin.json`** (`1.0.0`) — версия пакета плагина для Claude Code.
- `scripts/check-version-sync.sh` (в CI) проверяет синхронность: `AGENTS.md` и `SKILL.md` совпадают, а `plugin.json` не отстаёт от схемы.
- После обновления плагина в коде при необходимости публикуется тег/релиз: `git tag -a living-wiki--v1.0.0 <коммит> && git push origin living-wiki--v1.0.0`.

## Обновление установленного плагина

Claude Code обновляет плагин через marketplace:
- **Вручную**: `/plugin marketplace update living-wiki`, затем `/reload-plugins`.
- **Автоматически**: при включённом auto-update (`/plugin` → Marketplaces → `living-wiki` → Enable auto-update).

