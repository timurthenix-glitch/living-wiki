#!/usr/bin/env bash
# rules_hash — отпечаток слоя инструкций (AGENTS.md),
# который заменяет номер версии: с v1 версия не меняется никогда, а устарела ли
# копия в проекте — видно по этому хэшу.
#
#   scripts/rules-hash.sh          — напечатать актуальный хэш
#   scripts/rules-hash.sh --check  — сверить его с frontmatter AGENTS.md (CI)
#
# Хэш считается по содержимому AGENTS.md без строки rules_hash (иначе поле ссылалось бы
# на само себя) и с нормализацией CRLF → LF, чтобы checkout на Windows и на
# Linux давал одно и то же значение.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

hash_rules() {
  grep -v '^rules_hash:' AGENTS.md | tr -d '\r' | sha256sum | cut -d' ' -f1
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
elif [[ "${1:-}" == "--update" ]]; then
  sed -i.bak -E "s/^rules_hash:[[:space:]]*[0-9a-f]+/rules_hash: $computed/" AGENTS.md
  rm -f AGENTS.md.bak
  echo "Updated rules_hash in AGENTS.md to $computed"
else
  echo "$computed"
fi
