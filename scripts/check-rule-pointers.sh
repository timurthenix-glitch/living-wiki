#!/usr/bin/env bash
# Fails if any tool-specific pointer file has drifted from
# templates/rule-pointer.md. Used in CI; run scripts/sync-rule-pointers.sh
# locally to fix a failure.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

SOURCE="templates/rule-pointer.md"
TARGETS=(
  ".agents/rules/living-wiki.md"
  ".clinerules"
  ".cursor/rules/living-wiki.md"
  ".github/copilot-instructions.md"
  ".junie/guidelines.md"
  ".kiro/steering/living-wiki.md"
  ".qoder/rules/living-wiki.md"
  ".windsurf/rules/living-wiki.md"
)

fail=0
for t in "${TARGETS[@]}"; do
  if [[ ! -f "$t" ]]; then
    echo "::error::$t does not exist — run scripts/sync-rule-pointers.sh" >&2
    fail=1
    continue
  fi
  if ! diff -u -w "$SOURCE" "$t" > /dev/null 2>&1; then
    echo "::error::$t is out of sync with $SOURCE — run scripts/sync-rule-pointers.sh" >&2
    fail=1
  fi
done

exit "$fail"
