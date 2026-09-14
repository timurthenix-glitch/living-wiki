# Changelog

Версии соответствуют полю `bootstrap_version` в `AGENTS.md` и `skills/living-wiki/SKILL.md`. При бампе версии обнови **оба** файла синхронно — единого источника правды между репозиторием и Claude Code-скиллом два, потому что этого требует формат skill-файла (YAML frontmatter вместо заголовка), но содержание должно оставаться идентичным по сути. `scripts/check-version-sync.sh` (запускается в CI) падает, если `plugin.json`, `AGENTS.md` и `SKILL.md` разошлись.

## Unreleased

Пакетные и инфраструктурные улучшения — контракт `bootstrap_version` (`v0.1`) не менялся, схема vault и данные пользователя не затронуты.

- `LICENSE` (MIT) + поле `license` в `.claude-plugin/plugin.json`.
- `description` в `skills/living-wiki/SKILL.md` дополнен явным «не использовать для»: техдокументация/API/архитектурные и командные wiki не должны триггерить этот скилл.
- Семь файлов-указателей (`.clinerules/`, `.cursor/rules/`, `.github/copilot-instructions.md`, `.junie/guidelines.md`, `.kiro/steering/`, `.qoder/rules/`, `.windsurf/rules/`) теперь генерируются из единого `templates/rule-pointer.md` через `scripts/sync-rule-pointers.sh`.
- CI (`.github/workflows/checks.yml`): `scripts/check-rule-pointers.sh` проверяет, что копии-указатели не разошлись с шаблоном; `scripts/check-version-sync.sh` проверяет синхронность `plugin.json`/`AGENTS.md`/`SKILL.md`.
- `hooks/hooks.json` + `hooks/check-review-due.sh` — необязательный `SessionStart`-хук: читает реальные файлы `self/Reviews/` (без отдельного состояния) и, если Weekly/Monthly обзор просрочен, добавляет напоминание в контекст сессии. Разделы 4.7/6.4 `AGENTS.md` остаются обязательными для агента независимо от того, сработал ли хук.
- `examples/demo-vault/` — заполненный пример структуры (wiki/self/journal/Reviews) для наглядности формата.

## v0.1 — 2026-09-13

Первая версия.

- Базовая структура: `wiki/<тема>/`, `self/` (Goals, Habits, Lessons-Learned, Skills, Reviews), `journal/`, `raw/`, `crm/`.
- Автоматизация саморазвития: триггеры на сигнальные фразы, стрики привычек, обнаружение повторов и противоречий, еженедельные/ежемесячные обзоры по календарю.
- Правила против частых поломок: единственный источник правды, запрет на схему «про запас», обязательные `.gitkeep`, explicit-failing автоматизация, пороги на размер страницы.
- Версионирование самого бутстрапа: `bootstrap_version` в frontmatter `AGENTS.md`, раздел 2.1 — обновление при повторном запуске без потери данных пользователя.
- Упаковка: `AGENTS.md` — универсальный fallback для любого агента; `skills/living-wiki/SKILL.md` + `.claude-plugin/` — устанавливаемый плагин для Claude Code; короткие файлы-указатели для Cursor/Windsurf/Cline/Copilot/Qoder/Kiro/Junie.
- Экономия контекста для Claude Code: `SKILL.md` разделён на компактное ядро (всегда нужное — принципы, постоянный цикл, триггеры саморазвития) и `skills/living-wiki/references/*.md` (разовая настройка, обзоры, приём данных, мультиагентность — читаются агентом только когда реально нужны). `AGENTS.md` избавлен от дублирующихся формулировок (было отдельным разделом 8, повторявшим раздел 1, и повтором про «другие агенты» внутри раздела 2). Поведение и структура вики не изменились, `bootstrap_version` не поднимался.
