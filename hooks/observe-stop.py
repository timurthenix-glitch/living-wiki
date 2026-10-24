#!/usr/bin/env python3
"""
observe-stop.py — Hook on Stop for Living Wiki.

Executes when the agent turn completes:
  1. Ensures session buffer changes are synced with SQLite memory if needed.
  2. Reminds the agent to provide the end-of-turn "✓ зафиксировал:" summary
     if files in self/, wiki/, or journal/ were updated during this turn.
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
    # Quick sync check
    try:
        root = Path(__file__).resolve().parent.parent
        engine_script = root / "scripts" / "engine.py"
        if engine_script.exists():
            subprocess.run(
                [sys.executable, str(engine_script), "sync"],
                capture_output=True,
                timeout=5,
            )
    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
