# Product Updates Implementation Progress

Worktree: `/home/jack/.codex/worktrees/ae47/bifrost`  
Branch: `codex/product-updates`  
Executor: this Codex chat, with disjoint Terra implementation tasks for tooling, client preview, and CI/release integration.

## Frozen Coverage

Latest published non-draft final release verified through `gh release list` on 2026-10-06: `v1.4.1`, published 2026-09-17T22:43:44Z.

- Previous final commit: `ade714a08b95c84daa285e859ebece55f45c6754`.
- Target: `32aeea16c99c27d1278b5576f09080e29a59cca2` (refreshed `origin/main`).
- Exact interval: 124 commits, 124 PR associations verified with GitHub's commit/pulls endpoint, 124 PR detail responses. No direct commits or missing associations.
- Verified PR authors: Jack 107, Dependabot 13, MTG-Thomas 2, wilhil 1, sdc53 1. PR #745 also carries Thomas's original #744 contribution; that attribution was checked against #744's author and commits.
- Candidate numbers in squash subjects are cross-checked against commit/pulls associations; publication timestamps do not decide inclusion.

## Source and Phase Status

Authoritative schema/tool: `product-updates/schema.json`, `scripts/product_updates.py`. Verified metadata: `product-updates/inventory.json`; durable classifications: `product-updates/dispositions.json`; draft backfill: `product-updates/drafts/initial-backfill/entries/`. Required release review uses the same schema's `releaseReview` definition and `scripts/release_gate.py`.

- Tooling: deterministic schema validation, cached-source coverage, Git ancestry eligibility, release interval filtering, schema-generated TypeScript/bundles, local assets, external credits, and PR/merge-group event validation are implemented. Metadata collection is an explicit preparation command; rendering has no GitHub or LLM dependency.
- Backfill: all 124 PRs reconciled: 68 grouped into 16 highlights, 39 Other changes with plain-English summaries, and 17 Omit decisions with reasons. No unresolved authors or PR associations. The preview adds one staged Product Updates/Discord announcement, with no invented PR number. Content remains draft for Jack's review.
- Client: development-only admin route, navigation and quiet shared unread cues; a replaceable local receipt adapter; state controls, safe screenshots, and persistent GitHub/Discord/Website brand links with a rainbow edge. Production build excludes the preview. No product API/database contract changed.
- CI/release: a real always-triggered Product Updates workflow uses the trusted base validator/schema. Initial bootstrap fails visibly until the validator exists on main; no green stub or candidate-code fallback. The live ruleset rollout is prepared, not applied. Release publication consumes a prepared body, and its gate refuses unreviewed coverage, security/CVE or breaking-change material.
- Preview stack: running at https://bifrost-3fc9b38f-lsk5.eu1.netbird.services/whats-new. Obtain login locally with `./debug.sh status`; credentials are not stored here.
- Screenshots: `product-updates-desktop-review.png`, `product-updates-mobile-review.png`, and `product-updates-image-review.png` in this directory. Desktop/mobile are the final passing browser flow; the embedded-image view was inspected separately.
- Draft GitHub outputs: saved under `product-updates/previews/` after the implementation commit so image URLs can pin the actual asset-bearing commit. They are previews, not approved release bodies.

## Approval and Production Gaps

Initial prose and security/upgrade material remain drafts. The reviewed design authorizes implementation, not a claim that this newly drafted prose has received Jack's approval. Dependency PR #910 includes advisory GHSA-6fqq-452j-qhrp; #718 includes a js-yaml CPU-use security fix. CVE applicability requires explicit release review, not an invented “none” statement. Retention defaults and removed/renamed CLI/MCP surfaces require prominent operator review.

Production seed/read-receipt API and database integration, publication delivery, tags, release publishing, deployment, and live ruleset changes are outside this slice. Jack supplied https://discord.gg/f7TCcWX2s for the branded footer. Actual invite expiry/use settings and server safety are not verified by this implementation. No external messages or GitHub settings have been changed.

## Verification

Focused verification passed:

```bash
python3 -m unittest scripts.test_product_updates scripts.test_release_gate -q
# 27 tests: 24 tooling contracts and 3 strict release-gate contracts.
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

Failures resolved during development: a staged announcement was incorrectly excluded by interval filtering (fixed with a regression test); a Markdown screenshot created invalid paragraph nesting (fixed and covered by its image-error component test); a duplicate Discord locator matched both announcement and footer links (scoped to the community navigation); static type checks exposed optional-value handling (fixed, now clean). One browser run hit `ERR_NETWORK_CHANGED` during a redundant full-page navigation while separate host-network Docker checks ran. The test now uses the product sidebar and Docker checks finish before the authenticated browser run. No retries, skips, or increased timeouts were added.

Not run: full backend unit/E2E, full Vitest/Playwright, `pre-pr`, registry/signing/attestation, and live GitHub merge queue. No API types were regenerated because no API contract changed.

## Resumption

1. Review this file, the handoff, authoring guide, and draft GitHub previews. Inspect `git status` and refresh main without moving the frozen initial backfill target.
2. Review the 16 highlights, Other/Omit decisions, external credits, security applicability/CVE findings, and upgrade warnings with Jack. Move approved content into canonical `entries/`, approve matching dispositions, and create the required release-review record only after review.
3. Attach actual PR/source metadata to the staged announcement after opening its PR. Do not invent its number or call the mock a production release.
4. When refreshing metadata for later release ranges, preserve verified secondary-contribution evidence and retained history; rehearse multiple final ranges before production wiring.
5. Verify real content-only, metadata-edit, mixed-source and multi-PR GitHub checks after bootstrap. Apply the reviewed ruleset delta only under separate authorization.
6. Implement the documented production seed/admin API/read-receipt contracts separately. Preserve UUID receipts, approved revisions, bundled rollback content/assets and running-build eligibility; do not add polling or a bespoke background job.
7. Verify invite Never/No Limit settings and Discord server safety in the separate setup chat. Publication delivery, tags and production deployment remain separately authorized work.
