#!/usr/bin/env bash
# Update all Bifrost plugin manifest `version` fields in place.
#
# Usage: scripts/update-plugin-version.sh <version>
#
# Claude Code and Codex plugin marketplaces key installed content by manifest
# version. Wire this to the release version at tag time so users on the
# installed plugin actually get our latest skill content.
#
# Idempotent — if a manifest already matches, that file is a no-op.
# Requires: jq (standard in GitHub Actions runners and most dev environments).
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <version>" >&2
  exit 2
fi

version="$1"

# Resolve manifest paths relative to the repo root (this script lives in
# scripts/, so go one directory up).
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="${script_dir}/.."
manifests=(
  "${repo_root}/.claude-plugin/plugin.json"
  "${repo_root}/.codex-plugin/plugin.json"
  "${repo_root}/plugins/bifrost/.codex-plugin/plugin.json"
)

for manifest in "${manifests[@]}"; do
  if [[ ! -f "$manifest" ]]; then
    echo "update-plugin-version: manifest not found at $manifest" >&2
    exit 1
  fi
done

if ! command -v jq >/dev/null 2>&1; then
  echo "update-plugin-version: jq is required but not installed" >&2
  exit 1
fi

for manifest in "${manifests[@]}"; do
  current=$(jq -r '.version' "$manifest")
  if [[ "$current" == "$version" ]]; then
    echo "update-plugin-version: ${manifest#"$repo_root"/} already at $version"
    continue
  fi

  tmp=$(mktemp)
  jq --arg v "$version" '.version = $v' "$manifest" > "$tmp"
  mv "$tmp" "$manifest"

  echo "update-plugin-version: ${manifest#"$repo_root"/}: $current -> $version"
done
