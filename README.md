# Living Wiki — живая память агента: MEMORY.md + скилы

Один файл памяти и папка скилов, которые агент ведёт сам. Без Obsidian, без графа, без базы данных и API-ключей — обычный markdown, который читает любой агент.

> **In English:** a Claude Code plugin (also usable standalone with any file-reading agent) that gives an agent durable project memory — a single `MEMORY.md` of short declarative facts plus `skills/<name>/SKILL.md` for repeatable procedures, both written by the agent as the work happens. The rules and the resulting memory are in Russian; the `description` field above, which Claude Code matches on, is English.

## Что создаётся в проекте

```
AGENTS.md              — правила (копия из этого репозитория)
MEMORY.md              — память: короткие факты, лимит 6000 символов
skills/<имя>/SKILL.md  — повторяемые процедуры, появляются по мере надобности
```

Два файла на старте. Ни `wiki/`, ни `journal/`, ни `raw/`, ни `attachments/`, ни дат в именах.

## Как это работает

- Факт выяснился в диалоге — агент пишет его в `MEMORY.md` сразу, в том же ответе. Пользователь не говорит «запиши» и не подводит итог: аудит ответа — часть работы агента.
- Процедура повторилась дважды или оказалась неочевидной — появляется `skills/<имя>/SKILL.md`. После использования скил дописывается, а подвёл дважды — переписывается.
- Память не журнал: там только то, что действует сейчас. Дубликаты сливаются, противоречия заменяются, лишнее удаляется при переполнении.

## Установка

### Claude Code

```
/plugin marketplace add timurthenix-glitch/living-wiki
/plugin install living-wiki@living-wiki
```

### Любой другой агент/IDE (Cursor, Windsurf, Cline, Antigravity, Codex, Gemini CLI, Copilot, Kiro, Junie)

1. Скопируй `AGENTS.md` в корень проекта — это весь набор правил, самодостаточный, ~8 КБ.
2. Если инструмент не читает `AGENTS.md` сам — положи рядом соответствующий указатель из репозитория (`.cursor/rules/living-wiki.md`, `.agents/rules/living-wiki.md`, `.clinerules/living-wiki.md`, `.windsurf/rules/living-wiki.md`, `.qoder/rules/living-wiki.md`, `.kiro/steering/living-wiki.md`, `.junie/guidelines.md`, `.github/copilot-instructions.md`). У Antigravity указатель нужно один раз включить в Settings → Rules («Always On»).
3. Скажи агенту: «прочитай AGENTS.md и работай по нему». `MEMORY.md` он создаст сам.

### Совсем без репозитория

Скопируй содержимое `AGENTS.md` в чат со словами «настрой память по этим правилам».

## Версия

Версия одна — **v1**, окончательная: подверсий, минорных номеров и параллельных релизов нет, любое улучшение вносится прямо в v1.

Свежесть развёрнутой копии правил определяется не номером, а `rules_hash` во frontmatter `AGENTS.md` — sha256 по `AGENTS.md` и `skills/living-wiki/**`, считается `scripts/rules-hash.sh`, проверяется в CI. Хэши не совпали — копию правил надо обновить; `MEMORY.md` и `skills/` при этом не трогаются.

`version` в `.claude-plugin/plugin.json` — semver самого пакета; он обязан быть не меньше правила-маркера (`1.0`), потому что Claude Code предлагает обновление только когда эта версия растёт.

После изменения `version` публикуй GitHub Release вручную: `git tag -a living-wiki--vX.Y.Z <коммит> && git push origin living-wiki--vX.Y.Z`, затем `gh release create`.

## Проверки

`.github/workflows/checks.yml` на каждый push/PR прогоняет:

- `scripts/check-rule-pointers.sh` — восемь файлов-указателей не разошлись с `templates/rule-pointer.md`;
- `scripts/check-version-sync.sh` — маркер версии в `AGENTS.md` и `SKILL.md` совпадает, `plugin.json` не отстаёт;
- `scripts/rules-hash.sh --check` — `rules_hash` соответствует содержимому слоя правил;
- `scripts/check-json-valid.sh` — манифесты парсятся как JSON.

## Лицензия

MIT.
