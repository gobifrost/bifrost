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

For a **full release**, always offer to dispatch the `bifrost-documentation` skill (`diff` mode) before tagging — even if the script returns 0, screenshots can drift in subtle ways (theme tweaks, copy changes) that the timestamp comparison won't catch. Frame it:

> "Docs were last updated `<DOCS_LAST>`. Before I cut `<tag>`, want me to run **bifrost-documentation** in `diff` mode to refresh anything that drifted? This opens a separate docs PR; we can tag bifrost in parallel."

If the user opts in, invoke that skill and let it run (the docs PR is independent of the bifrost tag). If they decline, note it explicitly and continue.

If the script exits `2` (missing docs repo), follow its instructions to clone before proceeding.

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

### 2b. Credit external contributors

Find PRs merged since the last release that were authored by someone other than the repo owner — they must be credited in the release notes.

```bash
LAST_TAG_DATE=$(git log -1 --format=%cI $(git describe --tags --abbrev=0))
gh pr list --state merged --base main --search "merged:>=${LAST_TAG_DATE}" \
    --limit 200 --json number,title,author,mergedAt \
    | jq -r '.[] | select(.author.login != "jackmusick") | "#\(.number) @\(.author.login) — \(.title)"'
```

Include a **Contributors** section in the release notes listing each PR with author attribution (e.g., `- #22 by @sdc53 — embed token sessionStorage fix`). If a PR's change maps to a bullet you've already written elsewhere in the notes, append the `(#NN by @user)` credit to that bullet too.

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
3. **Contributors** — REQUIRED. Credit every external contributor whose PR landed in this release. Use the list you generated in step 2b. Format: `- #NN by @login — short description`. If a contributor's PR maps to a bullet in another section, also append `(#NN by @login)` to that bullet. If there were zero external contributions, write "None in this release — solo-maintained cycle."
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
