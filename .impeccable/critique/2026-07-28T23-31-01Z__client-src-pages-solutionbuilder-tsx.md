---
target: private Solution Builder workspace and Agent skill visibility
total_score: 37
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 0
timestamp: 2026-07-28T23-31-01Z
slug: client-src-pages-solutionbuilder-tsx
---
## Design Health Score

| # | Heuristic | Score | Evidence |
|---|---|---:|---|
| 1 | Visibility of System Status | 4 | Build state, active Skill, Source-to-Preview state, review readiness, selected revision, and recoverable errors are visible in context. |
| 2 | Match System / Real World | 4 | The workbench uses familiar Agent, Preview, Code, Changes, device-preview, and staged review language. |
| 3 | User Control and Freedom | 4 | Users can resize panes, switch mobile workspaces, inspect revisions, undo, download, reload previews, and retry failures. |
| 4 | Consistency and Standards | 4 | Tabs, file browsing, diffs, viewport presets, keyboard resizing, and staged primary actions follow established app-builder conventions. |
| 5 | Error Prevention | 4 | Promotion remains unavailable until source and deployed preview match, with a specific blocker such as “Deploy a preview before requesting review.” |
| 6 | Recognition Rather Than Recall | 4 | The active skill, complete companion-file inventory, generated files, diffs, changed paths, revision states, and runtime skill reads are visible. |
| 7 | Flexibility and Efficiency | 3 | Search, responsive presets, revision selection, resizable panes, and mobile workspace switching support expert use; dense review surfaces could add more accelerators. |
| 8 | Aesthetic and Minimalist Design | 3 | The hierarchy is substantially calmer and workbench-specific, though Code, Changes, and promotion review remain necessarily information-dense. |
| 9 | Error Recovery | 4 | Builder, code, diff, skill, preview, and promotion failures expose a visible retry or next action. |
| 10 | Help and Documentation | 3 | Contextual copy explains how bifrost-build guides changes, what a portable Agent Skill is, and why actions are unavailable; deeper in-product guidance could go further. |
| **Total** |  | **37/40** | **Strong — familiar, trustworthy app-builder experience** |

## Design Specificity Verdict

The private Solution Builder now reads as a real app-building workbench rather
than a generic administrative page. Its distinctive product model is explicit:
an Agent follows the portable `bifrost-build` skill, authors a multi-file
Solution, produces immutable revisions, deploys a preview, and presents exact
source evidence for review and promotion.

The deterministic Impeccable layout detector returned zero findings. Independent
source and rendered-layout reviews found no clipping, overlap, broken stacking, or
unusable mobile pane behavior in the checked desktop, mobile, Agent overview, and
Agent settings states.

## What Is Working

- A chat-led Agent pane sits beside Preview, Code, and Changes, matching the mental
  model users bring from contemporary app builders.
- The Code surface exposes a searchable real file tree and exact revision content.
- The Changes surface exposes revision history and real unified diffs instead of
  revision metadata alone.
- Desktop, tablet, and mobile presets make responsive preview checking immediate.
- Promotion follows the actual lifecycle: create source, deploy preview, update a
  stale preview, then request review.
- Agent Skill identity is visible in the builder, Agent overview, settings,
  downloads, companion files, and `read_skill_asset` tool activity.
- Mobile uses one explicit Agent / Preview / Code / Changes switcher and stacks
  source navigation over content instead of compressing a desktop split.
- Transient failures provide visible `Try again` affordances across the workbench.

## Remaining Refinements

### P2 — Dense source review can be faster for expert users

Long file trees and diffs are correctly bounded and scrollable, but review remains
reading-heavy. Future iterations could add keyboard file navigation, next/previous
change controls, collapse-all diff sections, and file-status filters.

### P2 — Promotion review is deliberately information-dense

The review workspace correctly surfaces pinned revision, hashes, build/deploy
state, changed paths, roles, connections, global configuration, and final scope.
For very large Solutions, progressive grouping or a compact review summary could
reduce scanning without hiding governance evidence.

### P3 — Contextual education could become richer

The interface now explains active skill behavior and readiness blockers, but a
first-run walkthrough or linked “How promotion works” reference could help new
authors understand the full private-to-company lifecycle sooner.

## Persona Check

**Alex, the power user:** can search source, inspect exact diffs, resize the
workbench, switch device presets, and see skill provenance. Additional keyboard
diff accelerators remain the main opportunity.

**Jordan, the first-time solution author:** gets a recognizable staged path and
plain-language next action rather than raw sessions and revisions alone.

**Sam, the keyboard or assistive-technology user:** receives labeled controls,
keyboard-adjustable pane sizing, explicit mobile navigation, persistent blockers,
and visible retry actions.

**Riley, the deliberate reviewer:** can verify pinned source, changed paths,
hashes, build/deploy state, roles, connections, and exact skill context before
promotion.

## Review Method

- Review A: independent Nielsen and design-specificity assessment of the final
  source and rendered states.
- Review B: independent deterministic layout detector and responsive risk review.
- Viewports checked: 1440×900 desktop and 390×844 mobile.
- Browser fallback: Playwright was used because the browser-overlay plugin was not
  available in this environment.
