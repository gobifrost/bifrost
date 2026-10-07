---
name: bifrost:release
description: Prepare Bifrost release data, build, and release. Use when adding or aggregating release notes, pushing commits to main, cutting a versioned release, or deploying to K8s. Handles dev push (CI builds :dev image), pre-release (vX.Y.Z-rc.N tag → pre-release GitHub Release), and full release (version tag → GitHub Release + :latest).
---

# Bifrost Release

## Release cadence (the three rungs, and who they're for)

Bifrost ships on a deliberate three-rung ladder. Know which audience each rung serves before you cut it:

| Rung | Tag / image | Cadence | Stability promise | Who runs it |
|------|-------------|---------|-------------------|-------------|
| **dev** | `:dev` (every merge to main) | Continuous | **Bleeding edge. Expect bugs.** This is where the maintainer flushes out defects in his own production before they reach anyone else. | The maintainer's prod + community members who want the very latest and accept breakage. |
| **pre-release** | `vX.Y.Z-rc.N` → versioned images, GitHub Release marked *pre-release*, **no `:latest`** | Roughly monthly, **in between** full releases | **Safer than dev** — a candidate that's been through the gates and is being soak-tested, but not yet blessed as final. | Operators who want fresher-than-monthly without riding `:dev`. |
| **full release** | `vX.Y.Z` → versioned images + `:latest` + final GitHub Release | Roughly monthly | **Blessed/stable.** The default for production installs. | Everyone on `:latest`. |

The intent going forward (announce this in the first full release that introduces it): **full releases land roughly monthly; between them we cut `-rc.N` pre-releases that are intended to be safer but more frequent. `:dev` remains bleeding edge and will contain bugs the maintainer intends to find in his own production first.** When you draft notes for the release that introduces this cadence, include a short "Release cadence going forward" callout stating exactly that.

## Release Data and Publication

Treat content preparation and publication as separate operations. If asked only to
add or prepare release data, stop after a reviewable committed draft, coverage
reconciliation, validation, and previews. That request does not authorize a tag,
GitHub Release, deployment, Discord post, or publication approval.

Write each noteworthy change once in `product-updates/` during its PR. Read
`docs/product-updates-authoring.md` and apply its General Writing/Humanizer pass,
short-note format, verified app links, and enforced word budgets. Before a formal
release, aggregate the eligible approved entries since the previous final and
freeze the complete body. Publishing consumes that prepared body without a new
writing pass. Draft or staged entries never activate production announcements.

After a source PR lands, refresh the cumulative verified inventory and commit the
metadata in a content follow-up PR before considering its announcement delivered.
Keep its reviewed UUID and prose; cite the actual landed PR/commit. Validate the
prepared image bundle includes the entry. This activation step applies to dev
images too and does not require publishing a GitHub release.

The in-app trigger is unseen approved entry UUIDs in the running build, checked
when an authenticated platform admin enters the shell. A displayed batch is
acknowledged automatically. Reusing an entry UUID for editorial corrections or
promotion from dev to stable does not reopen it. New noteworthy entries can
appear in dev builds; a formal release aggregates them without re-announcing
previously seen entries. Receipts are persisted per authenticated admin on the server. The feed is enabled
on every image, including `:dev`, candidates, and stable. Runtime history retains
all applicable approved UUIDs; a formal release interval never clears history or
receipts. Every image build runs `scripts.prepare_product_updates_image` before
Docker packaging, including the exact merge-candidate images promoted to `:dev`.

## Step 1: Resolve the Requested Operation

For release-data-only requests, use the preparation path above. Resolve a release
rung only when publication is requested, using the conversation before asking.

> "Which release rung?
> - **dev push** — commits to main → CI builds `:dev` (every merge; you + community track bleeding edge)
> - **pre-release** — tag `vX.Y.Z-rc.N` → versioned images + a GitHub Release marked *pre-release* (newer than the last final, not yet blessed)
> - **full release** — tag `vX.Y.Z` → versioned images + `:latest` + a final GitHub Release"

The ladder is **dev → pre-release (`-rc.N`) → full release (`:latest`)**. SemVer orders pre-releases
below the final (`v0.9.3-rc.1 < v0.9.3-rc.2 < v0.9.3`), and CI's `create-release` job auto-detects a
pre-release from the `-` in the tag — so an `-rc` tag never gets `:latest` and never becomes the
`git describe` baseline that dev versions count from.

---

## Dev Push

For rapid iteration — commits on main, CI handles the build.

### 1. Check state

```bash
git status --short
git log --oneline origin/main..HEAD
```

Report: any uncommitted changes, how many commits ahead of origin.

### 2. Run local unit tests (sanity check)

```bash
./test.sh tests/unit/
```

**If tests fail:** show the failures and stop. Do not push until they pass.

### 2b. Validate approved product updates

Every ordinary build validates and bundles approved entries against the frozen
coverage target, with assets pinned to the build commit. This is deterministic
and uses no AI summary step:

```bash
TARGET=$(jq -r .target_ref product-updates/inventory.json)
CONTENT_REF=$(git rev-parse HEAD)
python3 scripts/product_updates.py validate \
  --content-dir product-updates \
  --inventory product-updates/inventory.json \
  --dispositions product-updates/dispositions.json \
  --target "$TARGET"
python3 -m scripts.prepare_product_updates_image --target "$CONTENT_REF"
```

Only approved entries enter the bundle. Draft previews use an explicit
`--allow-draft` command from the authoring guide and never run in a dev image
or release build.

### 3. Documentation freshness check

Run the helper script — it compares last-commit timestamps on `bifrost` vs `gobifrost` and lists any user-facing files changed since docs were last updated:

```bash
./scripts/release/check-docs-freshness.sh
```

Exit codes:

- `0` — docs are current (at or ahead of bifrost, OR no user-facing surface-area changes). Proceed.
- `1` — drift detected. Surface to the user with the script's output:

  > "Docs were last updated `<DOCS_LAST>`, bifrost main has moved since (`<N>` commits, `<M>` user-facing files touched — see list above). Want me to run the **bifrost-documentation** skill in `diff` mode before pushing? It'll re-capture screenshots for any pages whose source globs changed and open a docs PR."

  If yes, invoke the `bifrost-documentation` skill (`diff` mode) and wait for the docs PR before continuing. If no, note it and proceed.

- `2` — error (missing docs repo). The script prints the clone command. Run it and re-run the check.

The script's `USER_FACING_PATTERNS` array is the source of truth for what counts as user-facing — when adding new dirs that affect docs/screenshots (e.g., a new public router, a new MCP-tool family), update that list.

### 4. Summarize commits since last release

```bash
git describe --tags --abbrev=0 2>/dev/null || echo "no-prior-tag"
```

Then run:
```bash
LAST_TAG=$(git describe --tags --abbrev=0 2>/dev/null)
git log ${LAST_TAG}..HEAD --oneline 2>/dev/null || git log --oneline origin/main..HEAD
```

Present the summary as:

> **Commits since `<last-tag>`:**
>
> ⚠️ **Breaking changes:** *(list any commits whose message contains `BREAKING`, `breaking change`, or uses the `!:` conventional-commit marker — e.g., `feat!:`, `fix!:`. If none, omit this section.)*
>
> **All commits:**
> - `<sha>` `<message>`
> - ...

### 5. Push

```bash
git push origin main
```

### 6. Tell the user what happens next

> "Pushed. CI will now:
> 1. Run **unit tests** (fast ~2 min) — if they pass:
> 2. Build and push `ghcr.io/gobifrost/bifrost-api:dev` and `ghcr.io/gobifrost/bifrost-client:dev`
> 3. Also tag `ghcr.io/gobifrost/bifrost-api:<git-describe>` for traceability
>
> E2E tests run in parallel but don't block the build.
>
> K8s pods on `:dev` will pick up the new image on next restart/rollout. To force a rollout:
> ```bash
> kubectl rollout restart deployment/bifrost-api deployment/bifrost-worker deployment/bifrost-scheduler deployment/bifrost-client -n bifrost
> ```
>
> Watch CI: https://github.com/gobifrost/bifrost/actions"

---

## Pre-Release

For a release candidate — a versioned, signed build the community can pin and test, that is
explicitly **not** final. Same machinery as a full release EXCEPT the `-rc.N` suffix makes CI mark
the GitHub Release as a pre-release and skip the `:latest` tag.

### 1. Determine the version

Ask: "What pre-release tag? Format `vX.Y.Z-rc.N` — e.g. `v0.9.3-rc.1`. Bump `N` for each candidate
of the same target version (`-rc.1`, `-rc.2`, …)."

- The tag MUST start with `v` and MUST contain a `-` (the `-` is what flips CI to pre-release).
- `X.Y.Z` is the version you intend to finalize; `-rc.N` says "candidate N for that version."
- Do NOT reuse an `-rc` number. Do NOT cut `vX.Y.Z` with no suffix here — that's the full-release path.

### 2. Plugin manifest version guard

The tag-build CI job has a hard guard: every plugin manifest `version` must equal the tag's version
(WITH the `-rc.N` suffix). Run the bump locally first, land it via a PR, THEN tag:

```bash
TAG="vX.Y.Z-rc.N"; VERSION="${TAG#v}"
scripts/update-plugin-version.sh "$VERSION"
```

Same guard and trade-off as a full release — forgetting it fails the build, it doesn't ship stale.

### 3. Prepare Release Data

Prepare and review the candidate body before the tag. It uses the same approved
entries as a final release and adds candidate-specific test/known-issue material
only when that material is reviewed and committed:

```bash
TAG="vX.Y.Z-rc.N"
TARGET=$(jq -r .target_ref product-updates/inventory.json)
CONTENT_REF=$(git rev-parse HEAD)
./scripts/prepare-release-body.sh "$TAG" "$PREVIOUS_COMMIT" "$TARGET" "$CONTENT_REF" \
  "product-updates/release-bodies/${TAG}.md"
git add "product-updates/release-bodies/${TAG}.md"
git commit -m "docs(release): prepare ${TAG} notes"
./scripts/release-check.sh "$TAG" \
  --release-body "product-updates/release-bodies/${TAG}.md"
```

The generated body is deterministic and reviewed before the tag. Do not edit a
GitHub Release after CI creates it.

### 4. Tag and push

```bash
git tag vX.Y.Z-rc.N
git push origin vX.Y.Z-rc.N
```

### 5. What CI does

> "Pushed the pre-release tag. CI will:
> 1. Run the gate jobs on the tag ref.
> 2. Build + push versioned images:
>    - `ghcr.io/gobifrost/bifrost-api:X.Y.Z-rc.N` (and client)
>    - **NOT** `:latest` — that stays on the last full release.
> 3. Create a GitHub Release marked **pre-release** (CI detects the `-` in the tag).
>
> The `:dev` images are unaffected, and the dev baseline (`git describe`) does NOT move to an `-rc`
> tag — dev versions keep counting from the last FULL release.
>
> Watch CI: https://github.com/gobifrost/bifrost/actions"

The committed prepared body is published directly with the type stubs, source
archive, checksums, signatures, Docker instructions, and pre-release flag.
Do **not** draft a gobifrost.com blog post for a pre-release (that's a
full-release step).

---

## Full Release

For a named version — creates a GitHub Release, tags `:latest`, and sets the baseline for future `git describe` dev versions.

### 1. Determine version

Ask: "What version? (current git describe: run `git describe --tags --always`)"

The tag must start with `v` — e.g., `v2.1.0`.

### 1b. Documentation freshness check (REQUIRED for full release)

A versioned release should ship with current docs. Run:

```bash
./scripts/release/check-docs-freshness.sh
```

If the script exits `2` (missing docs repo), follow its instructions to clone before proceeding.

#### 1b-i. Identify net-new feature surface (REQUIRED)

`diff` mode only re-captures entries that **already exist** in the docs manifest. A versioned release frequently ships **brand-new feature surface** that has no MDX page or manifest entry yet — `diff` mode will miss it entirely.

Before invoking the docs skill, scan the commits since the last tag for **net-new feature surface**:

```bash
LAST_TAG=$(git describe --tags --abbrev=0 2>/dev/null)
git log ${LAST_TAG}..HEAD --pretty=format:'%h %s' | grep -iE '^[a-f0-9]+ feat[(!:]'
```

For each `feat:` / `feat!:` commit, ask: does the changed code introduce **new client routes, new admin pages, or new user-facing surfaces** that aren't already documented? Quick heuristic — `git show <sha> --stat | grep -E 'client/src/pages/|client/src/components/[a-z]+/[A-Z][A-Za-z]+\.tsx'`. If the diff adds a file there, the docs probably need a new MDX page.

If you find any, surface them to the user **before** invoking the docs skill:

> "I see `feat: external MCP client (#177)` since `<last-tag>`, which adds 5 new admin/user pages. The bifrost-documentation skill in `diff` mode won't author docs for new features — only refresh existing ones. Want to:
>
> **A.** Author the new docs now (3 MDX pages + manifest entries + capture). ~1-2 hours of focused work, requires brainstorming the page structure with you.
> **B.** Punt to a follow-up issue and ship `<tag>` with the new feature undocumented. Acceptable if the feature is infra-only or has clear in-app affordances.
> **C.** Hybrid — write a single bare-bones how-to that points users at the UI, defer deeper coverage to a follow-up.
>
> Either way, after that decision I'll run the docs skill in `diff` mode for screenshot drift on existing entries."

Proceed only after the user picks one of those paths.

#### 1b-ii. Run the docs skill in `diff` mode

For drift on **existing** entries, dispatch the docs skill regardless of the freshness-check exit code — screenshots can drift in subtle ways (theme tweaks, copy changes) that timestamp comparison won't catch:

> "Docs were last updated `<DOCS_LAST>`. Running **bifrost-documentation** in `diff` mode now to refresh anything that drifted on existing pages. New-feature docs are handled separately above."

Let it run. The docs PR is independent of the bifrost tag — you can tag in parallel after the docs PR is open.

### 2. Freeze, reconcile, and prepare the release body

```bash
# Do not use git describe here: an RC is not the previous final release.
PREVIOUS_FINAL=$(gh release list --limit 100 --json tagName,isDraft,isPrerelease,publishedAt \
  --jq '[.[] | select(.isDraft == false and .isPrerelease == false)] | sort_by(.publishedAt) | last | .tagName')
PREVIOUS_COMMIT=$(git rev-parse "${PREVIOUS_FINAL}^{commit}")
LEDGER_BASE=$(jq -r .base_ref product-updates/inventory.json)
TARGET=$(git rev-parse HEAD)
git merge-base --is-ancestor "$PREVIOUS_COMMIT" "$TARGET"
git log "${PREVIOUS_COMMIT}..${TARGET}" --oneline
printf 'Frozen final range: %s (%s) .. %s\n' "$PREVIOUS_FINAL" "$PREVIOUS_COMMIT" "$TARGET"
```

Present the summary as:

> **Commits since `<last-tag>`:**
>
> ⚠️ **Breaking changes:** *(list any commits whose message contains `BREAKING`, `breaking change`, or uses the `!:` conventional-commit marker — e.g., `feat!:`, `fix!:`. If none, omit this section.)*
>
> **All commits:**
> - `<sha>` `<message>`
> - ...

Before authoring, refresh the cumulative verified inventory from its retained
`LEDGER_BASE` through `TARGET`. Never discard earlier entry source metadata when
cutting a release; image history must retain those entries. The GitHub body alone
uses the explicit `PREVIOUS_COMMIT..TARGET` interval. The frozen inventory is the source of truth for landed coverage, verified
authors, source PRs, and contributor credits. The validator fails if any
landed PR lacks a canonical Highlight, Other, or reasoned Omit disposition.
Resolve security advisories, upgrade instructions, and breaking changes as
reviewed content before preparation; never infer a CVE or claim a warning is
irrelevant from a commit title.

Collect the inventory with the same exact base and target; this is the only
step that calls GitHub for release-source metadata:

```bash
python3 scripts/product_updates.py collect-inventory \
  --base "$LEDGER_BASE" --target "$TARGET" --repository gobifrost/bifrost \
  --output product-updates/inventory.json
```

Cross-check its PR count against landed history before review. Commit subjects
identify candidate PR numbers; the per-PR API response verifies authors and
metadata. Investigate every count mismatch and every direct commit instead of
dropping it from the disposition ledger:

```bash
git log "${LEDGER_BASE}..${TARGET}" --format='%s' \
  | grep -oE '\(#[0-9]+\)' | tr -d '()#' | sort -u > /tmp/release-pr-numbers.txt
while read -r pr; do
  gh api "repos/gobifrost/bifrost/pulls/${pr}" \
    --jq '[.number, .user.login, .html_url] | @tsv'
done < /tmp/release-pr-numbers.txt | tee /tmp/release-pr-metadata.tsv
test "$(wc -l < /tmp/release-pr-numbers.txt)" = "$(wc -l < /tmp/release-pr-metadata.tsv)"
```

```bash
TAG="vX.Y.Z"
TARGET=$(jq -r .target_ref product-updates/inventory.json)
CONTENT_REF=$(git rev-parse HEAD)
./scripts/prepare-release-body.sh "$TAG" "$PREVIOUS_COMMIT" "$TARGET" "$CONTENT_REF" \
  "product-updates/release-bodies/${TAG}.md"
git diff --check
# Review the exact Markdown, then commit it through the normal PR path.
git add "product-updates/release-bodies/${TAG}.md"
git commit -m "docs(release): prepare ${TAG} notes"
```

The generated body keeps rich reviewed entry Markdown and pinned screenshots,
credits each included external contributor inline and in the rollup, and
subtracts highlighted and omitted PRs from Other changes. Before approval,
verify that the reviewed source supplies explicit Security, Action Required,
Fixed CVE, and Breaking Change material where applicable; a renderer must not
invent those claims from commit messages. A release with no new highlights
still renders its reviewed Other changes.

### 2c. Bump the Claude and Codex plugin manifests (REQUIRED)

Claude Code and Codex plugin marketplaces key installed plugin content by manifest `version`. Without this step, users installed via the bifrost plugin can keep getting old skill content even after the Git changes are merged.

Run the helper, commit the bump to `main` via a normal PR, and merge it **before** tagging:

```bash
# <tag> is the version you're about to cut, e.g. v0.8.1 — strip the leading v.
VERSION="${TAG#v}"
./scripts/update-plugin-version.sh "$VERSION"
git add .claude-plugin/plugin.json .codex-plugin/plugin.json plugins/bifrost/.codex-plugin/plugin.json
git commit -m "chore(release): bump plugin manifests to $VERSION"
# PR + merge via the normal flow, then continue.
```

The tag-build CI job (`build-api`) has a hard guard that fails the release if any plugin manifest version doesn't match the tag — so forgetting this step blocks the build, it doesn't silently ship stale skills.

**Trade-off:** between releases the manifest reflects the last tagged version, not the current dev commit. Per-push commit-back was considered and rejected — main has branch protection with required PR review and no ruleset bypass, so CI cannot push directly, and an auto-PR loop would churn the merge queue on every commit. See issue #245 and PR #246 for the full rationale.

### 3. Run pre-tag checks

```bash
./scripts/release-check.sh <tag> \
  --release-body product-updates/release-bodies/<tag>.md
```

This verifies:
- Working tree is clean
- Tag doesn't exist locally or on remote
- You're on `main`
- Unit tests pass
- The tracked release body exists and matches deterministic approved content

**If it fails:** show the failures and stop. Do not proceed.

### 4. Tag and push

```bash
git tag <tag>
git push origin <tag>
```

### 5. Tell the user what happens next

> "Tag `<tag>` pushed. CI will now:
> 1. Run **unit tests + E2E tests** (both required for a release, ~12 min total)
> 2. Build and push images:
>    - `ghcr.io/gobifrost/bifrost-api:<version>` (e.g., `2.1.0`)
>    - `ghcr.io/gobifrost/bifrost-api:2.1` and `ghcr.io/gobifrost/bifrost-api:2`
>    - `ghcr.io/gobifrost/bifrost-api:latest`
>    - Same for `bifrost-client`
> 3. Create a GitHub Release at https://github.com/gobifrost/bifrost/releases
>
> After CI completes, K8s pods on `:latest` or `:<version>` will need a rollout:
> ```bash
> kubectl rollout restart deployment/bifrost-api deployment/bifrost-worker deployment/bifrost-scheduler deployment/bifrost-client -n bifrost
> ```
>
> CLI users on `:latest` will automatically get the new version next `pipx install`.
>
> Watch CI: https://github.com/gobifrost/bifrost/actions"

### 6. Offer to draft a blog post (gobifrost `/blog` skill)

Versioned releases get a companion announcement on https://gobifrost.com. The drafting logic lives in the **gobifrost repo's `/blog` skill** at `~/GitHub/gobifrost/.claude/skills/blog/SKILL.md` — that skill owns voice samples, frontmatter shape, slug conventions, and asset-path patterns.

Ask the user:

> "Want me to draft a companion blog post for `<tag>`? It'll be a themed narrative on a draft branch — you edit the prose and add screenshots before publishing."

**If they decline:** stop here. Release is done.

**If they accept:** gobifrost is a separate repo and isn't auto-loaded as a workspace in this bifrost session, so the Skill tool can't invoke it directly. Read the skill file and follow it inline:

```bash
cat ~/GitHub/gobifrost/.claude/skills/blog/SKILL.md
```

If the file is missing, the gobifrost checkout is stale or absent — `git clone git@github.com:gobifrost/website.git ~/GitHub/gobifrost` (or `git pull` if it exists) and re-read.

Pass to the skill as **inputs**:

- **Source material:** the release notes you drafted in step 4b (themed groupings + headline). Drop the CVE list, Docker pulls, and verification commands — those don't belong in a blog post.
- **Slug:** suggest something thematic, NOT version-numbered (the skill enforces this). If you can't think of one, ask the user.
- **pubDate:** today.

Follow the skill's workflow exactly — it handles preflight, voice-matching against existing posts, branch creation, scaffolding, anti-bloat self-review, and pushing the draft branch. **Do not open a PR.** The skill explicitly forbids it; the user iterates and ships manually.

---

## K8s Quick Reference

**Current image tags in use** (all in namespace `bifrost`):
- `api`, `init container`, `worker`, `scheduler` → `ghcr.io/gobifrost/bifrost-api:dev`
- `client` → `ghcr.io/gobifrost/bifrost-client:dev`

**Force rollout after a push:**
```bash
kubectl rollout restart deployment/bifrost-api deployment/bifrost-worker deployment/bifrost-scheduler deployment/bifrost-client -n bifrost
```

**Check what version is running:**
```bash
curl -s https://bifrostdev.musick.gg/api/version | python3 -m json.tool
```

**Pin to a specific release (e.g., v2.1.0):**
Update `kubernetes/components/bifrost/*/deployment.yaml` image tags from `:dev` to `:2.1.0`, commit, and apply.
