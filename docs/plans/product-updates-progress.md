# Product Updates Implementation Progress

Worktree: `/home/jack/.codex/worktrees/ae47/bifrost`  
Branch: `codex/product-updates`  
Executor: this Codex chat, with disjoint Terra implementation tasks for tooling, client preview, and CI/release integration.

## Current Production Status

This section supersedes historical preview-only status below. Jack authorized finishing production delivery and merging on 2026-10-06. All image channels now use the immutable approved bundle and authenticated per-admin PostgreSQL receipts. The local-storage adapter, development gates and Preview Controls were removed. Fifteen visible entries from sixteen approved canonical notes are ready; the app/Discord announcement still needs its actual feature PR as verified source.

Production-image browser verification passed with the rebuilt `client/Dockerfile` image, using `BIFROST_PRODUCT_UPDATES_TEST_URL=http://bifrost-product-updates-production-proof bash scripts/test-product-updates-preview.sh`: one journey passed in 9.8 seconds. It checks the automatic modal, receipt POST, loaded feature screenshots, desktop/mobile scrolling and exit controls, Help navigation, and no repeated modal when the same admin signs into a fresh browser. Screenshots were refreshed from that production run.

Focused client verification: `./test.sh client unit src/components/layout/HelpMenu.test.tsx src/services/productUpdates.test.ts src/components/layout/ProductUpdateContent.test.tsx src/components/layout/ProductUpdatesDialog.test.tsx src/pages/ProductUpdates.test.tsx` passed 22 tests in five files; scoped ESLint, full `npm run lint` (zero errors; four existing warnings outside the changed files) and `npm run tsc` passed. Tests cover UUID identity versus revisions, new IDs, same-admin/other-admin tab messages, failed receipts and empty feeds.

Tooling verification: `python3 -m unittest scripts.test_product_updates scripts.test_release_gate -q` passed 37 tests; `bash scripts/test_prepare_release_body.sh`, `bash scripts/test_release_check.sh`, and `python3 scripts/check_skill_mirrors.py` passed. Dockerized Ruff and Pyright passed for the five changed tool/test modules. Coverage includes cumulative image history, explicit release subranges and preserving reviewed contribution credits during inventory refresh.

Durable failure dispositions: the first production browser run found Help's remaining DEV gate; removing it made the same journey pass. The API operation-catalog ID grammar rejected underscore names; valid IDs are now `productupdates.get` and `productupdates.receipts.create`. The real-bundle unit test exposed a missing test-runner bundle mount; the compose definition now supplies it. A browser assertion used an APIResponse matcher on a browser Response; it now asserts the numeric HTTP status without dumping response objects. Focused backend verification passed three real-bundle unit tests and three API/service E2E tests, including admin ownership, release-only/unknown ID rejection, revised UUID persistence and concurrent duplicate upserts. API quality and contract verification are recorded as they complete.

The trusted validator bootstrap PR #917 merged as `5d4d5224f008e7e041f01300d8577f744776daee` after its PR and merge-group checks passed. The feature PR follows it so its Product Updates check can run trusted code from main. No live ruleset changes, release tag, GitHub release publication or Discord messages have been made. Full suites were not run locally; the merge queue runs the broad gates.

Final backend commands: `./test.sh tests/unit/services/test_product_updates.py -v` (3 passed), `./test.sh tests/e2e/api/test_product_updates.py -v --durations=0` (3 passed), `./test.sh quality api` (Ruff passed; Pyright zero errors/warnings), `./test.sh tests/unit/test_contract_version.py -v` (2 passed), and `./test.sh tests/unit/services/test_operation_catalog.py tests/unit/services/test_operation_inventory.py -v` (14 passed, including generated-document freshness).

The cumulative inventory was refreshed through bootstrap merge `5d4d5224f`: 125 verified PRs/commits, no missing associations, 68 Highlight, 17 Other and 40 Omit. PR #917 is the added internal-tooling omission. Reviewed extra attribution for PR #745 survived refresh. Saved previews and the runtime bundle were regenerated. The release skill now explicitly requires a metadata activation follow-up after a feature lands, for dev delivery as well as formal releases.

Bootstrap post-merge CI failed the exact-candidate promotion lookup with zero listed merge-group runs. A subsequent read-only GitHub lookup found the successful exact-SHA run `37554995630`; the promotion job's token had no explicit Actions read permission. The feature adds that least-privilege permission. The next post-merge promotion run must verify this disposition; no timeout, retry or relaxed gate was added.

The first live Product Updates check exposed the Python 3.12 conditional `typing-extensions` dependency missing from the slim lock, which had been assembled for Python 3.14. The trusted-validator workflow now uses 3.14 consistently with image preparation, and the lock also retains the root-pinned, hashed typing-extensions block for older Python environments. Clean-container `pip install --require-hashes` passed with both `python:3.14-slim` and `python:3.12-slim`, verifying this fix; the check is rerun only on the corrected commit.

The first full feature-PR unit run passed 8,452 backend tests and all 3,619 client tests but found three integration omissions: two Product Updates routes absent from the authorization access list, the public ORM export count still at 540 after adding the receipt model, and a stale generated OpenAPI skill digest. The fixes retain admin-only route gates, update the explicit public count to 541, and regenerate/sync the appendix. `./test.sh tests/unit/test_access_list.py tests/unit/test_model_import_boundaries.py tests/unit/test_skill_appendix_fresh.py -v` passed 35 tests after regenerating the access-list projection. `./test.sh quality api` passed again. The operation generator produced no additional changes, and skill mirrors/diff checks passed before pushing the corrected commit. Direct GitHub job logs identify the failures; the aggregate `gh --log-failed` output returned a different successful client step and was not used to diagnose them.

Final review removed CodeQL's redundant compact-title conditional and unused preview-only missing-image switches; actual image-error handling remains covered. The shared content/dialog/history checks passed 18 tests, TypeScript and scoped ESLint passed. Jack then replaced the Discord invite with `https://discord.gg/x84pft2YDa`; Help, the community footer, approved announcement, saved preview and browser assertions now use it. Help/content link checks passed six tests and source validation passed. The announcement keeps its UUID and advances to revision 5, preserving receipts.

The rebuilt production-image browser journey passed again with the replacement Discord invite (10.1 seconds overall). The feature PR's full checks passed on `6dba827f01dbc38e2896620c2e690a7fcbed27ed`, but its first merge-group Product Updates check exposed an ancestry assumption that fails for a squash queue. The PR was dequeued and auto-merge disabled while correcting the trusted base helper in PR #919. The replacement resolves GitHub MergeQueueEntry head/base commit metadata and rejects missing, ambiguous or cyclic chains. Eight focused Node tests passed, including the actual failed candidate, and the workflow adapter passed syntax and two-page pagination checks. No failed validation gate was bypassed.

## Historical Implementation Record

## Frozen Coverage

Latest published non-draft final release verified through `gh release list` on 2026-10-06: `v1.4.1`, published 2026-09-17T22:43:44Z.

- Previous final commit: `ade714a08b95c84daa285e859ebece55f45c6754`.
- Target: `32aeea16c99c27d1278b5576f09080e29a59cca2` (refreshed `origin/main`).
- Exact interval: 124 commits, 124 PR associations verified with GitHub's commit/pulls endpoint, 124 PR detail responses. No direct commits or missing associations.
- Verified PR authors: Jack 107, Dependabot 13, MTG-Thomas 2, wilhil 1, sdc53 1. PR #745 also carries Thomas's original #744 contribution; that attribution was checked against #744's author and commits.
- Candidate numbers in squash subjects are cross-checked against commit/pulls associations; publication timestamps do not decide inclusion.

## Source and Phase Status

Authoritative schema/tool: `product-updates/schema.json`, `scripts/product_updates.py`. Verified metadata: `product-updates/inventory.json`; durable classifications: `product-updates/dispositions.json`; draft backfill: `product-updates/drafts/initial-backfill/entries/`. Required release review uses the same schema's `releaseReview` definition and `scripts/release_gate.py`.

- Tooling: deterministic schema validation, cached-source coverage, Git ancestry eligibility, release interval filtering, schema-generated TypeScript/bundles, local assets, external credits, and PR/merge-group event validation are implemented. Pending sources are structurally validated but withheld until cached metadata and ancestry verify every cited source and prerequisite. Reviewed landed portions remain Other changes; required security and upgrade notices block release until eligible. Metadata collection is an explicit preparation command; rendering has no GitHub or LLM dependency.
- Backfill: all 124 PRs reconciled: 68 grouped into 16 canonical notes, 17 smaller customer-facing fixes/hardening summaries, and 39 Omit decisions with reasons. The latest app curation uses nine feature cards, four fix bullets, two hardening bullets, and keeps the dependency security notice release-only. No unresolved authors or PR associations. The preview adds one staged Product Updates/Discord announcement, with no invented PR number. Content remains draft for Jack's review.
- Client: development-only automatic admin modal and history route reachable through the admin Help menu; automatic batch acknowledgment through a replaceable local receipt adapter; safe screenshots and shared GitHub/Discord/Website brand footer with a rainbow edge. The manual action, per-item unread badges, inbox tabs, and duplicate sidebar entry were removed after Jack reviewed the first design. Production build excludes the preview. No product API/database contract changed.
- CI/release: a real always-triggered Product Updates workflow uses the trusted base validator/schema. Initial bootstrap fails visibly until the validator exists on main; no green stub or candidate-code fallback. The live ruleset rollout is prepared, not applied. Release publication consumes a prepared body, and its gate refuses unreviewed coverage, security/CVE or breaking-change material.
- Preview stack: running at https://bifrost-3fc9b38f-lsk5.eu1.netbird.services/whats-new. Obtain login locally with `./debug.sh status`; credentials are not stored here.
- Screenshots: `product-updates-desktop-review.png`, `product-updates-mobile-review.png`, and `product-updates-image-review.png` in this directory. Desktop/mobile are the final passing browser flow; the embedded-image view was inspected separately.
- Draft GitHub outputs: saved under `product-updates/previews/` after the implementation commit so image URLs can pin the actual asset-bearing commit. They are previews, not approved release bodies.

## Approval and Production Gaps

Initial prose and security/upgrade material remain drafts. The reviewed design authorizes implementation, not a claim that this newly drafted prose has received Jack's approval. Dependency PR #910 includes advisory GHSA-6fqq-452j-qhrp; #718 includes a js-yaml CPU-use security fix. CVE applicability requires explicit release review, not an invented “none” statement. Retention defaults and removed/renamed CLI/MCP surfaces require prominent operator review.

Production seed/read-receipt API and database integration, publication delivery, tags, release publishing, deployment, and live ruleset changes are outside this slice. Jack supplied https://discord.gg/x84pft2YDa for the branded footer. Actual invite expiry/use settings and server safety are not verified by this implementation. No external messages or GitHub settings have been changed.

## Initial Baseline Verification

Focused verification passed before the UX revision below. Some initial navigation tests were subsequently replaced with Help/modal coverage:

```bash
python3 -m unittest scripts.test_product_updates scripts.test_release_gate -q
# 31 tests: 27 tooling contracts and 4 strict release-gate contracts.
bash scripts/test_release_check.sh
bash scripts/test_prepare_release_body.sh
# Includes valid reviewed fixture generation and actual draft-corpus blocking.
node --test scripts/test_product_updates_scope.cjs
# 3 merge-scope contracts.
python3 scripts/product_updates.py coverage --target 32aeea16c99c27d1278b5576f09080e29a59cca2 --draft-dir product-updates/drafts/initial-backfill --allow-draft
# All unresolved/unclassified/unrepresented lists empty.
python3 api/scripts/check_github_action_pins.py
python3 scripts/check_skill_mirrors.py
git diff --check

docker run --rm -v "$PWD:/repo:ro" -w /repo --entrypoint pyright bifrost-test-api-dev:latest scripts/product_updates.py scripts/release_gate.py
# 0 errors, 0 warnings.
docker run --rm -v "$PWD:/repo:ro" -w /repo --entrypoint ruff bifrost-test-api-dev:latest check --no-cache scripts/product_updates.py scripts/release_gate.py scripts/test_product_updates.py scripts/test_release_gate.py
# All checks passed.

docker exec -w /app bifrost-debug-3fc9b38f-client-1 npm test -- src/components/layout/Header.test.tsx src/components/layout/Sidebar.test.tsx src/components/layout/AccountMenuContent.test.tsx src/components/layout/ProductUpdatesMenuItem.test.tsx src/components/layout/ProductUpdatesSidebarLink.test.tsx src/lib/product-updates-preview.test.ts src/pages/ProductUpdatesPreview.test.tsx
# 7 files / 21 tests passed.
docker exec -w /app bifrost-debug-3fc9b38f-client-1 npx tsc -b --pretty false
# Passed.
./test.sh client preview
# 1 happy path passed: real admin login, sidebar navigation, announcement/footer,
# mark-read, shared cue clearing, desktop/mobile screenshots. No retries; trace off.
```

Targeted ESLint passed for all changed client files and the preview config/spec/runner. Production build command: `docker exec -w /app bifrost-debug-3fc9b38f-client-1 npx vite build --outDir /tmp/product-updates-production-build`. A subsequent recursive grep for `What.s New|Product Updates, Now in Bifrost|product-updates.bundle` returned no matches (`PRODUCT_UPDATES_PREVIEW_ABSENT`). Full `docker exec -w /app bifrost-debug-3fc9b38f-client-1 npm run lint` passed with zero errors and four pre-existing hook warnings in unchanged AgentRunsPanel/ExecutionHistory files.

`./test.sh quality repo` passed early, including Action version/SHA verification and skill mirrors. The final run passed the scoped tool/release/helper checks, then GitHub's API returned HTTP 403 while resolving existing Action tags. The same full-SHA pins had passed earlier and have not changed; the final offline pin-format and mirror checks passed. This is an external rate-limit disposition, not a green rerun of that failed command. Live GitHub PR/metadata-edit/merge-group readiness must still be verified before applying the required-check ruleset.

Final draft render verification: all four saved bundles reproduce deterministically, with 16 highlights/39 Other changes in the full and candidate previews, 17/39 including the staged announcement, and 0/1 in the no-new-highlights range.

Failures resolved during development: a staged announcement was incorrectly excluded by interval filtering (fixed with a regression test); a Markdown screenshot created invalid paragraph nesting (fixed and covered by its image-error component test); a duplicate Discord locator matched both announcement and footer links (scoped to the community navigation); static type checks exposed optional-value handling (fixed, now clean). One browser run hit `ERR_NETWORK_CHANGED` during a redundant full-page navigation while separate host-network Docker checks ran. The test now uses the product sidebar and Docker checks finish before the authenticated browser run. No retries, skips, or increased timeouts were added.

Not run: full backend unit/E2E, full Vitest/Playwright, `pre-pr`, registry/signing/attestation, and live GitHub merge queue. No API types were regenerated because no API contract changed.

## UX Revision After Jack's Review

The manual receipt workflow has been removed: no mark-read button, per-item unread badge, inbox tabs, duplicate sidebar link, pagination or viewport tracker. Eligible unseen entries open automatically in a scrollable admin modal; rendering acknowledges the displayed batch. Receipt updates leave the batch on screen. Direct history visits render the feed without an overlay and acknowledge the presented batch. Receipt means presented, not proof of reading. The local adapter and UUID/independent-admin boundaries are retained.

Jack subsequently suggested a Help menu. Implemented an admin-only (?) trigger immediately left of the user icon, containing Documentation, Release Notes, Website, Discord and GitHub, plus the existing copyable version. Account retains its own actions; ordinary users keep their existing account version display. Resource links and version work in production; the internal Release Notes item and announcement/feed remain DEV-only pending the separate production slice.

Shared `ProductUpdateContent.tsx` owns Markdown/images, metadata, attribution and branded footer for both surfaces. History now uses a plain feed with consistent separators and spacing. The draft announcement is shortened to release notes in the app and Discord, with Help as the history path. The Claude handoff has been updated to current implementation instead of the earlier speculative carousel/viewport-tracking design.

Revision checks:

```bash
docker exec -w /app bifrost-debug-3fc9b38f-client-1 npm test -- src/components/layout/Header.test.tsx src/components/layout/Sidebar.test.tsx src/components/layout/AccountMenuContent.test.tsx src/components/layout/VersionMenuItem.test.tsx src/components/layout/HelpMenu.test.tsx src/components/layout/ProductUpdatesDialog.test.tsx src/components/layout/ProductUpdateContent.test.tsx src/lib/product-updates-preview.test.ts src/pages/ProductUpdatesPreview.test.tsx
# 9 files / 29 tests passed.
docker exec -w /app bifrost-debug-3fc9b38f-client-1 npx tsc -b --pretty false
# Passed, zero errors.
./test.sh client preview
# Real password-grant login, automatic modal and UUID acknowledgment, modal-to-history,
# Help navigation/version/resources, no manual receipt UI, desktop/mobile screenshots,
# direct history reload; includes a mobile viewport-bounds assertion. No retries.
docker exec -w /app bifrost-debug-3fc9b38f-client-1 npx vite build --outDir /tmp/product-updates-production-build --emptyOutDir
# Production build passed; ProductUpdatesDialog, receipt key and announcement prose absent.
```

Scoped ESLint passed for changed source/tests in the debug client, and separately for the actual worktree browser spec/runner in `bifrost-test-playwright:latest` with the whole client mounted. The debug stack mounts source, but not its e2e directory; checks on that image's e2e copy are not counted as current-spec evidence. Full client lint also passed with the same four existing warnings recorded above. No backend boundaries changed; broader suites remain unrun.

Failure dispositions: `./test.sh client unit <focused files>` could not find host Vitest because this worktree has no host-installed dependencies; the identical focused set passed using the API-matched debug client dependency container. New test selectors initially used Playwright's `exact` option in Testing Library; removed that unsupported option, preserving exact string-name matching. An interrupted type/lint process returned SIGTERM and was not counted as verification; type and scoped lint were separated for the final check.

The first public-proxy browser flow timed out during the sign-in transition. Request-timing diagnostics measured dev module requests taking 1–17 seconds and shell availability around 26 seconds. The harness now uses this worktree's Docker bridge and the same client/API instead of shipping hundreds of Vite imports through NetBird, preserving the public review URL. Final local happy path passed in 9.3 seconds, including mobile bounds and Help checks. A diagnostic waiting for the background Help button while a modal was open was an invalid readiness check (Radix hides the background); the test correctly waits for the modal itself.

Screenshot review caught mobile clipping when an open centered dialog was resized. The modal now uses a viewport top gutter with bounded height; header/actions/community links remain outside the content scroller. The browser spec asserts the whole dialog fits the mobile viewport, and screenshots disable finite entry animations for a stable final-layout capture.

Final revision screenshots: `product-updates-modal-desktop-review.png`, `product-updates-modal-mobile-review.png`, `product-updates-help-review.png`, `product-updates-help-mobile-review.png`, and refreshed desktop/mobile history images. All were inspected after the viewport fix.

## Resumption

1. Review this file, the handoff, authoring guide, and draft GitHub previews. Inspect `git status` and refresh main without moving the frozen initial backfill target.
2. Review the 16 highlights, Other/Omit decisions, external credits, security applicability/CVE findings, and upgrade warnings with Jack. Move approved content into canonical `entries/`, approve matching dispositions, and create the required release-review record only after review.
3. Attach actual PR/source metadata to the staged announcement after opening its PR. Do not invent its number or call the mock a production release.
4. When refreshing metadata for later release ranges, preserve verified secondary-contribution evidence and retained history; rehearse multiple final ranges before production wiring.
5. Verify real content-only, metadata-edit, mixed-source and multi-PR GitHub checks after bootstrap. Apply the reviewed ruleset delta only under separate authorization.
6. Implement the documented production seed/admin API/read-receipt contracts separately. Preserve UUID receipts, approved revisions, bundled rollback content/assets and running-build eligibility; do not add polling or a bespoke background job.
7. Verify invite Never/No Limit settings and Discord server safety in the separate setup chat. Publication delivery, tags and production deployment remain separately authorized work.

## Short Notes and Release Preparation

All 17 draft notes were rewritten with short titles, one or two sentences, verified app links and screenshots where available. UUIDs, evidence, eligibility and security/upgrade requirements remain; editorial revisions incremented to 2. The three affected saved release previews and app bundle were regenerated. Ordinary prose is capped at 60 words, security/action-required prose at 100, titles at 10. General Writing/Humanizer is the authoring pass; human review still checks clarity and accuracy. Source Details collapses attribution by default, while note links and View All Updates open a new tab. GitHub renders installation-relative links as named in-Bifrost directions.

The release skill now explicitly covers preparing data independently from publishing: author once during the PR, review and aggregate approved entries since the previous final, freeze the body, then consume it at publication. The production trigger proposal checks unseen approved UUIDs in the running build at admin shell entry; editorial changes and dev-to-stable promotion retain receipts. Production persistence remains outside this DEV preview.

Focused verification for this revision:

```bash
python3 -m unittest scripts.test_product_updates scripts.test_release_gate -q
# 33 tests passed.
bash scripts/test_prepare_release_body.sh
bash scripts/test_release_check.sh
# Both passed.
docker run --rm -v "$PWD:/app" -w /app bifrost-test-api-dev:latest sh -c 'ruff check --no-cache scripts/product_updates.py scripts/test_product_updates.py && pyright scripts/product_updates.py scripts/test_product_updates.py'
# Ruff passed; Pyright 0 errors.
docker exec -w /app bifrost-debug-3fc9b38f-client-1 npm test -- src/components/layout/ProductUpdateContent.test.tsx src/components/layout/ProductUpdatesDialog.test.tsx src/pages/ProductUpdatesPreview.test.tsx
# 3 files / 12 tests passed.
docker exec -w /app bifrost-debug-3fc9b38f-client-1 npx tsc -b --pretty false
# Passed.
docker exec -w /app bifrost-debug-3fc9b38f-client-1 npx eslint src/components/layout/ProductUpdateContent.tsx src/components/layout/ProductUpdateContent.test.tsx src/components/layout/ProductUpdatesDialog.tsx src/pages/ProductUpdatesPreview.test.tsx
docker run --rm -v "$PWD/client:/app" -v /app/node_modules -w /app bifrost-test-playwright:latest npx eslint e2e/preview/product-updates.admin.spec.ts
# Scoped source/tests and actual worktree browser spec lint passed.
./test.sh client preview
# Passed in 9.9 seconds: real login, automatic acknowledgment, new-tab history,
# Help, reload, desktop/mobile captures and modal viewport bounds.
docker exec -w /app bifrost-debug-3fc9b38f-client-1 npx vite build --outDir /tmp/product-updates-production-build --emptyOutDir
# Passed; grep confirmed preview announcement, dialog and receipt key absent.
```

Failure dispositions: a content edit briefly omitted a closing JSX block; the compiler caught it and it was restored before final tests. After changing View All Updates to open a new tab, one browser selector still targeted the original page; it now targets the history tab. That tab also gets an explicit mobile viewport because new tabs inherit the context viewport, not the opener's per-page override. Final screenshots reflect that correction. Ruff's first container run could not write the host cache as the container user; using `--no-cache` avoids that filesystem boundary without changing rule enforcement. No retries, skips or timeout increases were introduced. Full backend, full frontend suites and live publication were not run.

## Date Grouping

Modal and history now use shared UpdateGroups rendering: one date heading per local calendar day, newest first, with updates grouped beneath it. Individual entries no longer repeat their date. The existing bounded content scrollers and fixed modal header/actions/footer remain. Preview Controls remains DEV-only and simulates feed states, another admin's receipts and dev/stable bundles.

Verification: focused component tests passed (3 files / 13 tests) using `docker exec -w /app bifrost-debug-3fc9b38f-client-1 npm test -- src/components/layout/ProductUpdateContent.test.tsx src/components/layout/ProductUpdatesDialog.test.tsx src/pages/ProductUpdatesPreview.test.tsx`; shared grouping coverage exercises multiple dates and same-day membership. `./test.sh client preview` passed in 9.9 seconds, asserting one date heading in modal and history plus the existing mobile bounds and receipt/new-tab flow. Screenshots refreshed and desktop/mobile modal inspected. `docker exec -w /app bifrost-debug-3fc9b38f-client-1 npx tsc -b --pretty false` passed. Scoped ESLint passed for ProductUpdateContent, its test, ProductUpdatesDialog and ProductUpdatesPreview in the debug client, and the actual browser spec through the whole-client Playwright image mount. Full suites were not run; no backend or API contracts changed.

The first browser date-count assertion also counted Radix's dialog title, which is an h2. It now counts the exact date heading, preserving the single-date contract without assuming the dialog title's heading level. No retries or timeout changes were added.

## Clean Review URL

Preview Controls is omitted from normal `/whats-new` rendering; development fixture controls require `?controls=1`. The opt-in URL retains error-state recovery, admin and channel selection. Notes and draft status remain visible without a controls panel. History/Help desktop/mobile screenshots were refreshed and the mobile history inspected.

Focused checks: `docker exec -w /app bifrost-debug-3fc9b38f-client-1 npm test -- src/pages/ProductUpdatesPreview.test.tsx` passed all 5 tests, including default controls absence and opt-in fixture recovery. `./test.sh client preview` passed in 10.8 seconds, asserting controls absence on the normal history path. `docker exec -w /app bifrost-debug-3fc9b38f-client-1 npx eslint src/pages/ProductUpdatesPreview.tsx src/pages/ProductUpdatesPreview.test.tsx` passed, as did the actual browser spec ESLint via the mounted whole-client Playwright image. Broader suites and backend checks were not run for this UI-only change.

`docker exec -w /app bifrost-debug-3fc9b38f-client-1 npx tsc -b --pretty false` also passed.

## Customer-Facing Curation and Mobile Exit

Jack rejected maintenance inventories and security implementation detail as feature highlights. The app now uses New Features and Functionality with individual feature headlines, followed by separate Bug Fixes and Hardening bullet lists. Nine landed feature notes plus the staged announcement remain feature cards; four repairs and two hardening notes become compact bullets. Thirteen smaller fixes and four smaller hardening items join their respective lists in history. Twenty-two previously Other maintenance/dependency changes are now Omit with preserved original summaries and reasons. All 124 PRs remain accounted for (68 source PRs on canonical entries, 17 Other, 39 Omit). Coverage-reconciliation evidence was updated.

The model note focuses on testing a real connection instead of the models.dev catalog vendor. Credential hardening is phrased as access controls, without implying an incident. Upgrade steps, including Fetch before Commit/Sync/Discard and client/SDK requirements, remain inline. Compact fix/hardening sources are collected under one Source Details per list. Required dependency review material uses `in_app: false` and remains in the shared release bundle/body and security gate; app display and acknowledgment exclude it. Source and bundle schemas/types support this explicit editorial decision; Other category metadata round-trips deterministically. All draft revisions are now 3. Saved previews were regenerated, including the now-empty no-new-highlights range after routine maintenance was omitted.

History has a Done link in its fixed header back to the app. The modal has a Done button tested to close it. Its actions and social links share one compact footer; mobile socials use icons with accessible labels/titles and retain 40px targets. Footer height is reduced and the page scroller uses available constrained space instead of reserving the old control-panel height. Desktop/mobile history, modal and fix/hardening screenshots were captured and inspected.

Focused verification:

```bash
python3 -m unittest scripts.test_product_updates scripts.test_release_gate -q
# 34 passed; includes release-only notice retention and Other category propagation.
python3 scripts/product_updates.py coverage --target 32aeea16c99c27d1278b5576f09080e29a59cca2 --draft-dir product-updates/drafts/initial-backfill --allow-draft
# Unclassified/uncovered/unrepresented all empty.
bash scripts/test_prepare_release_body.sh
bash scripts/test_release_check.sh
# Passed.
docker exec -w /app bifrost-debug-3fc9b38f-client-1 npm test -- src/components/layout/ProductUpdateContent.test.tsx src/components/layout/ProductUpdatesDialog.test.tsx src/pages/ProductUpdatesPreview.test.tsx src/lib/product-updates-preview.test.ts
# 4 files / 16 tests passed.
docker exec -w /app bifrost-debug-3fc9b38f-client-1 npx tsc -b --pretty false
# Passed after the final component edit.
docker exec -w /app bifrost-debug-3fc9b38f-client-1 npx eslint src/components/layout/ProductUpdateContent.tsx src/components/layout/ProductUpdateContent.test.tsx src/components/layout/ProductUpdatesDialog.tsx src/components/layout/ProductUpdatesDialog.test.tsx src/pages/ProductUpdatesPreview.tsx src/pages/ProductUpdatesPreview.test.tsx src/lib/product-updates-preview.ts src/lib/product-updates-preview.test.ts
# Passed; actual browser spec lint also passed through the whole-client Playwright mount.
docker run --rm -v "$PWD:/app" -w /app bifrost-test-api-dev:latest sh -c 'ruff format --no-cache scripts/product_updates.py scripts/test_product_updates.py && ruff check --no-cache scripts/product_updates.py scripts/test_product_updates.py && pyright scripts/product_updates.py scripts/test_product_updates.py'
# Passed, Pyright zero errors.
./test.sh client preview
# Desktop/mobile automatic modal, no receipt for release-only notice, single fix/hardening
# lists, Done visible while scrolling, mobile captures and full-page Done navigation.
docker exec -w /app bifrost-debug-3fc9b38f-client-1 npx vite build --outDir /tmp/product-updates-production-build --emptyOutDir
# Passed; preview dialog/receipt key/announcement absent from production build.
```

Failure dispositions: an automated edit accidentally shadowed the imported visibility helper with a nonexistent prop, keeping the modal quiet. Component tests, types and lint exposed it; removing the shadow restored the imported helper and all checks passed. The initial Ruff format invocation hit the known host-cache permission boundary; `--no-cache` preserves formatting/lint enforcement without using that cache. Screenshot review then reduced correction card prose to compact sentences; final verification used the changed content. Broad suites and production/live publication were not run.

Final browser capture after restoring the explicit Fetch-before-sync upgrade instruction passed in 8.0 seconds. The final generated previews contain 16 canonical notes/17 smaller changes for full and candidate, 17/17 with the staged announcement, and 0/0 for the maintenance-only interval.

## Completeness Recheck and Feature Screenshots

Rechecked GitHub releases with `gh release list --limit 20 --json tagName,isDraft,isPrerelease,publishedAt`: the latest final remains v1.4.1 (2026-09-17). `git fetch origin main` completed; origin/main and FETCH_HEAD both equal the frozen target 32aeea16c99c27d1278b5576f09080e29a59cca2. `git rev-list --count v1.4.1..origin/main` is 124; the ledger covers every source in current main, with no later main commits missing from this initial batch.

Captured the actual seeded admin Effective Access page and the Run history retention card through a read-only authenticated Docker-browser session. The access note replaces its generic Roles illustration with the real effective-access screen. The retention note gains its first screenshot. Those two note revisions are 4; other notes remain 3. Source captures, route/context and SHA256 evidence are in `product-updates/evidence/feature-screenshot-captures.json`. New source assets were committed in 2ac8231e7 before regenerating GitHub bodies; all saved GitHub preview asset URLs now pin that asset-bearing commit. They require pushing that commit before public URLs resolve; no publishing occurred. The superseded Roles screenshot and generated copy were removed. The deterministic asset map and copied local assets were regenerated.

Capture dispositions: the first retention crop selected its rounded header rather than the whole Card; capture now uses the Card data-slot ancestor and the final PNG includes the settings. An XPath quoting error was corrected before recapture. Kubernetes settings are deliberately gated by KubernetesStatus.configured; this Docker preview has no configured Kubernetes, so its capture is not included. The final capture completed with both actual feature images; no empty or fabricated Kubernetes screenshot was substituted.

Verification: deterministic preview rendering/validation and coverage passed; `python3 -m unittest scripts.test_product_updates scripts.test_release_gate -q` passed 34 tests. `./test.sh client preview` passed in 7.5 seconds with naturalWidth assertions for both images, plus existing mobile exit, grouping, receipts and desktop/mobile captures. Actual browser spec lint passed in the whole-client Playwright image. No source UI logic or API contracts changed; broader suites were not run.

Final capture scrolls the retention image into view and passed in 8.8 seconds. `docker exec -w /app bifrost-debug-3fc9b38f-client-1 npx tsc -b --pretty false` passed with regenerated image imports. Scoped ESLint covers the actual browser spec and generated asset map.

## Action Required Curation

Removed Action Required from five draft notes: retention, credential handling, data/execution access, sync guidance, and the release-only dependency review. Settings choices and internal review tasks are not customer migration requirements. Retention still explicitly states the 30-day run/event default on existing installs, Keep forever, and the lack of an archive for deleted run details. Effective Access was already unflagged.

Only the CLI/SDK compatibility notice retains the label, scoped to CLI/SDK users. Evidence: `api/shared/version.py` requires 1.4.2 because SDKs through 1.4.1 require the removed execution-response field (and the old role contract). The authoring guide now requires an affected audience, concrete required step, and incompatibility evidence. Editorial changes increment revisions and regenerate the shared bundle and all four saved release previews. Draft approval status and source coverage remain unchanged.

Focused verification: `python3 -m unittest scripts.test_product_updates scripts.test_release_gate -q` (34 passed); `docker exec -w /app bifrost-debug-3fc9b38f-client-1 npm test -- src/components/layout/ProductUpdateContent.test.tsx` (5 passed); coverage command above (no gaps); scoped ESLint on the changed component test and preview spec (passed); `./test.sh client preview` (passed, 9.5 seconds). Browser coverage asserts exactly one Action Required notice and the scoped CLI/SDK audience; screenshots refreshed. No application logic/API contract changes; broader suites were not run.

## Remove Visible Dates and Merge Readiness

Removed visible date headings/groups from modal and history. Features now share one New Features and Functionality section, followed by Bug Fixes and Hardening. Publication dates remain source metadata and retain newest-first ordering. The component test covers ordering across multiple dates without date headings; the live preview asserts the initial date heading is absent.

Merge-readiness audit confirmed that runtime surfaces remain gated by `import.meta.env.DEV` and receipts are local-browser preview storage. This branch does not yet deliver the feature in production images or maintain admin-owned server receipts. Backfill prose remains draft. The release skill separates data preparation from publication and documents UUID-preserving announcements; canonical and Codex mirror match. No PR, merge, release or deployment was performed. Original handoff explicitly reserved database/API wiring for a follow-on production slice; the user has been told this limitation.

Focused verification: four changed-surface component/adapter suites (17 tests passed), scoped ESLint for content/component test and preview spec (passed), and `./test.sh client preview` (passed, 19.3 seconds). Screenshots refreshed without dates. `docker exec -w /app bifrost-debug-3fc9b38f-client-1 npx tsc -b --pretty false` passed. Broader suites were not run.
