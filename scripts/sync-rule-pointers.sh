#!/usr/bin/env bash
# Copies templates/rule-pointer.md into every tool-specific pointer-file
# location so they can never silently drift apart. Run this after editing
# the template. scripts/check-rule-pointers.sh (used in CI) verifies the
# copies still match without writing anything.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

SOURCE="templates/rule-pointer.md"
TARGETS=(
  ".agents/rules/living-wiki.md"
  ".clinerules/living-wiki.md"
  ".cursor/rules/living-wiki.md"
  ".github/copilot-instructions.md"
  ".junie/guidelines.md"
  ".kiro/steering/living-wiki.md"
  ".qoder/rules/living-wiki.md"
  ".windsurf/rules/living-wiki.md"
)

for t in "${TARGETS[@]}"; do
  mkdir -p "$(dirname "$t")"
  cp "$SOURCE" "$t"
  echo "synced $t"
done
