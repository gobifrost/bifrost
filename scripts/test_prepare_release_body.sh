#!/usr/bin/env bash
# Regression guard for the deterministic pre-tag release-body entrypoint.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
set +e
output="$(cd "$repo_root" && ./scripts/prepare-release-body.sh 2>&1)"
status=$?
set -e

if [[ "$status" -eq 0 || "$output" != *"Usage:"* ]]; then
    echo "prepare-release-body must reject an incomplete invocation" >&2
    echo "$output" >&2
    exit 1
fi

target="$(jq -r .target_ref "$repo_root/product-updates/inventory.json")"
content_ref="$(git -C "$repo_root" rev-parse HEAD)"
body="${TMPDIR:-/tmp}/bifrost-release-body-$$.md"
trap 'rm -f "$body"' EXIT
set +e
output="$(cd "$repo_root" && ./scripts/prepare-release-body.sh v999.0.0 "$target" "$content_ref" "$body" 2>&1)"
status=$?
set -e

# The checked-in backfill is intentionally draft. A release helper must reject
# it rather than emitting a plausible-looking empty release body.
if [[ "$status" -eq 0 || "$output" != *"missing required review record"* ]]; then
    echo "prepare-release-body must block unreviewed release material" >&2
    echo "$output" >&2
    exit 1
fi

fixture="$(mktemp -d)"
trap 'rm -rf "$fixture" "$body"' EXIT
mkdir -p "$fixture/scripts" "$fixture/product-updates/entries"
cp "$repo_root/scripts/product_updates.py" "$repo_root/scripts/release_gate.py" \
    "$repo_root/scripts/prepare-release-body.sh" "$fixture/scripts/"
cp "$repo_root/product-updates/schema.json" "$fixture/product-updates/"
git -C "$fixture" init --quiet
git -C "$fixture" config user.email release-test@example.invalid
git -C "$fixture" config user.name release-test
git -C "$fixture" add scripts product-updates/schema.json
git -C "$fixture" commit --quiet -m base
fixture_base="$(git -C "$fixture" rev-parse HEAD)"
git -C "$fixture" commit --quiet --allow-empty -m target
fixture_target="$(git -C "$fixture" rev-parse HEAD)"
FIXTURE_TARGET="$fixture_target" FIXTURE_BASE="$fixture_base" python3 - "$fixture/product-updates" <<'PY'
import json
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
target = os.environ["FIXTURE_TARGET"]
base = os.environ["FIXTURE_BASE"]
entry = "11111111-1111-4111-8111-111111111111"
(root / "inventory.json").write_text(json.dumps({
    "schema_version": 1, "repository": "gobifrost/bifrost", "base_ref": base,
    "target_ref": target, "collected_at": "2026-01-01T00:00:00Z",
    "source_ref": "https://api.github.com/repos/gobifrost/bifrost/compare/test",
    "commits": [{"sha": target, "subject": "Reviewed change (#1)", "prs": [1]}],
    "prs": [{"number": 1, "title": "Reviewed change", "url": "https://github.com/gobifrost/bifrost/pull/1", "author": {"login": "octo", "type": "User"}, "merged_at": "2026-01-01T00:00:00Z", "merge_commit": target, "metadata_ref": "https://api.github.com/repos/gobifrost/bifrost/pulls/1"}],
}) + "\n")
(root / "dispositions.json").write_text(json.dumps({
    "schema_version": 1, "inventory": {"base_ref": base, "target_ref": target},
    "items": {"pr:1": {"classification": "highlight", "entry_ids": [entry], "review": {"status": "approved", "evidence": ["review"]}, "security_review": {"status": "required", "review_ref": "https://github.com/gobifrost/bifrost/security/advisories/1"}, "action_required": True}},
}) + "\n")
metadata = {"id": entry, "revision": 1, "published_at": "2026-01-01T00:00:00Z", "title": "Reviewed change", "visibility": "highlight", "area": "Platform", "type": "Security", "action_required": True, "security_review": {"status": "required", "review_ref": "https://github.com/gobifrost/bifrost/security/advisories/1"}, "sources": [{"pr": 1}], "contributors": [{"login": "octo", "profile_url": "https://github.com/octo", "source_pr": 1}], "eligibility": {"requires_prs": [1], "requires_commits": []}, "assets": [], "review": {"status": "approved", "evidence": ["review"]}}
(root / "entries" / f"{entry}.md").write_text("---json\n" + json.dumps(metadata) + "\n---\nReviewed release prose.\n")
(root / "release-review.json").write_text(json.dumps({"target_ref": target, "review": {"status": "approved", "evidence": ["release review"]}, "security": {"status": "approved", "evidence": ["advisory review"], "fixed_cves": ["None in this release"]}, "breaking_changes": {"status": "approved", "evidence": ["compatibility review"], "summary": "None in this release"}}) + "\n")
PY
git -C "$fixture" add product-updates
git -C "$fixture" commit --quiet -m 'reviewed release material'
fixture_ref="$(git -C "$fixture" rev-parse HEAD)"
(cd "$fixture" && ./scripts/prepare-release-body.sh v999.0.0 "$fixture_target" "$fixture_ref" release.md)
[[ "$(head -n 1 "$fixture/release.md")" == "<!-- product-updates: target=${fixture_target} content-ref=${fixture_ref} -->" ]]
grep -q '^## Fixed CVEs$' "$fixture/release.md"
grep -q '^## Docker Images$' "$fixture/release.md"
