#!/usr/bin/env bash
# Compute a monotonic semver dev version for the current commit.
#
# Format: <next-patch>-dev.<commits-since-tag>
# Example: latest tag v0.8.0, 47 commits ahead -> 0.8.1-dev.47
#
# Requires: a `v<MAJOR>.<MINOR>.<PATCH>` annotated tag in history.
# Exits non-zero with a diagnostic on stderr if no usable tag exists.
set -euo pipefail

last_tag=$(git describe --abbrev=0 --match "v[0-9]*.[0-9]*.[0-9]*" 2>/dev/null || true)
if [[ -z "$last_tag" ]]; then
  echo "compute-dev-version: no v* tag found in history" >&2
  exit 1
fi

if ! [[ "$last_tag" =~ ^v([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
  echo "compute-dev-version: latest tag '$last_tag' is not vMAJOR.MINOR.PATCH" >&2
  exit 1
fi

major="${BASH_REMATCH[1]}"
minor="${BASH_REMATCH[2]}"
patch="${BASH_REMATCH[3]}"
next_patch=$((patch + 1))

commits=$(git rev-list "${last_tag}..HEAD" --count)

printf '%s.%s.%s-dev.%s\n' "$major" "$minor" "$next_patch" "$commits"
