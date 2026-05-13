#!/usr/bin/env bash
# Update `.claude-plugin/plugin.json`'s `version` field in place.
#
# Usage: scripts/update-plugin-version.sh <version>
#
# Claude Code's plugin marketplace only fetches plugin updates when the
# manifest's `version` field changes. Wire this to the dev version
# (`scripts/compute-dev-version.sh`) at release time so users on the plugin
# actually get our latest skill content.
#
# Idempotent — if the file already matches, this is a no-op.
# Requires: jq (standard in GitHub Actions runners and most dev environments).
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <version>" >&2
  exit 2
fi

version="$1"

# Resolve the manifest path relative to the repo root (this script lives in
# scripts/, so go one directory up).
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
manifest="${script_dir}/../.claude-plugin/plugin.json"

if [[ ! -f "$manifest" ]]; then
  echo "update-plugin-version: manifest not found at $manifest" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "update-plugin-version: jq is required but not installed" >&2
  exit 1
fi

current=$(jq -r '.version' "$manifest")
if [[ "$current" == "$version" ]]; then
  echo "update-plugin-version: already at $version, no change"
  exit 0
fi

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
jq --arg v "$version" '.version = $v' "$manifest" > "$tmp"
mv "$tmp" "$manifest"
trap - EXIT

echo "update-plugin-version: $current -> $version"
