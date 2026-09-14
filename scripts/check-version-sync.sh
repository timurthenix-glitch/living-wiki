#!/usr/bin/env bash
# Two invariants, not one:
#  1. AGENTS.md bootstrap_version and SKILL.md's inline "skill vX.Y" must be
#     IDENTICAL — they're two copies of the same vault-schema version number,
#     duplicated only because the skill file format requires frontmatter
#     instead of a heading (see CHANGELOG.md).
#  2. plugin.json's semver (major.minor) must be >= that schema version, not
#     equal to it. The package is free to ship ahead of the schema (hooks,
#     docs, examples, license — anything that doesn't touch the vault
#     contract), because that's the only way Claude Code's own update
#     detection (which fires on any plugin.json version bump) ever sees a
#     packaging-only release. It must never fall BEHIND the schema version,
#     though: if bootstrap_version ever bumps without a matching package
#     bump, installed copies won't be offered the real schema update either.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

plugin_version=$(grep -oE '"version"[[:space:]]*:[[:space:]]*"[0-9]+\.[0-9]+' .claude-plugin/plugin.json | grep -oE '[0-9]+\.[0-9]+$')
agents_version=$(grep -oE '^bootstrap_version:[[:space:]]*v?[0-9]+\.[0-9]+' AGENTS.md | grep -oE '[0-9]+\.[0-9]+$')
skill_version=$(grep -oE '\(skill v[0-9]+\.[0-9]+\)' skills/living-wiki/SKILL.md | grep -oE '[0-9]+\.[0-9]+')

echo "plugin.json version (major.minor): $plugin_version"
echo "AGENTS.md bootstrap_version:        $agents_version"
echo "SKILL.md skill version:             $skill_version"

if [[ -z "$plugin_version" || -z "$agents_version" || -z "$skill_version" ]]; then
  echo "::error::Could not extract one of the version fields — check the source files haven't changed format." >&2
  exit 1
fi

if [[ "$agents_version" != "$skill_version" ]]; then
  echo "::error::AGENTS.md bootstrap_version ($agents_version) and SKILL.md (skill v$skill_version) must match exactly — they describe the same vault-schema contract." >&2
  exit 1
fi

ver_ge() {
  local a_major=${1%%.*} a_minor=${1#*.} b_major=${2%%.*} b_minor=${2#*.}
  if (( 10#$a_major != 10#$b_major )); then
    (( 10#$a_major > 10#$b_major ))
  else
    (( 10#$a_minor >= 10#$b_minor ))
  fi
}

if ! ver_ge "$plugin_version" "$agents_version"; then
  echo "::error::plugin.json version ($plugin_version) is behind the vault-schema version ($agents_version). Claude Code only offers an update when plugin.json's version increases, so the package version must never trail the schema version — bump plugin.json to at least $agents_version." >&2
  exit 1
fi

echo "OK: plugin.json ($plugin_version) >= schema version ($agents_version); AGENTS.md/SKILL.md agree ($agents_version)"
