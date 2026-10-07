# Product Updates Authoring

`product-updates/` is the single, version-controlled source for approved
What's new entries and GitHub release summaries. The same entry prose is
bundled for the admin experience and rendered into a release body; do not keep
a second release-notes draft elsewhere.

## Copy Standard

Use General Writing with Humanizer for entry prose when those skills are available.
Calibrate against Jack's example: “See a user's effective access.” Apply this
standard regardless of skill availability: short concrete title, one or two
sentences stating the change, and a useful app link or screenshot. Cut background,
implementation inventories, promotional claims, repeated summaries, and generic
“review this update” instructions. Put necessary upgrade actions in the note.

Validation enforces at most 10 title words and 60 body words (100 for Security or
Action Required). Image markup and URL targets do not consume the body budget.
A word limit checks length, not quality: the human content review must also check
this standard and retain copy-review evidence in the existing review record.

Set `action_required: true` only when an affected customer must take a concrete
step to keep an existing setup working after the upgrade. Name that audience,
state the required step, and retain evidence of the incompatibility. Optional
settings choices, ordinary workflow guidance, stronger access controls, and
internal release-review tasks do not earn this label. Explain consequential
defaults plainly even when no action is required.

Use verified installation-relative routes for app links. All links in the app
notes open in a new tab. GitHub rendering keeps app-link labels as plain directions
because each installation has its own URL; it never invents a deployment URL.

## Customer-Facing Selection

Feature headlines are for new capabilities and meaningful functionality people can
use. Do not promote internal plumbing, catalog vendor names, routine dependencies,
or maintenance inventories to feature notes. Use `Fixed` for repairs and
`Security` for hardening; the app presents these as separate compact bullet lists.
Smaller `other` dispositions use `category: fix` or `category: hardening` and a short
customer-facing summary. Omitted PRs stay in the coverage ledger with a reason.

A required release notice can set `in_app: false` to keep detailed release-review
material out of the app feed. This does not omit its source, remove its release
body, bypass security review, or weaken the release gate. Necessary customer
upgrade steps stay visible in the appropriate note. Do not describe strengthened
access controls as an incident unless the evidence establishes an incident.

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
  value, such as internal CI work. Routine dependency, catalog, container, and CI updates are normally
  omitted from customer notes; security-impacting updates still require explicit review.

An entry may be drafted before its PR exists. Add its actual PR number and
verified author/source metadata after the PR opens, then validate it before
merge. Do not guess a future PR number or contributor identity.

Use one UUID for a feature spanning several PRs and list every required source
in its eligibility data. Editorial fixes retain that UUID and increase its
revision. A new capability or materially different corrective announcement
gets a new UUID. One primary area and type are required; `highlight`, `other`,
and `omit` are visibility decisions, not areas or types.

A pending source does not prevent structural validation or a development build.
The renderer withholds its highlight until every cited source and declared
prerequisite is present in the verified metadata and reachable from the build.
Refresh cached metadata after landing before publishing that highlight. Approved
landed portions of a pending group appear as Other changes using their persisted
summary (or cached PR title); omitted and unreviewed sources stay excluded.
Security and action-required sources cannot use this smaller-change treatment
for a release: their required canonical notice must be eligible.

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
