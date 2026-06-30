---
name: bifrost-documentation
description: Refresh the gobifrost site by re-capturing screenshots and (optionally) authoring missing pages. Trigger phrases - "refresh docs", "update screenshots", "/bifrost-documentation", "rebuild docs site". Has three modes - bootstrap (one-shot manifest generation, mandatory first run), diff (default - only refresh entries whose Bifrost source changed), full (re-capture and re-author everything).
---

# Bifrost Documentation Pipeline

Refresh `gobifrost` programmatically: re-capture screenshots, author missing Diátaxis-shaped pages, open a docs PR with a TL;DR.

The docs repo is at `~/GitHub/gobifrost` (or clone it from `git@github.com:gobifrost/website.git` if missing). The bifrost repo's worktree is the source of truth for the running app.

## Default behavior — "catch up everything since the last documented commit"

**When invoked with no mode, do the whole job in one pass — both existing and net-new surface.** The user's expectation: *"run it, figure it all out — all new features, all existing features. Know the last commit it fully documented, document all changes and features since, and update it with screenshots."* Don't ask which mode; just bring the docs current.

The watermark already lives in the manifest: each entry carries `captured_at.bifrost_sha`. The **lowest** sha across all entries is the "fully documented through" commit. Net-new surface is discovered by **router-walk ∖ manifest**.

Run this sequence (fan out the per-page work with `Workflow` — one agent per page — when there's more than a handful):

1. **Establish the watermark.** `WATERMARK=$(min captured_at.bifrost_sha across screenshots.yaml entries)`. Everything in `git log $WATERMARK..HEAD` (bifrost) is in scope.
2. **Discover undocumented routes.** Enumerate client routes from `client/src/App.tsx` (`grep -oE 'path="[^"]+"'`), normalize, and subtract every `route:` already in `screenshots.yaml`. The remainder is net-new surface needing **MDX + a manifest entry + fixtures**. (Skip non-visual routes: `*/callback*`, `device`, `mfa-setup`, `auth/*`, param-only redirects.)
3. **Discover stale existing entries.** Entries whose `source_globs` changed in `$WATERMARK..HEAD`, or whose `captured_at.bifrost_sha` is behind HEAD — these need a re-capture (and a prose check if the feature changed, not just pixels).
4. **Author (fan out).** For each net-new route: pick the Diátaxis quadrant, write the MDX page, add the sidebar entry, and add a `screenshots.yaml` entry (`id`, `route`, `seed`, `diataxis.{page,type}`, per-entry `mocks`/fixtures). For each stale page: update prose if the feature changed. Apply the anti-bloat self-review. **MDX comments are `{/* */}`, never `<!-- -->`.**
5. **Capture in one loop.** `scripts/docs/run-pipeline.sh --docs-repo $DOCS --bifrost-repo $BIFROST --full` (or `--ids <new+stale ids>`). Runs headless in the playwright-runner container **on this host** — no external browser. Pixel-diff gates commits.
6. **Stamp the watermark forward.** Every captured entry's `captured_at.bifrost_sha` is set to the bifrost HEAD it was captured against. That advances the watermark so the next run starts exactly here.
7. **Build + lint + PR.** `npm run lint:manifest` and `npm run build` must pass; commit; open the docs PR with the TL;DR.

The named modes below are the surgical primitives this default orchestrates. Reach for a single mode only when you explicitly want just that slice (e.g. `lint` after hand-editing). The default path is what a release or a monthly catch-up should use.

## Modes

| Mode | When | What it does |
|------|------|--------------|
| `bootstrap` | First run, or after major doc reorganization. **Aborts if `screenshots.yaml` exists** unless `--force`. | Generates `screenshots.yaml` and `bootstrap-report.md` from MDX inventory + bifrost router walk. No captures. |
| `diff` (default) | Daily refresh. | Short-lists entries whose `source_globs` changed since `captured_at.bifrost_sha`. Captures and pixel-diffs. Commits only PNGs that actually changed. |
| `full` | Major UI shift, theme change, or post-bootstrap first run. | Bypasses the source-glob shortlist. Pixel diff still gates commits, so identical re-renders don't churn git. Also runs the authoring pass for any pages without screenshots. |
| `lint` | After hand-editing the manifest. | Validates `screenshots.yaml` against schema, MDX cross-references, file existence. No captures. |

## Workflow

1. **Preflight**
   - Locate docs repo. Try `~/GitHub/gobifrost` then `/tmp/gobifrost`. If missing, clone to `~/GitHub/`.
   - Verify clean tree (`git status --porcelain` empty). If dirty, ask the user to commit/stash before continuing.
   - Pull `main` (`git pull --ff-only origin main`).
   - **Compare last-update timestamps** as a sanity signal:
     ```bash
     BIFROST_LAST=$(cd ~/GitHub/bifrost && git log -1 --format=%cI origin/main)
     DOCS_LAST=$(cd ~/GitHub/gobifrost && git log -1 --format=%cI origin/main)
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
- **Net-new feature docs ARE in scope — see "Default behavior" at the top.** (This used to be a carve-out. It no longer is.) The default no-mode run discovers undocumented routes via router-walk ∖ manifest, authors MDX + manifest entries + fixtures for each (fanning out with `Workflow`), then captures in one loop and stamps the watermark forward. The individual `diff`/`bootstrap`/`full` modes remain the surgical primitives, but you do **not** need to hand-author net-new pages before running — the default path does it. The `Authoring new captures` procedure below is the per-page recipe each fan-out agent follows.

## Authoring new captures (manual)

When you've written a new MDX page and need a screenshot:

1. **Identify the route** in `client/src/App.tsx`. Confirm the route renders empty-state-free with mocked data.
2. **Find the API endpoints** the page calls. `grep -nE 'apiClient|useQuery' <component>` then look at the route names. For each, write a fixture under `gobifrost/fixtures/`.
3. **Add a manifest entry** to `screenshots.yaml`. **`mocks`, `actions`, `crop`, `callouts`, `fullPage`, `settle_ms` MUST be nested under a `capture:` key — NOT at the entry top level.** The capture spec reads `entry.capture.actions` / `effectiveMocks(entry)` from `capture`; top-level `mocks`/`actions` are silently ignored, which produces a premature screenshot of an empty/loading state that still "passes" (no action ran to fail). Top-level keys are only `id`, `image`, `route`, `auth_as`, `seed`, `external`, `diataxis`, `captured_at`. Always end actions with `wait_for: text="<exact on-page label that only appears once data renders>"` so capture can't fire on the empty state. Mock URLs use playwright glob — include BOTH `**/api/foo` and `**/api/foo?**`. **After capturing, open the PNG and confirm it shows real data, not an empty state** — a passing test does not prove the mock matched.
4. **Vite proxy collisions:** if the route shares a prefix with a `vite.config.ts` proxy rule (e.g. `/mcp-servers` collides with the `/mcp` rule because of prefix match in dev), use `nav_via: { from: "/", click: "<sidebar-link-text>" }` so the test stack reaches the page via in-app routing instead of hard navigation. For deeper paths after `nav_via`, use the `goto_spa: <path>` action to push the path via the SPA's history without re-triggering proxy rules.
5. **Capture only the new ids**: `scripts/docs/run-pipeline.sh --ids id1,id2 ...` so existing entries aren't re-run.
6. **`organization_id` matters**: a few panels (e.g. `AgentMCPConnectionsPanel`) only render for org-scoped entities. If your fixture has `organization_id: null`, the panel returns null and the capture's `wait_for` will time out. Make a separate fixture variant when needed.

## Invocation from `bifrost-release`

The release skill calls this one (in `diff` mode) before tagging or pushing when bifrost main has moved past the docs repo's last commit. Behavior is identical — there's no special "release" mode. The release flow waits for the docs PR to be opened, then continues with the bifrost tag/push in parallel.

**Important:** the release skill's step 1b-i identifies net-new feature surface separately and routes around this skill for that case (manual authoring + capture, see above) — `diff` mode is appropriate ONLY for refreshing existing entries.

## Capture gotchas (learned the hard way — read before authoring entries)

- **`mocks`/`actions`/`crop` MUST nest under `capture:`.** Top-level ones are silently ignored → premature empty-state screenshot that still "passes." Top-level keys are only: `id`, `image`, `route`, `auth_as`, `seed`, `external`, `diataxis`, `captured_at`.
- **Wait on always-present structure, not data labels.** End actions with `wait_for` on a card title / `h1` that renders regardless of data (e.g. `text=Maintenance Actions`), NOT a fixture-specific row value that may not paint. Add a `wait_ms` settle after.
- **Find the REAL mount endpoints before mocking.** Pages use a typed `apiClient.GET("/api/...")` — grep the *service* file, not just the component. Mock every endpoint hit on load (incl. `/api/organizations` for org-name badges, `/api/maintenance/preflight` via `useWorkflows`). A missing mock → error card → wait_for times out. Mock the MOST SPECIFIC path first (`/foo/bar` before `/foo`) so a broad glob doesn't shadow it.
- **Deep routes (`/x/:id`) need `nav_via` + `goto_spa`,** not a hard `goto` (which can 404 the SPA shell or hit a Vite proxy prefix). Pattern: `nav_via: {from: '/', click: '<Sidebar Label>'}` then `actions: [{goto_spa: '/x/<id>'}, {wait_for: ...}]`. See `mcp-external-server-detail`.
- **A page gated on `data && obj` needs the object-shaped fixture.** E.g. SolutionDetail reads `data.solution` from `GET /api/solutions/{id}/entities` — the entities fixture must contain `{solution:{...}, ...}`, and the bare `/{id}` mock needs its own solution-object fixture (not the entities one).
- **ALWAYS open the captured PNG and confirm it shows real, correct data** — a green test does not prove the mock matched, the right page loaded, or data rendered. This is non-negotiable; it has caught empty states and wrong-page captures every session. See [[feedback_drive_dont_just_test]].
- **Capture before build.** New MDX `![](…png)` refs break `npm run build` until the PNG exists — run the pipeline first, then build.
- **The pipeline auto-resets test state and auto-installs `scripts/docs` deps**, and **post-process now runs even if some entries fail** (partial captures land; failed ones are reported). Re-run only the failures with `--ids`.

## Hard rules

1. Never edit prose without running anti-bloat self-review.
2. Never bypass `npm run lint:manifest` before committing.
3. Never commit `.tmp-captures/` — it's gitignored.
4. Never run capture mode against a dirty docs tree — abort and ask.
5. The skill writes to a branch, never directly to `main`.
6. Never leave a `{/* SCREENSHOT */}` placeholder in shipped docs. Either capture it (most surfaces are mockable — even detail pages, via fixtures + `nav_via`/`goto_spa`) or fold the description into prose and delete the marker.
