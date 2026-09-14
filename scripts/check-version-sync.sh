#!/usr/bin/env bash
# Fails if the plugin's semver (major.minor) and the bootstrap_version used
# inside AGENTS.md / SKILL.md have drifted apart. See CHANGELOG.md for why
# there are two places this version is recorded.
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

if [[ "$plugin_version" != "$agents_version" || "$agents_version" != "$skill_version" ]]; then
  echo "::error::Version mismatch: plugin.json ($plugin_version) vs AGENTS.md bootstrap_version ($agents_version) vs SKILL.md ($skill_version) must share the same major.minor." >&2
  exit 1
fi

echo "OK: versions in sync ($plugin_version)"
