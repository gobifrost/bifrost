# Product Updates: Current Handoff for Claude

Jack authorized completing production delivery and merging. This branch implements the runtime rather than a development preview. Inspect current git/PR status before editing; no Claude session has been launched.

## Current Experience

Platform admins on every image receive a scrollable What's New modal for unseen approved entry UUIDs. Rendering the modal or history acknowledges the displayed batch automatically. PostgreSQL receipts belong to the authenticated admin and survive browser changes, editorial revisions, rollbacks and dev-to-stable promotion. There is no manual Mark Read action or unread badge. Direct history visits remain unobstructed.

Help (?) immediately left of the avatar contains Documentation, Release Notes, Website, Discord, GitHub and the copyable version. `/whats-new` is the permanent history path. View All Updates and note links open new tabs. Modal and history have visible Done controls, fixed metadata/actions outside the scroller, a rainbow edge and compact branded social links. Dates remain metadata and are not displayed.

Features have concise individual headlines and verified screenshots where available. Bug Fixes and Hardening use compact bullet lists. Source Details collapse attribution. Routine dependency/catalog maintenance is omitted from the app; the dependency security notice remains release-only. Action Required is limited to the scoped CLI/SDK compatibility notice.

## Content and Build

The initial cumulative inventory covers 126 PRs since v1.4.1: 16 approved canonical notes covering 68 PRs, 17 smaller customer-facing changes and 41 documented omissions (including both validator bootstrap repairs). Fifteen canonical entries are visible in the app. The Release Notes and Discord announcement keeps its stable UUID and must cite this feature's actual PR; pending sources stay withheld until their landed metadata is verified.

Every API/client image is prepared from the shared approved source. The API serves an immutable bundle; screenshots have content-hashed URLs. Database storage contains presentation receipts, not a second editable release-note source. No runtime GitHub requests, model generation or polling are involved.

GitHub release preparation independently filters the explicit previous-full-release commit through the target commit. The inventory stays cumulative so later releases do not erase app history. Adding/preparing content is separate from publishing a tag, GitHub release or Discord message. Those publication actions have not been requested.

## Files and Evidence

Read `docs/product-updates-authoring.md`, `docs/plans/product-updates-production-contract.md` and the current section of `docs/plans/product-updates-progress.md`. Primary client files are `ProductUpdatesDialog.tsx`, `ProductUpdateContent.tsx`, `HelpMenu.tsx`, `Header.tsx`, `pages/ProductUpdates.tsx` and `services/productUpdates.ts`. The API contract, router, service, receipt ORM and migration use the existing platform patterns.

The production Docker-image browser journey proves automatic presentation, durable acknowledgement, Help navigation, screenshots, desktop/mobile scrolling and suppression in a fresh browser. Current screenshots are `docs/plans/product-updates-*-review.png`. The former Preview Controls/local-storage fixture path has been removed.

Use focused tests and the merge queue. Preserve user-approved copy and source evidence; do not invent release claims or future merge commits. Live ruleset changes, release publication and Discord/server administration remain separate work.
