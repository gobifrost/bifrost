# Product Updates Authoring

`product-updates/` is the single, version-controlled source for approved
What's new entries and GitHub release summaries. The same entry prose is
bundled for the admin experience and rendered into a release body; do not keep
a second release-notes draft elsewhere.

## Before and during a pull request

Classify every landed change in `product-updates/dispositions.json` under its
`pr:<number>` key. The disposition is the durable decision read by CI and the
merge queue; the PR template records the author’s proposal, but is never the
canonical classification.

- `highlight` points to one or more entry UUIDs in `entries/`. Use a highlight
  for a capability, important correction, upgrade warning, or security notice
  people should see in the product or release notes.
- `other` records a concise, user-readable change without a full entry.
- `omit` requires a concrete reason. Use it only when there is no announcement
  value, such as internal CI work. Routine dependency updates are normally
  `other`; security-impacting updates require explicit review.

An entry may be drafted before its PR exists. Add its actual PR number and
verified author/source metadata after the PR opens, then validate it before
merge. Do not guess a future PR number or contributor identity.

Use one UUID for a feature spanning several PRs and list every required source
in its eligibility data. Editorial fixes retain that UUID and increase its
revision. A new capability or materially different corrective announcement
gets a new UUID. One primary area and type are required; `highlight`, `other`,
and `omit` are visibility decisions, not areas or types.

Security disclosures and action-required or breaking updates require explicit
review evidence. Entry Markdown is reviewed prose, not generated at release
time. Assets live beneath the entry's asset directory, have useful alt text,
and must not contain executable HTML or embeds.

## Local validation and previews

The deterministic tool never calls an LLM or GitHub. It uses the committed
inventory and verified metadata, so local and CI results agree:

```bash
python3 scripts/product_updates.py validate \
  --content-dir product-updates \
  --inventory product-updates/inventory.json \
  --dispositions product-updates/dispositions.json \
  --target "$(jq -r .target_ref product-updates/inventory.json)"

# Draft backfill prose is preview-only until its review status becomes approved.
python3 scripts/product_updates.py preview \
  --content-dir product-updates \
  --draft-dir product-updates/drafts/initial-backfill \
  --allow-draft \
  --inventory product-updates/inventory.json \
  --dispositions product-updates/dispositions.json \
  --target <frozen-40-character-sha> \
  --output /tmp/product-updates-preview.json
```

Do not pass `--allow-draft` to ordinary CI, image builds, candidates, or a
release. Draft content is useful for review and preview only; approval is a
durable review status, not a checked PR-template box.

## Preparing a release

Before a candidate or final tag, use `collect-inventory` to freeze the landed
range. The target remains the applicability boundary; the later content ref is
only the commit that contains reviewed prose and assets.

```bash
PREVIOUS_FINAL=$(gh release list --limit 100 --json tagName,isDraft,isPrerelease,publishedAt \
  --jq '[.[] | select(.isDraft | not) | select(.isPrerelease | not)] | sort_by(.publishedAt) | last.tagName')
BASE=$(git rev-list -n1 "$PREVIOUS_FINAL")
TARGET=$(git rev-parse HEAD)
python3 scripts/product_updates.py collect-inventory \
  --base "$BASE" --target "$TARGET" --repository gobifrost/bifrost \
  --output product-updates/inventory.json

# Review and approve the resulting dispositions and entries before preparing.
TARGET=$(jq -r .target_ref product-updates/inventory.json)
CONTENT_REF=$(git rev-parse HEAD)
python3 scripts/product_updates.py validate \
  --content-dir product-updates \
  --inventory product-updates/inventory.json \
  --dispositions product-updates/dispositions.json
  --target "$TARGET"
./scripts/prepare-release-body.sh vX.Y.Z "$TARGET" "$CONTENT_REF" \
  product-updates/release-bodies/vX.Y.Z.md
```

`prepare-release-body.sh` verifies the persisted `release-review.json`: every
covered source disposition is approved, highlight sources have approved
canonical entries, and security/action-required sources retain corresponding
entry material. It also requires reviewed CVE and breaking-change findings,
including an explicit `None in this release` finding where applicable.

Review and commit the generated body before creating the tag. The release
workflow publishes that exact body alongside the existing Docker, type-stub,
and signed-artifact material. `./scripts/release-check.sh vX.Y.Z
--release-body product-updates/release-bodies/vX.Y.Z.md` rejects a missing,
untracked, or stale body; the release skill contains the full release gate.

The initial backfill remains draft until its prose, source claims, security
review, and upgrade guidance are approved. Its frozen inventory is evidence of
coverage, not approved release content.
