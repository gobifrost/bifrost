#!/usr/bin/env bash
# Regression guard for the pre-tag reviewed release body requirement.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
missing_body="${TMPDIR:-/tmp}/bifrost-release-check-missing-body-$$.md"

set +e
output="$(cd "$repo_root" && timeout 2s ./scripts/release-check.sh v999.0.0 --release-body "$missing_body" 2>&1)"
status=$?
set -e

if [[ "$status" -eq 0 ]]; then
    echo "release-check accepted a missing prepared release body" >&2
    exit 1
fi

if [[ "$output" != *"Prepared release body is required"* ]]; then
    echo "release-check did not explain the missing prepared release body:" >&2
    echo "$output" >&2
    exit 1
fi
