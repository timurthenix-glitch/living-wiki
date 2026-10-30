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
  ".clinerules"
  ".cursor/rules/living-wiki.md"
  ".github/copilot-instructions.md"
  ".junie/guidelines.md"
  ".kiro/steering/living-wiki.md"
  ".qoder/rules/living-wiki.md"
  ".windsurf/rules/living-wiki.md"
)

# Clean up invalid legacy directory if present
if [[ -d ".clinerules" ]]; then
  echo "Removing legacy .clinerules directory..."
  rm -rf ".clinerules"
fi

for t in "${TARGETS[@]}"; do
  dir="$(dirname "$t")"
  if [[ "$dir" != "." ]]; then
    mkdir -p "$dir"
  fi
  cp "$SOURCE" "$t"
  echo "synced $t"
done
