# Living Wiki — живая вики и автоматизация саморазвития

> **In English:** a Claude Code plugin (also usable standalone with any file-reading agent) that sets up and maintains a personal, self-improving knowledge base and journal on plain markdown — `wiki/`, `self/`, `journal/` folders linked with Obsidian-style `[[wikilinks]]`, no database, no API keys, no embeddings. Install with `/plugin marketplace add timurthenix-glitch/living-wiki` then `/plugin install living-wiki@living-wiki` in Claude Code. The rest of this README, and the instructions the agent itself follows (`AGENTS.md`, `skills/living-wiki/SKILL.md`), are in Russian — that's the author's language and the language the resulting vault is written in. The one field that actually needs to be in English for Claude Code to match the skill correctly (`description` in `SKILL.md`'s frontmatter) already is.

Персональная база знаний и дневник саморазвития, которую ведёт AI-агент: обычные markdown-файлы, связанные `[[wikilinks]]`, без базы данных, без API-ключей, без привязки к одному инструменту. Открой папку проекта как vault в Obsidian — увидишь растущий граф.

Идея основана на «LLM wiki» Андрея Карпати (см. [его пост](https://x.com/karpathy)) — вместо векторной базы и embeddings агент сам ходит по markdown-файлам через `[[ссылки]]` и index-файлы, и этого достаточно, пока речь не о тысячах документов.

## Что внутри

- `AGENTS.md` — полный набор правил, самодостаточный файл. Это универсальный fallback: подходит для **любого** агента, который умеет читать файлы проекта (Claude Code, Cursor, Windsurf, Antigravity, Codex, Gemini CLI, обычный чат с доступом к файлам — что угодно).
- `skills/living-wiki/SKILL.md` + `.claude-plugin/` — та же логика, упакованная как устанавливаемый плагин для Claude Code (см. установку ниже). Ради экономии контекста ядро правил в `SKILL.md` компактное, а детали, нужные не в каждой сессии (разовая настройка, еженедельные обзоры, приём данных со стороны, работа с несколькими агентами), вынесены в `skills/living-wiki/references/*.md` и читаются агентом только по ситуации.
- `.agents/rules/`, `.cursor/rules/`, `.windsurf/rules/`, `.clinerules/`, `.qoder/rules/`, `.kiro/steering/`, `.junie/guidelines.md`, `.github/copilot-instructions.md` — короткие файлы-указатели на `AGENTS.md` в форматах, которые эти инструменты понимают из коробки. Все восемь — копии одного `templates/rule-pointer.md`; после правки указателя запусти `scripts/sync-rule-pointers.sh`, CI (`scripts/check-rule-pointers.sh`) проверяет, что копии не разошлись. Antigravity (`.agents/rules/living-wiki.md`) не читает файл-указатель сам по себе — его нужно один раз включить в Rules-панели Antigravity (Settings → Rules) с активацией «Always On», иначе он просто лежит в проекте, не подключённый.
- `hooks/` — два необязательных `SessionStart`-хука. `check-review-due.sh` смотрит на реальные файлы в `self/Reviews/` и, если Weekly/Monthly обзор просрочен, добавляет короткое напоминание в контекст сессии — ускоритель поверх раздела 4.7/6.4 `AGENTS.md`. `vault-lint.sh` сканирует `wiki/` и `self/` на битые `[[wikilinks]]`, страницы-сироты (<2 связей), разросшиеся страницы и файлы вне `index.md` своей темы — молчит, если всё чисто, иначе оставляет короткую сводку; с флагом `--list` печатает файл-за-файлом отчёт, по которому агент делает «Полную уборку» (раздел 4.6 `AGENTS.md`) точечно, не открывая весь vault. Оба хука — ускорители поверх правил `AGENTS.md`, а не замена: агент обязан делать ту же проверку сам, даже если хук не сработал.
- `examples/demo-vault/` — заполненный пример структуры (wiki/self/journal/Reviews), просто для наглядности формата; не устанавливается и не используется агентом.
- `LICENSE` — MIT.

Все файлы правил синхронизированы по смыслу и версии (`bootstrap_version`, сейчас `v0.3`, см. `CHANGELOG.md`), а CI (`.github/workflows/checks.yml`) при каждом push/PR проверяет: синхронность указателей, соотношение версий (см. «Версионирование» ниже) и что JSON-манифесты (`plugin.json`, `marketplace.json`, `hooks/hooks.json`) валидны.

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

Два разных номера версии, специально не связанные равенством:

- **`bootstrap_version`** (сейчас `v0.3`, в frontmatter `AGENTS.md` и в заголовке `SKILL.md`) — версия схемы vault и правил, которые видит уже существующий проект. Поднимается только когда реально меняется структура/поведение из разделов 2–7 `AGENTS.md`. Раздел 2.1 описывает, как агент обновляет уже существующий проект до новой версии, не трогая накопленные данные пользователя (`wiki/`, `self/`, `journal/`, `raw/`, `log.md`).
- **`version` в `.claude-plugin/plugin.json`** (semver, сейчас `0.3.1`, last build 2026-09-17) — версия самого пакета плагина. Она обязана быть **не меньше** `bootstrap_version`, но может уйти вперёд неё — упаковочные изменения (хуки, лицензия, примеры, CI), которые не трогают схему vault, поднимают только её. Так и должно быть: Claude Code предлагает обновление только когда `plugin.json.version` меняется (см. ниже), поэтому пакет не имеет права отставать от схемы — а вот опережать её пакетными улучшениями можно сколько угодно раз между бампами схемы.
- `scripts/check-version-sync.sh` (гоняется в CI) проверяет оба правила: `AGENTS.md`/`SKILL.md` совпадают точно, `plugin.json` — не меньше их.
- Перед релизом полезно ещё прогнать `claude plugin validate .claude-plugin/plugin.json --strict --json` (и то же для `marketplace.json`) — это официальный валидатор схемы манифеста от Claude Code, строже, чем просто JSON-парсинг из CI.
- **После бампа `version` также публикуй GitHub Release**, иначе он не появится сам: `git tag -a living-wiki--vX.Y.Z <коммит> && git push origin living-wiki--vX.Y.Z`, затем `gh release create living-wiki--vX.Y.Z --title "..." --notes-file ...` — заголовок и текст releaseа не создаются автоматически ни пушем коммитов, ни бампом `version` в `plugin.json`, это отдельное ручное действие. Если схема (`bootstrap_version`) в этом релизе экспериментальная и ещё не проверена на практике — добавляй `--prerelease`: GitHub не будет считать pre-release кандидатом на бейдж «Latest», так что предыдущий стабильный релиз (например, тот, на который смотрит `living-wiki-legacy-v0.1`) останется отмеченным как основной, а новый ляжет рядом с явной пометкой.

## Обновление уже установленного плагина

Claude Code обновляет плагин, только когда в `.claude-plugin/plugin.json` меняется поле `version` — если запушить изменения в код, но не поднять версию, установленные копии не увидят разницы. Страница GitHub Releases на это не влияет никак: обновление идёт через git, а не через релизы.

- **Вручную** (работает всегда): `/plugin marketplace update living-wiki`, затем, если попросит, `/reload-plugins`.
- **Автоматически**: Claude Code проверяет обновления в фоне ~10 минут после старта сессии, но только если для `living-wiki` включён auto-update (`/plugin` → Marketplaces → `living-wiki` → Enable auto-update) — по умолчанию для сторонних marketplace он выключен.
- **Новая установка** всегда подтягивает то, что сейчас в `main`.

Экспериментальные изменения, которые не должны сразу попасть к уже установившим, ведутся в отдельной ветке (не в `main`, без бампа `version` там). Попробовать её можно так: `/plugin marketplace add timurthenix-glitch/living-wiki#<ветка>` (с другим `name` в её `marketplace.json`, чтобы не конфликтовать с уже добавленным `living-wiki`). Когда всё готово — мёрж в `main` и бамп версии превращает эксперимент в обычный релиз.

## Несколько версий одновременно

Если схема поменялась (новый `bootstrap_version`), а часть пользователей сознательно хочет остаться на старой — не обязательно держать вторую ветку/marketplace. `.claude-plugin/marketplace.json` умеет перечислять несколько версий одного плагина в одном файле: у каждой записи в `plugins` — свой `source`, и он не обязан быть `"."` (текущий коммит), а может указывать на конкретный git-тег/ветку/коммит в этом же репозитории:

```json
{
  "name": "living-wiki-legacy-v0.1",
  "source": { "source": "github", "repo": "timurthenix-glitch/living-wiki", "ref": "living-wiki--v0.2.0" }
}
```

Обе версии ставятся из одного и того же добавления marketplace:

```
/plugin marketplace add timurthenix-glitch/living-wiki
/plugin install living-wiki@living-wiki              # текущая схема
/plugin install living-wiki-legacy-v0.1@living-wiki   # зафиксированная старая схема
```

Слэш-команды плагинов в Claude Code — это шаблоны промта с подстановкой аргумента, а не переключатель состояния: команды вида «`/living-wiki v0.1`, включи эту версию» не существует и не нужна — выбор версии происходит один раз, на уровне `/plugin install`, а не во время сессии.
