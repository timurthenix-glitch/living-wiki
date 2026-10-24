#!/usr/bin/env python3
"""
observe-prompt.py — Hook on UserPromptSubmit for Living Wiki.

Inspects incoming user prompt for self-improvement signals:
  - decisions & goals (решил, цель, планирую, decided, my goal)
  - lessons & insights (заметил, понял, вывод, урок, realized, noticed)
  - errors & failures (ошибся, не получилось, баг, сломалось, failed, error)
  - successes & milestones (получилось, сделал, заработало, completed, success)
  - habits & routines (каждый день, привычка, бросил, habit, streak)

If signals are detected, injects high-priority operational instructions to
record them automatically without asking permission.
"""

import sys
import json
import re

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SIGNALS = [
    (
        "Цель / Решение",
        r"(?i)\b(решил|решила|хочу начать|хочу бросить|моя цель|планирую|поставил цель|договорился|decided|my goal|going to|plan to|committed to)\b",
        "Обнаружено решение или цель. Зафиксируй в self/Goals.md с текущей датой."
    ),
    (
        "Урок / Инсайт",
        r"(?i)\b(заметил|заметила|понял|поняла|осознал|осознала|вывод|урок|инсайт|в следующий раз|I noticed|lesson learned|realized|takeaway|next time)\b",
        "Обнаружен урок/инсайт. Зафиксируй в self/Lessons-Learned.md (сформулируй правилом на будущее)."
    ),
    (
        "Ошибка / Сбой / Неудача",
        r"(?i)\b(ошибся|ошиблась|не получилось|не вышло|не работает|сломалось|баг|упало|крашится|провалился|failed|broke|broken|error|bug|crash)\b",
        "Обнаружена ошибка/сбой. Если это технические грабли — зафиксируй 3-строчный «Технический якорь» в self/Lessons-Learned.md."
    ),
    (
        "Успех / Результат",
        r"(?i)\b(получилось|сделал|сделала|достиг|достигла|заработало|справился|справилась|успешно|made it|completed|done|success|works now)\b",
        "Обнаружен успех/результат. Отметь прогресс в self/Goals.md или journal/."
    ),
    (
        "Привычка / Регулярность",
        r"(?i)\b(каждый день|привычка|бросил|бросила|веду|стрик|трекаю|streak|habit|routine)\b",
        "Обнаружено упоминание привычки. Обнови стрик в self/Habits.md."
    ),
]


def main():
    try:
        if sys.stdin.isatty():
            sys.exit(0)
        raw_input = sys.stdin.read()
        if not raw_input.strip():
            sys.exit(0)
        data = json.loads(raw_input)
    except Exception:
        sys.exit(0)

    prompt = ""
    if isinstance(data, dict):
        prompt = data.get("prompt") or data.get("userPrompt") or ""
    elif isinstance(data, str):
        prompt = data

    detected = []
    for category, pattern, instruction in SIGNALS:
        if re.search(pattern, prompt):
            detected.append((category, instruction))

    if detected:
        details = " | ".join(f"{cat}: {inst}" for cat, inst in detected)
        msg = (
            f"[LIVING WIKI AUTO-OBSERVER]: В сообщении обнаружен сигнал саморазвития ({details}). "
            f"Согласно AGENTS.md §6.1, ты ОБЯЗАН зафиксировать это в фоновом режиме (в self/ или journal/) "
            f"ДО или вместе с ответом. Спрашивать «хотите записать?» СТРОГО ЗАПРЕЩЕНО. "
            f"В конце ответа добавь строку: «✓ зафиксировал: [файл — «кратко»]»."
        )
    else:
        # Minimal background reinforcement
        msg = (
            "[LIVING WIKI]: Фоновый авто-контур активен. Если в ходе обсуждения появляются решения, "
            "уроки или сбои инструментов — фиксируй их молча в self/ (сводка «✓ зафиксировал:» в конце ответа)."
        )

    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": msg
        }
    }
    print(json.dumps(output, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
