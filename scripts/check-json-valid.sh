#!/usr/bin/env bash
# Cheap syntax-only fallback for the plugin/marketplace/hook JSON files — no
# external dependency beyond python3, which GitHub's ubuntu-latest runner
# ships by default. This only proves the files parse as JSON; it does not
# check plugin manifest semantics the way `claude plugin validate --strict`
# does. Run that too before a release if the `claude` CLI is available.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# GitHub Actions даёт python3, но на Windows-машинах разработчиков обычно
# есть только `python` (а `python3` может отсутствовать или быть заглушкой
# Microsoft Store). Выбираем первый реально работающий интерпретатор.
PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import sys" >/dev/null 2>&1; then
    PY="$candidate"
    break
  fi
done
if [[ -z "$PY" ]]; then
  echo "::error::No working Python interpreter found (tried python3, python)." >&2
  exit 1
fi

files=(
  ".claude-plugin/plugin.json"
  ".claude-plugin/marketplace.json"
  "hooks/hooks.json"
)

fail=0
for f in "${files[@]}"; do
  [[ -f "$f" ]] || continue
  if ! "$PY" -c "import json,sys; json.load(open(sys.argv[1], encoding='utf-8'))" "$f"; then
    echo "::error::$f is not valid JSON" >&2
    fail=1
  else
    echo "OK: $f"
  fi
done

exit "$fail"
