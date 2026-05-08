---
name: bifrost-documentation
description: Refresh the bifrost-integrations-docs site by re-capturing screenshots and (optionally) authoring missing pages. Trigger phrases - "refresh docs", "update screenshots", "/bifrost-documentation", "rebuild docs site". Has three modes - bootstrap (one-shot manifest generation, mandatory first run), diff (default - only refresh entries whose Bifrost source changed), full (re-capture and re-author everything).
---

# Bifrost Documentation Pipeline

Refresh `bifrost-integrations-docs` programmatically: re-capture screenshots, author missing Diátaxis-shaped pages, open a docs PR with a TL;DR.

The docs repo is at `~/GitHub/bifrost-integrations-docs` (or clone it from `git@github.com:jackmusick/bifrost-integrations-docs.git` if missing). The bifrost repo's worktree is the source of truth for the running app.

## Modes

| Mode | When | What it does |
|------|------|--------------|
| `bootstrap` | First run, or after major doc reorganization. **Aborts if `screenshots.yaml` exists** unless `--force`. | Generates `screenshots.yaml` and `bootstrap-report.md` from MDX inventory + bifrost router walk. No captures. |
| `diff` (default) | Daily refresh. | Short-lists entries whose `source_globs` changed since `captured_at.bifrost_sha`. Captures and pixel-diffs. Commits only PNGs that actually changed. |
| `full` | Major UI shift, theme change, or post-bootstrap first run. | Bypasses the source-glob shortlist. Pixel diff still gates commits, so identical re-renders don't churn git. Also runs the authoring pass for any pages without screenshots. |
| `lint` | After hand-editing the manifest. | Validates `screenshots.yaml` against schema, MDX cross-references, file existence. No captures. |

## Workflow

1. **Preflight**
   - Locate docs repo. Try `~/GitHub/bifrost-integrations-docs` then `/tmp/bifrost-integrations-docs`. If missing, clone to `~/GitHub/`.
   - Verify clean tree (`git status --porcelain` empty). If dirty, ask the user to commit/stash before continuing.
   - Pull `main` (`git pull --ff-only origin main`).
   - **Compare last-update timestamps** as a sanity signal:
     ```bash
     BIFROST_LAST=$(cd ~/GitHub/bifrost && git log -1 --format=%cI origin/main)
     DOCS_LAST=$(cd ~/GitHub/bifrost-integrations-docs && git log -1 --format=%cI origin/main)
     echo "bifrost: $BIFROST_LAST"
     echo "docs:    $DOCS_LAST"
     ```
     Print both. If `BIFROST_LAST > DOCS_LAST`, that's normal — diff mode handles it. If `DOCS_LAST > BIFROST_LAST`, something is unusual (docs ahead of code); flag it but proceed.
   - Cut a fresh branch: `docs/screenshot-refresh-YYYY-MM-DD-<short-sha>`.
   - Verify bifrost test stack is up: `./test.sh stack status` in the bifrost worktree. If down, `./test.sh stack up`. The boot is ~2-5 minutes; warn the user up front.
   - Set `DOCS_REPO_PATH=<absolute path to docs repo>` for downstream tools.

2. **Mode dispatch**

   - **`bootstrap`**:
     ```bash
     node $BIFROST/scripts/docs/bootstrap-manifest.mjs \
         --docs-repo $DOCS_REPO --bifrost-repo $BIFROST $FORCE
     ```
     Then `cd $DOCS_REPO && npm run lint:manifest` to confirm schema validity. Commit `screenshots.yaml` + `bootstrap-report.md`. Open PR titled "Bootstrap docs screenshot manifest" with body = `bootstrap-report.md`. Stop here — do not continue to capture in the same run.

   - **`diff` / `full`**:
     ```bash
     $BIFROST/scripts/docs/run-pipeline.sh \
         --docs-repo $DOCS_REPO --bifrost-repo $BIFROST $MODE_FLAG
     ```
     Reads JSON output for the TL;DR.

   - **`lint`**: `cd $DOCS_REPO && npm run lint:manifest`. Print result. No PR.

3. **Authoring pass** (full mode only)
   For each MDX page in `bootstrap-report.md`'s "Pages without screenshots" list that the user wants documented, generate a Diátaxis-templated stub. Use `templates/` as starting points.
   - **Apply the anti-bloat self-review (below) before committing.**
   - The skill must NOT silently overwrite hand-written prose; only fills empty/stub pages.

4. **Anti-bloat self-review** (every prose change)
   After writing or editing any MDX, sweep for these patterns and cut them:
   - "In this section, we'll explore..."
   - "It's important to note that..."
   - "Let's dive into..."
   - "As we mentioned earlier..."
   - Any paragraph longer than 80 words in a tutorial or how-to (split, condense, or move to an explanation page).
   - Preamble before numbered steps (just start the steps).
   Reference + explanation pages can be denser. Tutorials and how-tos are terse by Diátaxis discipline.

5. **Finalize**
   - Run `cd $DOCS_REPO && npm run lint:manifest` to confirm.
   - Optionally `npm run build` for a smoke build.
   - Stage and commit. Title: `Refresh screenshots and docs (<N> changed, <M> authored)`.
   - Push branch.
   - Open PR via `gh pr create`. Body = TL;DR (template below).

## TL;DR template

```
## Mode
<bootstrap | diff | full>

## Bifrost SHA range
`<old>..<HEAD>` (or `<HEAD>` if bootstrap)

## Counts
- Candidates short-listed: <N>
- Captures attempted: <N>
- PNGs committed (passed pixel diff): <N>
- Entries unchanged (visual no-op): <N>
- Authored pages (full mode): <N>

## Manual review needed
<list any low-confidence flags from bootstrap-report.md or capture errors>

## Failures
<list any entries that errored, with route + reason>
```

## Diátaxis quadrant rules

When authoring or editing prose, pick the right quadrant and stay in it:

- **Tutorial** (`getting-started/`, `*first-*`): goal-oriented, a single happy path, no detours. Numbered steps, ≤2 sentences each.
- **How-to** (`how-to-guides/**`): one specific task. "How to <verb> <noun>" title. No teaching — assume the reader already understands the concept.
- **Reference** (`sdk-reference/**`): describe, don't explain. Tables of params, signatures, return values, one minimal example per item. Reference is the only quadrant where bloat is permitted, and only when needed for disambiguation.
- **Explanation** (`core-concepts/`, `about/`): why-shaped. Cross-link to tutorials/how-tos rather than repeating their content.

If the user asks for a doc and you can't pick a quadrant in one sentence, ask them.

## When NOT to use this skill

- The user wants to write a single targeted doc page from scratch — write it directly.
- The user is debugging or fixing a typo — direct edit.
- The bifrost test stack is broken — fix that first; this skill cannot work without it.
- **The user is shipping net-new feature docs.** None of this skill's modes author MDX from a feature spec — `diff` only refreshes existing entries, `bootstrap` walks the router but produces a draft manifest that overwrites the existing one (destructive — discards hand-curated mocks/seeds/actions), and `full` only authors stubs for MDX pages already referenced by the manifest. Brand-new how-to / explanation pages must be authored directly (read the PR description, brainstorm structure with the user, write MDX, add manifest entries with mocks/seeds, then run with `--ids <new-ids>` to capture). The bifrost-release skill flags this case in step 1b-i and prompts the user before invoking this skill.

## Authoring new captures (manual)

When you've written a new MDX page and need a screenshot:

1. **Identify the route** in `client/src/App.tsx`. Confirm the route renders empty-state-free with mocked data.
2. **Find the API endpoints** the page calls. `grep -nE 'apiClient|useQuery' <component>` then look at the route names. For each, write a fixture under `bifrost-integrations-docs/fixtures/`.
3. **Add a manifest entry** to `screenshots.yaml` with `route`, `mocks`, `actions` (`wait_for: text="<exact-label>"` is the most common). Mock URLs use playwright glob — `**/api/foo` and a separate `**/api/foo?**` to cover query strings.
4. **Vite proxy collisions:** if the route shares a prefix with a `vite.config.ts` proxy rule (e.g. `/mcp-servers` collides with the `/mcp` rule because of prefix match in dev), use `nav_via: { from: "/", click: "<sidebar-link-text>" }` so the test stack reaches the page via in-app routing instead of hard navigation. For deeper paths after `nav_via`, use the `goto_spa: <path>` action to push the path via the SPA's history without re-triggering proxy rules.
5. **Capture only the new ids**: `scripts/docs/run-pipeline.sh --ids id1,id2 ...` so existing entries aren't re-run.
6. **`organization_id` matters**: a few panels (e.g. `AgentMCPConnectionsPanel`) only render for org-scoped entities. If your fixture has `organization_id: null`, the panel returns null and the capture's `wait_for` will time out. Make a separate fixture variant when needed.

## Invocation from `bifrost-release`

The release skill calls this one (in `diff` mode) before tagging or pushing when bifrost main has moved past the docs repo's last commit. Behavior is identical — there's no special "release" mode. The release flow waits for the docs PR to be opened, then continues with the bifrost tag/push in parallel.

**Important:** the release skill's step 1b-i identifies net-new feature surface separately and routes around this skill for that case (manual authoring + capture, see above) — `diff` mode is appropriate ONLY for refreshing existing entries.

## Hard rules

1. Never edit prose without running anti-bloat self-review.
2. Never bypass `npm run lint:manifest` before committing.
3. Never commit `.tmp-captures/` — it's gitignored.
4. Never run capture mode against a dirty docs tree — abort and ask.
5. The skill writes to a branch, never directly to `main`.
