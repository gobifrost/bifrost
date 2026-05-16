---
name: bifrost:release
description: Build and release Bifrost. Use when pushing commits to main, cutting a versioned release, or deploying to K8s. Handles dev push (CI builds :dev image) and full release (version tag → GitHub Release + :latest).
---

# Bifrost Release

## Step 1: Ask which workflow

> "Are you doing a **dev push** (push commits → CI builds `:dev`) or a **full release** (version tag → GitHub Release + `:latest`)?"

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

### 3. Documentation freshness check

Run the helper script — it compares last-commit timestamps on `bifrost` vs `bifrost-integrations-docs` and lists any user-facing files changed since docs were last updated:

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
> 2. Build and push `ghcr.io/jackmusick/bifrost-api:dev` and `ghcr.io/jackmusick/bifrost-client:dev`
> 3. Also tag `ghcr.io/jackmusick/bifrost-api:<git-describe>` for traceability
>
> E2E tests run in parallel but don't block the build.
>
> K8s pods on `:dev` will pick up the new image on next restart/rollout. To force a rollout:
> ```bash
> kubectl rollout restart deployment/bifrost-api deployment/bifrost-worker deployment/bifrost-scheduler deployment/bifrost-client -n bifrost
> ```
>
> Watch CI: https://github.com/jackmusick/bifrost/actions"

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

### 2. Summarize commits since last release

```bash
LAST_TAG=$(git describe --tags --abbrev=0 2>/dev/null)
git log ${LAST_TAG}..HEAD --oneline
```

Present the summary as:

> **Commits since `<last-tag>`:**
>
> ⚠️ **Breaking changes:** *(list any commits whose message contains `BREAKING`, `breaking change`, or uses the `!:` conventional-commit marker — e.g., `feat!:`, `fix!:`. If none, omit this section.)*
>
> **All commits:**
> - `<sha>` `<message>`
> - ...

Show this to the user and confirm they want to proceed with tagging.

### 2b. Credit external contributors (REQUIRED — gate before drafting notes)

Every PR landed in this release must be checked for authorship. Missing a contributor — especially on a headline feature — is the most common and most embarrassing failure mode of the release flow. **Prior failures:** v0.9.0 nearly shipped with @sdc53's external-MCP-client headline feature uncredited because the original `gh pr list --search merged:>=...` filter quietly missed PRs that were merged into the merge queue rather than directly to main. Don't trust a single query.

Use this **two-query cross-check** — get the canonical PR list from git history (which never lies), then query each PR's author directly:

```bash
LAST_TAG=$(git describe --tags --abbrev=0)

# Step 1: Extract every PR number from squash-merge subjects since LAST_TAG.
# git log is the source of truth — it captures every PR that actually landed,
# regardless of merge mechanism (direct merge, merge queue, rebase, squash).
git log ${LAST_TAG}..HEAD --format='%s' \
    | grep -oE '\(#[0-9]+\)' | grep -oE '[0-9]+' | sort -u > /tmp/pr-nums.txt
echo "PRs to check: $(wc -l < /tmp/pr-nums.txt)"

# Step 2: Fetch author for each PR (one gh call per PR, ~1-2s each).
# The output line goes into /tmp/pr-authors.txt: "<num>\t<author>\t<title>"
> /tmp/pr-authors.txt
while read num; do
    gh pr view "$num" --json number,title,author 2>/dev/null \
        | jq -r --arg n "$num" 'select(.author.login != null) | "\(.number)\t\(.author.login)\t\(.title)"' \
        >> /tmp/pr-authors.txt
done < /tmp/pr-nums.txt

# Step 3: Group by author. Anyone who is NOT jackmusick AND NOT a bot is an
# external contributor and MUST be credited.
awk -F'\t' '{print $2}' /tmp/pr-authors.txt | sort | uniq -c | sort -rn

# Step 4: Print just the external-contributor PRs (excludes you and bots).
grep -vE $'\t(jackmusick|app/dependabot|app/renovate|github-actions\\[bot\\])\t' /tmp/pr-authors.txt
```

**Sanity checks before proceeding** (do these every release, no exceptions):

1. **Counts match.** The PR-numbers count from step 1 should match the line count in `/tmp/pr-authors.txt`. If not, some PRs failed to fetch — re-run step 2 and investigate.
2. **The headline feature has an author.** Look at the `feat:` / `feat!:` commits since the last tag. For each, confirm its author appears in `/tmp/pr-authors.txt`. If a major feature was authored by someone other than `jackmusick`, that name must appear in your final Contributors section. Spot-checking the highest-impact PR every release is mandatory.
3. **Spec PRs count too.** A spec/design PR (`docs(spec):`) authored by a contributor is part of their contribution to the feature — credit it alongside the implementation PR.

**Then build the credits section:**

- Include a **Contributors** section in the release notes listing each external contributor's PRs with attribution. Lead with the most impactful contribution per person, not chronological.
- **Per-bullet credits are mandatory, not optional.** For every feature/fix/security bullet elsewhere in the notes that maps to an external contributor's PR, append `(#NN by @user)` directly to that bullet. The Contributors section is *additional* attribution, not a replacement — credit appears both where the change is described AND in the Contributors roll-up.
- For a feature whose spec was authored separately from the implementation, list both PRs on the same Contributors line (e.g., `**@sdc53** — designed and implemented the external MCP client (#176 spec, #177 implementation)`).
- Bots (dependabot, renovate, github-actions) are NOT credited as contributors — they're a different category. If their PRs land security or dep updates worth highlighting, those go under "Security & Supply-Chain Hardening" without an `@bot` credit.

**Anti-patterns to avoid:**

- ❌ Writing "None in this release — solo-maintained cycle" without running the two-query cross-check.
- ❌ Trusting a single `gh pr list --search` query. Squash-merge subjects in `git log` are the canonical record; everything else is a derived view that can have edge-case gaps (merge queue, force-pushes, deleted branches).
- ❌ Crediting only in the Contributors section without per-bullet `(#NN by @user)` markers. Readers scanning the feature list shouldn't have to scroll to find out who shipped it.
- ❌ Putting external contributors as a footnote. They led the work — lead with their name on the bullet that describes it.

### 2c. Bump the Claude plugin manifest (REQUIRED)

Claude Code's plugin marketplace only fetches plugin updates when `.claude-plugin/plugin.json`'s `version` field changes on the default branch. Without this step, users installed via the bifrost plugin will keep getting the old skill content.

Run the helper, commit the bump to `main` via a normal PR, and merge it **before** tagging:

```bash
# <tag> is the version you're about to cut, e.g. v0.8.1 — strip the leading v.
VERSION="${TAG#v}"
./scripts/update-plugin-version.sh "$VERSION"
git add .claude-plugin/plugin.json
git commit -m "chore(release): bump plugin manifest to $VERSION"
# PR + merge via the normal flow, then continue.
```

The tag-build CI job (`build-api`) has a hard guard that fails the release if the manifest version doesn't match the tag — so forgetting this step blocks the build, it doesn't silently ship stale skills.

**Trade-off:** between releases the manifest reflects the last tagged version, not the current dev commit. Per-push commit-back was considered and rejected — main has branch protection with required PR review and no ruleset bypass, so CI cannot push directly, and an auto-PR loop would churn the merge queue on every commit. See issue #245 and PR #246 for the full rationale.

### 3. Run pre-tag checks

```bash
./scripts/release-check.sh <tag>
```

This verifies:
- Working tree is clean
- Tag doesn't exist locally or on remote
- You're on `main`
- Unit tests pass

**If it fails:** show the failures and stop. Do not proceed.

### 4. Tag and push

```bash
git tag <tag>
git push origin <tag>
```

### 4b. Draft human-readable release notes (REQUIRED for OSPS Passing)

CI's `create-release` job in `.github/workflows/ci.yml` writes a templated body covering Docker pulls, type stubs, and Sigstore verification. That template is **not** sufficient for the OpenSSF Best Practices "Passing" criteria `release_notes` and `release_notes_vulns`, which require a human-readable change summary and an explicit list of CVEs fixed (or "None in this release"). After the tag is pushed, draft notes and overwrite CI's body via `gh release edit <tag> --notes-file <file>`.

**Always-required sections** (drop a section only if explicitly empty AND verifiably so):

1. **Headline** — 1–2 sentences naming what this release is about. Plus the commit count since the previous tag.
2. **Themed groupings** — pick from this set as appropriate; use human judgment on ordering and which apply:
   - Security & Supply-Chain Hardening
   - Bug Fixes
   - Reliability
   - Developer Experience
   - Features
   - Breaking Changes
3. **Contributors** — REQUIRED, and you MUST cross-check this against step 2b's two-query scan. If the scan found ANY non-jackmusick / non-bot author, this section is non-empty.
   - **Lead each contributor's line with their highest-impact PR**, not chronological order. Format: `**@login** — <verb-led description of what they shipped> (#NN <role>, #MM <role>)`. Roles: `spec` for design PRs, `implementation` for the matching feature PR. For a single-PR contribution: `**@login** — <description> (#NN)`.
   - **Per-bullet credits are mandatory in OTHER sections**, not just this one. For every feature/fix/security bullet whose underlying PR was authored externally, the bullet must end with `(#NN by @login)` (or `(#NN spec, #MM implementation by @login)` for paired PRs). The Contributors section is *additive* — readers scanning Features should see attribution inline, AND there should be a Contributors roll-up at the bottom.
   - Only write "None in this release — solo-maintained cycle" after step 2b's scan has been re-run AND its sanity checks (counts match, headline feature has an author) all pass with zero external authors.
4. **Fixed CVEs** — REQUIRED. Cross-reference commits via `git log <prev-tag>..HEAD --grep='CVE\|GHSA\|PYSEC\|vuln\|security'` and read the bodies of dep-bump PRs (`gh pr view <num> --json body`) for specific CVE/GHSA IDs. List each package bumped and the specific CVEs/GHSAs the bump closed. If nothing was fixed, write "None in this release". **Do NOT fabricate IDs** — if a bump didn't list a specific CVE, say "multiple Dependabot security advisories closed via dep bumps; see commit log for details" rather than inventing one.
5. **Breaking Changes** — REQUIRED. If none, write "None in this release". If anything moved, was renamed, or changed install/upgrade procedure, document the migration step.
6. **Docker Images / Type Stubs / Signed Artifacts** — keep the corresponding blocks from CI's template body (the verification commands matter for users).

**Format rules:**

- Markdown bullets, one line per item.
- Link PRs as `(#123)` — GitHub auto-links these.
- Group by theme, **NOT** chronologically. Raw `git log` output does not satisfy `release_notes`.
- Cap individual sections at ~10 bullets; collapse routine Dependabot bumps into a single line referencing the commit log.

**Drafting workflow:**

```bash
# 1. Get the commit list
git log <prev-tag>..HEAD --oneline

# 2. Get security-tagged commits for the Fixed CVEs section
git log <prev-tag>..HEAD --grep='CVE\|GHSA\|PYSEC\|vuln\|security' --oneline

# 3. For any dep-bump PR or security PR you need details on
gh pr view <num> --json title,body

# 4. (Reuse the contributor list from step 2b)

# 5. Write the notes to a file, then (after CI's create-release job finishes) overwrite the body
gh release edit <tag> --notes-file /tmp/release-notes-<tag>.md
```

### 5. Tell the user what happens next

> "Tag `<tag>` pushed. CI will now:
> 1. Run **unit tests + E2E tests** (both required for a release, ~12 min total)
> 2. Build and push images:
>    - `ghcr.io/jackmusick/bifrost-api:<version>` (e.g., `2.1.0`)
>    - `ghcr.io/jackmusick/bifrost-api:2.1` and `ghcr.io/jackmusick/bifrost-api:2`
>    - `ghcr.io/jackmusick/bifrost-api:latest`
>    - Same for `bifrost-client`
> 3. Create a GitHub Release at https://github.com/jackmusick/bifrost/releases
>
> After CI completes, K8s pods on `:latest` or `:<version>` will need a rollout:
> ```bash
> kubectl rollout restart deployment/bifrost-api deployment/bifrost-worker deployment/bifrost-scheduler deployment/bifrost-client -n bifrost
> ```
>
> CLI users on `:latest` will automatically get the new version next `pipx install`.
>
> Watch CI: https://github.com/jackmusick/bifrost/actions"

### 6. Offer to draft a blog post (gobifrost `/blog` skill)

Versioned releases get a companion announcement on https://gobifrost.com. The drafting logic lives in the **gobifrost repo's `/blog` skill** at `~/GitHub/gobifrost/.claude/skills/blog/SKILL.md` — that skill owns voice samples, frontmatter shape, slug conventions, and asset-path patterns.

Ask the user:

> "Want me to draft a companion blog post for `<tag>`? It'll be a themed narrative on a draft branch — you edit the prose and add screenshots before publishing."

**If they decline:** stop here. Release is done.

**If they accept:** gobifrost is a separate repo and isn't auto-loaded as a workspace in this bifrost session, so the Skill tool can't invoke it directly. Read the skill file and follow it inline:

```bash
cat ~/GitHub/gobifrost/.claude/skills/blog/SKILL.md
```

If the file is missing, the gobifrost checkout is stale or absent — `git clone git@github.com:jackmusick/gobifrost.git ~/GitHub/gobifrost` (or `git pull` if it exists) and re-read.

Pass to the skill as **inputs**:

- **Source material:** the release notes you drafted in step 4b (themed groupings + headline). Drop the CVE list, Docker pulls, and verification commands — those don't belong in a blog post.
- **Slug:** suggest something thematic, NOT version-numbered (the skill enforces this). If you can't think of one, ask the user.
- **pubDate:** today.

Follow the skill's workflow exactly — it handles preflight, voice-matching against existing posts, branch creation, scaffolding, anti-bloat self-review, and pushing the draft branch. **Do not open a PR.** The skill explicitly forbids it; the user iterates and ships manually.

---

## K8s Quick Reference

**Current image tags in use** (all in namespace `bifrost`):
- `api`, `init container`, `worker`, `scheduler` → `ghcr.io/jackmusick/bifrost-api:dev`
- `client` → `ghcr.io/jackmusick/bifrost-client:dev`

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
