#!/usr/bin/env bash
# Cheap syntax-only fallback for the plugin/marketplace/hook JSON files — no
# external dependency beyond python3, which GitHub's ubuntu-latest runner
# ships by default. This only proves the files parse as JSON; it does not
# check plugin manifest semantics the way `claude plugin validate --strict`
# does. Run that too before a release if the `claude` CLI is available.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

files=(
  ".claude-plugin/plugin.json"
  ".claude-plugin/marketplace.json"
  "hooks/hooks.json"
)

fail=0
for f in "${files[@]}"; do
  [[ -f "$f" ]] || continue
  if ! python3 -c "import json,sys; json.load(open(sys.argv[1], encoding='utf-8'))" "$f"; then
    echo "::error::$f is not valid JSON" >&2
    fail=1
  else
    echo "OK: $f"
  fi
done

exit "$fail"
