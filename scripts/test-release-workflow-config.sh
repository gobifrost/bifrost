#!/usr/bin/env bash
# Static checks for fork-local image publication in .github/workflows/ci.yml.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workflow="$repo_root/.github/workflows/ci.yml"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

grep -q "IMAGE_NAMESPACE: mtg-thomas" "$workflow" \
  || fail "ci.yml must publish fork images under ghcr.io/mtg-thomas"

if grep -q "jackmusick/bifrost-\(api\|client\)" "$workflow"; then
  fail "ci.yml still publishes Bifrost images under jackmusick"
fi

grep -q 'ghcr.io/${{ env.IMAGE_NAMESPACE }}/${{ env.API_IMAGE }}' "$workflow" \
  || fail "API image refs must use IMAGE_NAMESPACE + API_IMAGE"

grep -q 'ghcr.io/${{ env.IMAGE_NAMESPACE }}/${{ env.CLIENT_IMAGE }}' "$workflow" \
  || fail "client image refs must use IMAGE_NAMESPACE + CLIENT_IMAGE"

echo "release workflow config checks passed"
