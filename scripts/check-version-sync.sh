#!/usr/bin/env bash
# С v1/v2 версия — константа схемы: единая версия, без подверсий и минорных номеров.
# Свежесть развёрнутой копии определяется rules_hash (scripts/rules-hash.sh),
# а не цифрой. Здесь проверяются только согласованность маркера и semver пакета:
#  1. AGENTS.md bootstrap_version и inline «skill vN» в SKILL.md — одно и то же
#     число в двух форматах, обязаны совпадать (если SKILL.md присутствует).
#  2. plugin.json (major.minor) >= версии схемы: Claude Code предлагает
#     обновление только когда растёт version в plugin.json.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

plugin_version=$(grep -oE '"version"[[:space:]]*:[[:space:]]*"[0-9]+\.[0-9]+' .claude-plugin/plugin.json | grep -oE '[0-9]+\.[0-9]+$')
agents_raw=$(grep -oE '^bootstrap_version:[[:space:]]*v?[0-9]+(\.[0-9]+)?' AGENTS.md | grep -oE '[0-9]+(\.[0-9]+)?$')

echo "plugin.json version (major.minor): $plugin_version"
echo "AGENTS.md bootstrap_version:        ${agents_raw:-<none>}"

if [[ -z "$plugin_version" || -z "$agents_raw" ]]; then
  echo "::error::Could not extract version fields — check plugin.json or AGENTS.md format." >&2
  exit 1
fi

normalize() {
  local v="$1"
  [[ "$v" == *.* ]] || v="${v}.0"
  printf '%s' "$v"
}

if [[ -f "skills/living-wiki/SKILL.md" ]]; then
  skill_raw=$(grep -oE '\(skill v[0-9]+(\.[0-9]+)?\)' skills/living-wiki/SKILL.md | grep -oE '[0-9]+(\.[0-9]+)?' || true)
  echo "SKILL.md skill version:             ${skill_raw:-<none>}"
  if [[ -n "$skill_raw" && "$(normalize "$agents_raw")" != "$(normalize "$skill_raw")" ]]; then
    echo "::error::AGENTS.md bootstrap_version ($agents_raw) and SKILL.md (skill v$skill_raw) must match exactly — they are the same marker." >&2
    exit 1
  fi
fi

ver_ge() {
  local a_major=${1%%.*} a_minor=${1#*.} b_major=${2%%.*} b_minor=${2#*.}
  if (( 10#$a_major != 10#$b_major )); then
    (( 10#$a_major > 10#$b_major ))
  else
    (( 10#$a_minor >= 10#$b_minor ))
  fi
}

if ! ver_ge "$plugin_version" "$(normalize "$agents_raw")"; then
  echo "::error::plugin.json version ($plugin_version) is behind the rules marker ($agents_raw) — bump plugin.json." >&2
  exit 1
fi

echo "OK: plugin.json ($plugin_version) >= rules marker ($agents_raw)"
