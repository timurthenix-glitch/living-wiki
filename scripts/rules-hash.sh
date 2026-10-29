#!/usr/bin/env bash
# rules_hash — отпечаток слоя инструкций (AGENTS.md + skills/living-wiki/**),
# который заменяет номер версии: с v1 версия не меняется никогда, а устарела ли
# копия в проекте — видно по этому хэшу.
#
#   scripts/rules-hash.sh          — напечатать актуальный хэш
#   scripts/rules-hash.sh --check  — сверить его с frontmatter AGENTS.md (CI)
#
# Хэш считается по содержимому без строки rules_hash (иначе поле ссылалось бы
# на само себя) и с нормализацией CRLF → LF, чтобы checkout на Windows и на
# Linux давал одно и то же значение.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

hash_rules() {
  {
    grep -v '^rules_hash:' AGENTS.md | tr -d '\r'
    find skills/living-wiki -type f -name '*.md' | LC_ALL=C sort | while IFS= read -r f; do
      printf '=== %s\n' "$f"
      tr -d '\r' < "$f"
    done
  } | sha256sum | cut -d' ' -f1
}

computed=$(hash_rules)
stored=$(grep -oE '^rules_hash:[[:space:]]*[0-9a-f]+' AGENTS.md | awk '{print $2}' || true)

if [[ "${1:-}" == "--check" ]]; then
  if [[ -z "$stored" ]]; then
    echo "::error::В frontmatter AGENTS.md нет rules_hash — добавь его через scripts/rules-hash.sh." >&2
    exit 1
  fi
  if [[ "$computed" != "$stored" ]]; then
    echo "::error::rules_hash в AGENTS.md ($stored) не совпадает с содержимым слоя ($computed) — прогони scripts/rules-hash.sh и впиши новое значение." >&2
    exit 1
  fi
  echo "OK: rules_hash $computed соответствует содержимому"
else
  echo "$computed"
fi
