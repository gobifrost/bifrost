# Product Updates: Current Handoff for Claude

The earlier detailed UX revision plan is superseded by Jack's feedback and the simpler implementation in this branch. This file is context for a later Claude review; no Claude session has been launched.

Worktree: `/home/jack/.codex/worktrees/ae47/bifrost`, branch `codex/product-updates`. Inspect current status and commits before editing. Jack owns design/content approval. Read current AGENTS.md, DESIGN.md and `docs/plans/product-updates-progress.md` for test evidence and remaining scope.

## Current Interaction

- Eligible unseen updates automatically open in a scrollable modal for platform admins entering the authenticated shell. The displayed batch is acknowledged after rendering; fetching or a failed load alone writes no receipt. Closing keeps the underlying route intact. The modal stays quiet after the batch has been presented.
- The modal has **View All Updates**, which closes it and opens `/whats-new`. The permanent path is **Help → Release Notes**. The admin-only Help (?) trigger sits immediately left of the user icon and contains Documentation, Release Notes, Website, Discord, GitHub, and the copyable version. Direct history visits remain unobstructed.
- History is a plain feed. No manual mark-read action, per-item unread badge, inbox tabs, carousel, counters, or viewport-tracking system.
- Modal and history share content rendering and the persistent GitHub / Discord / Website footer with brand icons and a rainbow edge. Dates, areas/types, sources, credits, screenshots and substantive upgrade/security notices remain.
- The announcement says release notes are available in the app and Bifrost is now on Discord. All initial prose remains draft; publication has not occurred.

## Preserve

The frozen backfill accounts for 124 PRs since `v1.4.1`: 16 highlights covering 68 PRs, 39 Other summaries, and 17 documented omissions. The app fixture adds a staged announcement. Tool/schema, coverage reconciliation, source eligibility, attribution evidence and release generation are already implemented. Use `docs/plans/product-updates-progress.md` and `product-updates/previews/` rather than inventing release claims or regenerating prose independently.

Primary UI files: `client/src/components/layout/ProductUpdatesDialog.tsx`, `ProductUpdateContent.tsx`, `HelpMenu.tsx`, `VersionMenuItem.tsx`, `Header.tsx`, `client/src/pages/ProductUpdatesPreview.tsx`, and the local adapter `client/src/lib/product-updates-preview.ts`. Their sibling tests and the dedicated browser happy path cover the interaction.

This remains a development-only admin preview. Production API/database persistence, publishing, deployment, tags, live ruleset changes and Discord/server changes are separate work. The production contract proposal lives in `docs/plans/product-updates-production-contract.md`.

Inspect the actual rendered desktop/mobile flow before calling the design accepted. Current screenshots, exact test commands and failures are recorded in the progress document. Continue in this worktree; preserve existing edits and coordinate before starting overlapping writers. Do not treat a passing test as Jack's design approval.
