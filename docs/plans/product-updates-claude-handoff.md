# Product Updates: Claude UX Revision Handoff

## Assignment

Executor: Claude, selected by Jack; model/version is Jack's choice. This document is a handoff package, not a launched Claude session. Jack owns design/content approval. No background executor or monitoring is running.

Continue in `/home/jack/.codex/worktrees/ae47/bifrost`, branch `codex/product-updates`. Baseline: `b2c3a7a28a5e9ced0be375cd268bb4874b4be5fc`, preceded by implementation commit `72e730fec8d11ecb65b7220b1de7b82ebe7e98f5`. Worktree was clean when this handoff was prepared. Preserve these commits and inspect current status before editing. The existing Terra tasks are finished; coordinate before starting any overlapping writer.

Deliver a revised, interactive development-only experience with desktop/mobile screenshots. Use the existing deterministic content pipeline and frozen backfill. The user considers much of the current UX rough; passing tests and the old screenshots are evidence of mechanics, not design acceptance.

Read current AGENTS.md, DESIGN.md, the applicable testing/debug skills, `docs/plans/product-updates-progress.md`, and `docs/product-updates-authoring.md`. Original assignment: `/home/jack/workspace/bifrost-product-updates-handoff/HANDOFF.md`. The latest user feedback below supersedes the original manual-acknowledgment/no-automatic-modal direction. Production API/DB wiring and external publication remain outside this revision.

## Latest User Feedback

> Some of this design is good. A lot is pretty rough. Why are we forcing users to mark it as read? Even if we did why is there no padding on the button? Do we need an unread status on every item? How do we get to the whole page? Why not a modal pop up that automatically marks as read as it’s called so the user doesn’t have to do anything?

The manual action was implemented because the previous assignment explicitly requested it. That design is now being reconsidered. Remove the acknowledgment chore; the product should track whether an update has been presented without requiring the user to manage receipts. A receipt means “shown,” not proof the person read or understood it.

## Requested Direction and Proposed Interaction

The user requests an automatic modal alternative, simpler status presentation, clear access to the full page, and polished spacing. The detailed timing and layout below are recommendations from this chat, ready for Claude to implement and Jack to review; they are not separately approved pixel specifications.

### Modal and acknowledgment

- Use the shared Dialog primitive and existing product shell. Inspect `client/src/components/ui/dialog.tsx`, `VersionUpdateBanner.tsx`, `HeaderStatusIndicators.tsx`, and `useVersionCheck.ts` before implementation. Preserve the installed-version display and refresh behavior.
- When a platform admin enters the authenticated shell and eligible unseen highlights exist, automatically show the latest unseen highlight. Open after authentication and update loading succeed; retain the current underlying route. Avoid interrupting an already-open dialog or an in-progress interaction. A small shell coordinator should reuse existing dialog ownership conventions, rather than starting a second notification framework.
- Show one rich update at a time. With multiple unseen highlights, provide a restrained “1 of N” position and Next/Previous navigation. This avoids an automatic modal containing the entire 17-entry backfill. The first slide in the current preview should be “Product Updates, Now in Bifrost.”
- Mark only the current entry UUID as seen after its content successfully renders in the open dialog. Calling an open handler, fetching data, loading, or a failure state does not acknowledge content. Keep the entry on screen after its receipt changes; snapshot the modal queue at opening so receipt updates do not remove slides or close the modal.
- Closing via Close, Escape, or the backdrop keeps acknowledged entries acknowledged and leaves unpresented slides unseen. Do not repeatedly reopen the modal during the same authenticated session. New unseen content can be offered on a later session; a quiet navigation cue allows access meanwhile. Do not invent time-based cooldowns or polling.
- Keep the adapter's UUID-based, per-admin semantics and same-window/cross-tab notifications. Two admins remain independent; editorial revisions and dev-to-stable transitions preserve receipts; rollback/future-entry visibility is constrained by the bundle.
- Receipt write failure leaves a usable modal and honest state. Do not clear shared cues on failure. Inspect existing local-storage failure behavior and make failures observable without turning the modal into another task.

### History and navigation

- Keep a permanent **What's New** entry in the platform-admin sidebar. Desktop and mobile must expose it. The account-menu entry can remain if it helps discovery; avoid adding multiple competing status indicators.
- The modal includes an obvious **View All Updates** link to `/whats-new`. Clicking it closes the dialog and navigates to the full history. The history page remains directly reachable and useful after all updates are seen.
- Remove the **Mark Presented Updates Read** button and per-item **Unread** badges. Prefer one quiet dot on the permanent navigation entry. Remove the Unread tab/count from the default history layout unless user review supplies a reason to retain it; history is a browsable feed, not an inbox.
- On the history page, acknowledge an entry when its content becomes visible in the viewport. Reuse a simple shared visibility hook/observer; acknowledge each eligible UUID once and clean up observers. Opening history alone must not acknowledge offscreen entries. Test this deterministically without arbitrary sleeps.
- Preserve dates, titles, compact area/type metadata, rich Markdown, screenshots/captions, source links, credits, and prominent Security/Action Required information. Distinguish these meaningful metadata from receipt bookkeeping.

### Layout and brand

- Modal: concise What's New header, close control, date/title/content, optional image, then navigation and View All Updates. Size to content until constrained; keep header and footer outside the long-content scroller. Use available viewport height on mobile. Retain focus trapping, Escape behavior, accessible naming, and focus restoration using the shared primitive.
- History: calm page title and readable feed with consistent margins, spacing and line length. Keep the development-only preview controls collapsed and visually separate from the product experience. Do not show diagnostic fixture controls inside the normal modal.
- Both surfaces share one community-links component: GitHub icon + **GitHub**, Discord brand icon + **Discord**, Bifrost mark + **Website**, in that order. Footer links remain available while long content scrolls. Retain the subtle rainbow edge; avoid ornamental gradients behind body text.
- Destinations: `https://github.com/gobifrost/bifrost`, `https://discord.gg/f7TCcWX2s`, `https://gobifrost.com`. Keep external-link accessibility and existing brand icon assets.
- The current acknowledgment button uses the shared Button default, whose class definition includes `h-10`, `px-4`, and `gap-1.5`. Its cramped appearance has not been diagnosed as a missing class. Inspect actual computed styles and surrounding spacing for remaining actions; do not assert a cause from the screenshot or broadly change the shared Button to fix this feature. Use concise labels and product-standard touch targets, padding and icon gaps.

## Keep the Completed Foundation

Latest verified final release: `v1.4.1`, commit `ade714a08b95c84daa285e859ebece55f45c6754`. Frozen target: `32aeea16c99c27d1278b5576f09080e29a59cca2`. All 124 landed PRs are classified: 68 PRs grouped into 16 highlights, 39 Other summaries, 17 Omit reasons. The preview adds a staged announcement with no invented source PR. Initial prose, upgrade material and security applicability remain drafts.

- Canonical schema/tool: `product-updates/schema.json`, `scripts/product_updates.py`.
- Verified sources/classifications: `product-updates/inventory.json`, `product-updates/dispositions.json`, `product-updates/evidence/`.
- Draft prose: `product-updates/drafts/initial-backfill/entries/`.
- Rendered release review artifacts: `product-updates/previews/initial-full-release.md`, `initial-candidate.md`, `announcement-preview.md`, `no-new-highlights.md` and sibling bundles.
- Production contract proposal: `docs/plans/product-updates-production-contract.md`. Update its acknowledgment semantics to match the new interaction, while retaining revision safety, running-build visibility, independent admins and admin-only authorization.
- Pending GitHub ruleset rollout: `docs/plans/product-updates-ruleset-rollout.md` and before/proposed/patch files. No rules have been applied.

Keep the source eligibility fixes: every cited source and declared prerequisite must be verified and reachable before a rich entry appears. Reviewed landed portions of pending groups remain Other changes; a release cannot silently drop required security or upgrade notices. Keep source/credit associations, Jack/bot exclusions, safe Markdown/assets and deterministic rendering. Public GitHub asset URLs pin the asset-bearing implementation commit; that commit is local and has not been published.

## Primary Edit Surface

- `client/src/pages/ProductUpdatesPreview.tsx` and its test: current feed/rendering, controls, footer, manual acknowledgment. Extract shared content/community components as needed so modal and history do not diverge.
- `client/src/lib/product-updates-preview.ts` and its test: replaceable local adapter, receipt events, bundle filtering and fixture states.
- `client/src/components/layout/ProductUpdatesSidebarLink.tsx`, `ProductUpdatesMenuItem.tsx`, their tests, and shell integration in `Header.tsx`, `Sidebar.tsx`, `AccountMenuContent.tsx`.
- `client/src/App.tsx`: DEV-gated admin route; keep runtime wiring and fixture imports absent from production builds.
- `client/e2e/preview/product-updates.admin.spec.ts`, `client/playwright.preview.config.ts`, `scripts/test-product-updates-preview.sh`: dedicated happy path and screenshot wrapper.

Inspect current exports and consumers before editing. No production endpoint/model change is needed for this mock. No background job or browser polling is needed. No tags, release publication, deployment, live ruleset edits, Discord posts, or invite/server modifications are authorized.

## Acceptance and Verification

1. Show the modal automatically for an admin with unseen eligible content; opening and actually rendering slide one acknowledges only that UUID. Loading/failure/offscreen content does not acknowledge entries. Closing and receipt updates do not cause modal loops.
2. Advance through the modal, then open history via View All Updates. Confirm the permanent sidebar route on desktop/mobile. Observe history acknowledgment on presented entries, with no required manual receipt action and no per-item unread badges.
3. Verify independent admins, cross-tab cue updates, dev-to-stable UUID continuity, and rollback/future filtering. Preserve the existing boundary tests rather than replacing them with tests of implementation details.
4. Inspect actual desktop/mobile and keyboard flows, including long Markdown, an image, missing-image state and receipt failure. Check computed spacing and clipping; take screenshots of modal and history on both viewport sizes. Explain remaining rough edges candidly.
5. Run focused component/adapter tests and the one browser happy path through `./test.sh`; client types/lint and the production-build exclusion check. Run tooling/gate tests only if those files or their contracts change. Read the testing skill for test-harness requirements; no retries, skips or timeout increases to mask failures.

Existing verification: 31 tooling/gate tests, 21 client tests, one browser happy path, scoped Python checks, client types/lint and production exclusion passed. Exact commands and known failure dispositions are in `docs/plans/product-updates-progress.md`. The final online Action-tag check hit GitHub HTTP 403 after passing earlier with unchanged pins; offline pin/mirror checks passed. Full backend/Vitest/Playwright suites and live merge-queue readiness were not run. Do not treat these as verification of the new modal.

Use the existing worktree debug stack (`./debug.sh status`) before booting anything. Last URL: `https://bifrost-3fc9b38f-lsk5.eu1.netbird.services/whats-new`; re-check it because the exposure is ephemeral. Keep credentials out of documents, process arguments and logs. Follow the existing preview wrapper's in-memory status handling.

Update `docs/plans/product-updates-progress.md` with changes, tests, screenshots and remaining gaps. Return the reviewable branch and screenshots to Jack. Ask only for a concrete unresolved contract or scope expansion; resolve routine layout choices with the direction above. Content approval and production delivery remain separate from this UX revision.
