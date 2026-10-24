#!/usr/bin/env python3
"""
observe-tool.py — Hook on PostToolUse for Living Wiki.

Catches tool failures, non-zero exit codes, test failures, or error tracebacks.
Injects context reminding the agent of §6.1 / §6.1.1.2 (Unexpected tool failure
is default lesson requiring a 3-line Technical Anchor).
Also auto-records the incident in engine.py session buffer.
"""

import sys
import json
import subprocess
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


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

    tool_name = data.get("toolName", "")
    tool_input = data.get("toolInput", {})
    tool_result = data.get("toolResult", {})

    is_error = False
    error_summary = ""

    if isinstance(tool_result, dict):
        if tool_result.get("isError") or tool_result.get("exitCode", 0) != 0:
            is_error = True
            error_summary = str(tool_result.get("output") or tool_result.get("error") or "")[:200]
    elif isinstance(tool_result, str):
        err_markers = ["Traceback (most recent call last)", "Error:", "FAILED", "fatal:", "Exception:"]
        if any(marker in tool_result for marker in err_markers):
            is_error = True
            error_summary = tool_result[:200]

    if is_error:
        cmd_str = tool_input.get("command", "") if isinstance(tool_input, dict) else str(tool_input)
        cmd_summary = f"{tool_name}: {cmd_str[:80]}" if cmd_str else tool_name

        try:
            root = Path(__file__).resolve().parent.parent
            engine_script = root / "scripts" / "engine.py"
            if engine_script.exists():
                subprocess.run(
                    [
                        sys.executable,
                        str(engine_script),
                        "record",
                        f"Сбой инструмента: {cmd_summary}",
                        "--kind",
                        "failure",
                        "--detail",
                        error_summary[:120],
                    ],
                    capture_output=True,
                    timeout=3,
                )
        except Exception:
            pass

        msg = (
            f"[LIVING WIKI TOOL ALERT]: Команда ({cmd_summary}) завершилась с ошибкой. "
            f"Согласно AGENTS.md §6.1 и §6.1.1.2: «Неожиданный результат инструмента — это урок по умолчанию». "
            f"Если это не случайная опечатка, а реальное техническое препятствие (баг, несовместимость, ограничение платформы) — "
            f"сформулируй 3-строчный «Технический якорь» (Что сделали / Первопричина под капотом / Грабли на будущее) "
            f"и внеси его в self/Lessons-Learned.md."
        )
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": msg,
            }
        }
        print(json.dumps(output, ensure_ascii=False))

    sys.exit(0)


if __name__ == "__main__":
    main()
