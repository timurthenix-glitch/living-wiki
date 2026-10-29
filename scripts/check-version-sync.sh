#!/usr/bin/env bash
# С v1 версия — константа: единая версия, без подверсий и минорных номеров.
# Свежесть развёрнутой копии определяется rules_hash (scripts/rules-hash.sh),
# а не цифрой. Здесь проверяются только согласованность маркера и semver пакета:
#  1. AGENTS.md bootstrap_version и inline «skill vN» в SKILL.md — одно и то же
#     число в двух форматах, обязаны совпадать.
#  2. plugin.json (major.minor) >= версии схемы: Claude Code предлагает
#     обновление только когда растёт version в plugin.json.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

plugin_version=$(grep -oE '"version"[[:space:]]*:[[:space:]]*"[0-9]+\.[0-9]+' .claude-plugin/plugin.json | grep -oE '[0-9]+\.[0-9]+$')
agents_raw=$(grep -oE '^bootstrap_version:[[:space:]]*v?[0-9]+(\.[0-9]+)?' AGENTS.md | grep -oE '[0-9]+(\.[0-9]+)?$')
skill_raw=$(grep -oE '\(skill v[0-9]+(\.[0-9]+)?\)' skills/living-wiki/SKILL.md | grep -oE '[0-9]+(\.[0-9]+)?')

echo "plugin.json version (major.minor): $plugin_version"
echo "AGENTS.md bootstrap_version:        ${agents_raw:-<none>}"
echo "SKILL.md skill version:             ${skill_raw:-<none>}"

if [[ -z "$plugin_version" || -z "$agents_raw" || -z "$skill_raw" ]]; then
  echo "::error::Could not extract one of the version fields — check the source files haven't changed format." >&2
  exit 1
fi

normalize() {
  local v="$1"
  [[ "$v" == *.* ]] || v="${v}.0"
  printf '%s' "$v"
}

if [[ "$(normalize "$agents_raw")" != "$(normalize "$skill_raw")" ]]; then
  echo "::error::AGENTS.md bootstrap_version ($agents_raw) and SKILL.md (skill v$skill_raw) must match exactly — they are the same marker." >&2
  exit 1
fi

# Версии сравниваются через `sort -V`, а не арифметикой shell: на старых
# сборках Git Bash/MSYS `test -lt` и `(( ))` дают неверный порядок для 1.2/1.10.
# Семантика semver: 1.10 > 1.2, 2.0 > 1.99, 1.1.1 >= 1.
ver_ge() {
  local a="$1" b="$2" top
  [[ "$a" == *.* ]] || a="${a}.0"
  [[ "$b" == *.* ]] || b="${b}.0"
  top=$(printf '%s\n%s\n' "$a" "$b" | sort -V | tail -n1)
  [[ "$top" == "$a" ]]
}

if ! ver_ge "$plugin_version" "$(normalize "$agents_raw")"; then
  echo "::error::plugin.json version ($plugin_version) is behind the rules marker ($agents_raw) — bump plugin.json." >&2
  exit 1
fi

echo "OK: plugin.json ($plugin_version) >= rules marker ($agents_raw); AGENTS.md/SKILL.md agree ($agents_raw)"
