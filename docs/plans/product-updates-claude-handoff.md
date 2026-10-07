# Product Updates: Current Handoff for Claude

The earlier detailed UX revision plan is superseded by Jack's feedback and the simpler implementation in this branch. This file is context for a later Claude review; no Claude session has been launched.

Worktree: `/home/jack/.codex/worktrees/ae47/bifrost`, branch `codex/product-updates`. Inspect current status and commits before editing. Jack owns design/content approval. Read current AGENTS.md, DESIGN.md and `docs/plans/product-updates-progress.md` for test evidence and remaining scope.

## Current Interaction

- Eligible unseen updates automatically open in a scrollable modal for platform admins entering the authenticated shell. The displayed batch is acknowledged after rendering; fetching or a failed load alone writes no receipt. Closing keeps the underlying route intact. The modal stays quiet after the batch has been presented.
- The modal has **View All Updates**, which opens `/whats-new` in a new tab, keeping the original modal available. The permanent path is **Help → Release Notes**. The admin-only Help (?) trigger sits immediately left of the user icon and contains Documentation, Release Notes, Website, Discord, GitHub, and the copyable version. Direct history visits remain unobstructed.
- Modal and history group notes under a single date heading per local calendar day, newest first. History is a plain feed. No manual mark-read action, per-item unread badge, inbox tabs, carousel, counters, or viewport-tracking system.
- Modal and history share content rendering and the compact GitHub / Discord / Website footer with brand icons and a rainbow edge. Mobile links are labeled icons; the modal combines them with View All Updates and Done in one row, and full history has Done in its fixed header. Dates, feature screenshots and substantive upgrade/security instructions remain; routine area/type badges are removed. Sources and credits sit under collapsed Source Details. All note links open a new tab.
- The announcement says release notes are available in the app and Bifrost is now on Discord. All initial prose remains draft; publication has not occurred.

## Preserve

The frozen backfill accounts for 124 PRs since `v1.4.1`: 16 canonical notes covering 68 PRs, 17 smaller customer-facing fix/hardening bullets, and 39 documented omissions. Nine of the canonical notes are feature cards, four are compact fixes, two are compact hardening notes, and one dependency security notice is release-only. The app fixture adds a staged announcement. Tool/schema, coverage reconciliation, source eligibility, attribution evidence and release generation are already implemented. Use `docs/plans/product-updates-progress.md` and `product-updates/previews/` rather than inventing release claims or regenerating prose independently.

Primary UI files: `client/src/components/layout/ProductUpdatesDialog.tsx`, `ProductUpdateContent.tsx`, `HelpMenu.tsx`, `VersionMenuItem.tsx`, `Header.tsx`, `client/src/pages/ProductUpdatesPreview.tsx`, and the local adapter `client/src/lib/product-updates-preview.ts`. Their sibling tests and the dedicated browser happy path cover the interaction.

This remains a development-only admin preview. Production API/database persistence, publishing, deployment, tags, live ruleset changes and Discord/server changes are separate work. The production contract proposal lives in `docs/plans/product-updates-production-contract.md`.

Inspect the actual rendered desktop/mobile flow before calling the design accepted. Current screenshots, exact test commands and failures are recorded in the progress document. Continue in this worktree; preserve existing edits and coordinate before starting overlapping writers. Do not treat a passing test as Jack's design approval.

## Copy and Release Trigger

The 17 draft notes now use short titles, one or two sentences, verified app links and screenshots where available. Authoring requires a General Writing/Humanizer pass; validation caps titles at 10 words and bodies at 60 words, or 100 for security/action-required notes. These limits constrain length; a human review still checks clarity and accuracy. Relative app links render as plain in-Bifrost instructions in GitHub release bodies.

The release skill separates adding/preparing release data from publication. Prepare and review notes during their PR, aggregate approved entries since the previous final, then freeze the body for publication. Production announcements should trigger on unseen approved UUIDs in the installed build at admin shell entry. Editorial revisions and promotion from dev to stable preserve receipts. The current DEV mock uses local receipts; durable production receipts remain unimplemented.

Preview Controls is hidden on the normal review URL. Opt in with `/whats-new?controls=1` to use development fixture controls. The plain `/whats-new` route is the customer-style review surface.

Features retain individual headlines under New Features and Functionality. Fixed and Security notes render as compact bullet lists under Bug Fixes and Hardening, with necessary upgrade actions inline and grouped source/credit details collapsed. Smaller notes join the matching lists in history; omitted maintenance stays in the ledger. `in_app: false` withholds a note from app display and receipts without deleting release material or bypassing review.

Completeness was rechecked against freshly fetched main: it still matches frozen 32aeea16c. Effective Access and retention notes now have actual feature screenshots (revision 4); the older generic Roles illustration was replaced. GitHub preview images are pinned to asset-bearing commit 2ac8231e7. This Docker environment has no Kubernetes connection, so it cannot capture that conditionally available settings panel.

Action Required is now limited to the verified CLI/SDK compatibility break and names that audience. Retention and general hardening/sync notes have no such label; the retention default and Keep forever choice remain explicit. Follow the stricter authoring criteria in `docs/product-updates-authoring.md`, rather than flagging optional configuration or release-review chores.
