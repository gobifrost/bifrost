#!/usr/bin/env bash
# Render the reviewed release body before a version tag exists.
set -euo pipefail

if [[ $# -ne 4 ]]; then
    echo "Usage: $0 <tag> <40-character-target-sha> <40-character-content-sha> <output-body.md>" >&2
    exit 2
fi

tag="$1"
target="$2"
content_ref="$3"
output="$4"
version="${tag#v}"

if [[ "$tag" != v* ]]; then
    echo "tag must start with v: $tag" >&2
    exit 2
fi
if [[ ! "$target" =~ ^[0-9a-f]{40}$ ]]; then
    echo "target must be a 40-character lowercase commit SHA" >&2
    exit 2
fi
if [[ ! "$content_ref" =~ ^[0-9a-f]{40}$ ]]; then
    echo "content ref must be a 40-character lowercase commit SHA" >&2
    exit 2
fi
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
bundle="$tmp_dir/bundle.json"
product_body="$tmp_dir/product-updates.md"
review_material="$tmp_dir/release-review.md"

python3 -m scripts.release_gate \
    --content-dir product-updates \
    --target "$target" \
    --output "$review_material"
if ! git merge-base --is-ancestor "$target" "$content_ref"; then
    echo "content ref must descend from the frozen target" >&2
    exit 2
fi
if ! git cat-file -e "$content_ref:product-updates" 2>/dev/null; then
    echo "content ref does not contain product-updates assets" >&2
    exit 2
fi
unexpected_changes="$(git diff --name-only "$target..$content_ref" -- . ':!product-updates/**')"
if [[ -n "$unexpected_changes" ]]; then
    echo "content ref includes code changes after the frozen target:" >&2
    echo "$unexpected_changes" >&2
    exit 2
fi
python3 scripts/product_updates.py validate \
    --content-dir product-updates \
    --inventory product-updates/inventory.json \
    --dispositions product-updates/dispositions.json \
    --target "$target"
python3 scripts/product_updates.py bundle \
    --content-dir product-updates \
    --inventory product-updates/inventory.json \
    --dispositions product-updates/dispositions.json \
    --target "$target" \
    --content-ref "$content_ref" \
    --output "$bundle"
while IFS= read -r asset; do
    if ! git cat-file -e "$content_ref:product-updates/$asset" 2>/dev/null; then
        echo "content ref does not contain bundled asset: $asset" >&2
        exit 2
    fi
done < <(jq -r '.entries[].assets[].path' "$bundle")
python3 scripts/product_updates.py render-release \
    --bundle "$bundle" \
    --asset-base-url "https://raw.githubusercontent.com/gobifrost/bifrost/${content_ref}/" \
    --output "$product_body"

mkdir -p "$(dirname "$output")"
awk -v target="$target" -v content_ref="$content_ref" \
    'BEGIN { printf "<!-- product-updates: target=%s content-ref=%s -->\n\n", target, content_ref } { print }' \
    "$product_body" > "$output"
printf '\n' >> "$output"
awk '{ print }' "$review_material" >> "$output"
printf '%s\n' \
    '' \
    '## Docker Images' \
    '' \
    '**API:**' \
    '```bash' \
    "docker pull ghcr.io/gobifrost/bifrost-api:${version}" \
    '```' \
    '' \
    '**Client:**' \
    '```bash' \
    "docker pull ghcr.io/gobifrost/bifrost-client:${version}" \
    '```' \
    '' \
    '## Type Stubs for IDEs' \
    '' \
    'Download `bifrost.pyi` from the release assets and place it in your workspace for IDE autocomplete and type checking. See `DISTRIBUTION.md` for instructions.' \
    '' \
    '## Signed Artifacts (Sigstore / cosign — keyless)' \
    '' \
    'Each release asset has a matching `.sigstore` bundle containing its keyless OIDC signature, certificate, and Rekor inclusion proof.' \
    '' \
    '```bash' \
    "cosign verify-blob --bundle bifrost-${tag}-source.tar.gz.sigstore --certificate-identity-regexp 'https://github\\.com/gobifrost/bifrost/.*' --certificate-oidc-issuer https://token.actions.githubusercontent.com bifrost-${tag}-source.tar.gz" \
    '```' \
    '' \
    'A SLSA build-provenance attestation is also published:' \
    '' \
    '```bash' \
    "gh attestation verify bifrost-${tag}-source.tar.gz --owner gobifrost" \
    '```' \
    >> "$output"
