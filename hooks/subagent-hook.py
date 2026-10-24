#!/usr/bin/env python3
"""
subagent-hook.py — Hooks on SubagentStart & SubagentStop for Living Wiki.

Solves the Colossal Subagent Problem:
  1. Subagent Amnesia: When subagents finish, their execution context and
     hard-won technical discoveries are destroyed.
  2. Context Explosion: Subagents should not read the entire 60KB AGENTS.md.
  3. Shared-State Corruption: Subagents must not call session-close on the
     parent's session.json.
  4. Knowledge Handoff: Subagents are instructed to format technical findings
     into a dedicated "### Находки для Живой Вики" block, and the parent agent
     is prompted to act as Knowledge Harvester upon subagent completion.
"""

import sys
import json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def handle_start():
    msg = (
        "[LIVING WIKI SUBAGENT CONTRACT]: Ты работаешь в режиме изолированного субагента. "
        "Твой контекст будет уничтожен после завершения, поэтому важные технические уроки должны быть переданы наверх. "
        "ПРАВИЛА: "
        "1) Экономь токены — не читай 60КБ AGENTS.md целиком; "
        "2) Не закрывай сессию родителя (не вызывай session-close); "
        "3) Если ты столкнулся с неочевидной ошибкой, решил сложный баг или выявил архитектурные грабли — "
        "ОБЯЗАТЕЛЬНО заверши свой финальный ответ выделенным блоком:\n\n"
        "### Находки для Живой Вики (Technical Anchor)\n"
        "- **Что сделали**: конкретное решение/настройка\n"
        "- **Первопричина**: физика бага под капотом\n"
        "- **Грабли на будущее**: что упадёт, если откатить назад"
    )
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SubagentStart",
            "additionalContext": msg,
        }
    }
    print(json.dumps(output, ensure_ascii=False))


def handle_stop():
    msg = (
        "[LIVING WIKI KNOWLEDGE HARVESTER]: Субагент завершил выполнение! "
        "ВНИМАНИЕ (Защита от субагентной амнезии): Проверь ответ субагента на наличие блока "
        "«### Находки для Живой Вики» или нетривиальных технических решений. "
        "Если субагент обнаружил баг или применил неочевидный фикс — немедленно зафиксируй этот урок "
        "в self/Lessons-Learned.md (Технический якорь) и добавь событие через engine.py record, "
        "иначе опыт субагента будет безвозвратно утерян!"
    )
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SubagentStop",
            "additionalContext": msg,
        }
    }
    print(json.dumps(output, ensure_ascii=False))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "start"
    if mode == "stop":
        handle_stop()
    else:
        handle_start()
    sys.exit(0)


if __name__ == "__main__":
    main()
