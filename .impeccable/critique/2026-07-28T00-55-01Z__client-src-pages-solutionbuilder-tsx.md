---
target: private Solution Builder workspace and Agent skill visibility
total_score: 20
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 3
timestamp: 2026-07-28T00-55-01Z
slug: client-src-pages-solutionbuilder-tsx
---
## Design Health Score

| # | Heuristic | Score | Key issue |
|---|---|---:|---|
| 1 | Visibility of System Status | 3 | Source, preview, stale, build, and error states are visible, but skill use and disabled-action reasons are not. |
| 2 | Match System / Real World | 2 | Source versus Preview is clear; sessions, turns, revisions, and bundles remain implementation vocabulary. |
| 3 | User Control and Freedom | 3 | Undo, source download, preview reload, and sessions give meaningful recovery. |
| 4 | Consistency and Standards | 2 | Shared components are consistent, but the workspace lacks a coherent workbench interaction model. |
| 5 | Error Prevention | 2 | Restore is confirmed, but users cannot inspect the diff they are about to restore. |
| 6 | Recognition Rather Than Recall | 1 | Skill identity, files, change details, and meaningful session names are absent. |
| 7 | Flexibility and Efficiency | 2 | Chat, sessions, and the route bar help, but there are no device presets, compare tools, or expert accelerators. |
| 8 | Aesthetic and Minimalist Design | 1 | Actions, statuses, IDs, tabs, and empty space have competing visual weight. |
| 9 | Error Recovery | 2 | Errors are visible, but often do not expose a concrete diagnosis or next action. |
| 10 | Help and Documentation | 2 | Empty states explain the immediate state, but builder and skill concepts lack contextual help. |
| **Total** |  | **20/40** | **Acceptable — significant improvement required** |

## Design Specificity Verdict

The workspace is structurally specific but experientially under-specified. It has
the correct builder nouns—chat, preview, revisions, source, deploy—but it still
feels like a generic dark administrative shell. It does not expose the distinctive
thing Bifrost is doing: an Agent follows a portable skill bundle to author a
multi-entity private Solution whose exact changes can be inspected and promoted.

The deterministic Impeccable scan found zero source-pattern violations in
`SolutionBuilder.tsx`. That is not evidence that the UX is complete; it means the
problems are information architecture, hierarchy, workflow coverage, and rendered
layout rather than lint-level styling.

No reliable browser overlay was available. A Playwright screenshot at 1280×720 was
used as the fallback visual signal.

## Overall Impression

The workspace is cautious and functional, but it reads as “chat and wait” rather
than “build, inspect, verify, and promote.” Its strongest idea—Source versus
Preview—is present, but the evidence needed to trust a generated change is absent.

## What Is Working

- Source and Preview are explicitly separated, with a stale-preview warning.
- Undo, source download, preview reload, and session scoping provide real recovery.
- Reusing the existing chat transcript and tool-call presentation avoids a second
  incompatible messaging model.

## Priority Issues

### P1 — Skill-backed behavior is invisible

The builder is backed by `bifrost-build`, and general Agents can carry portable
skill bundles, but the UI shows only “App Builder.” Agent settings do not expose
the bundle binding, the workspace does not identify the active skill, and tool
activity does not distinguish skill-asset reads from ordinary tools.

Impact: users cannot tell which instructions or assets shaped a result, verify that
the expected skill was active, or understand the difference between an ordinary
Agent and a skill-backed Agent.

Fix: add a persistent Skill identity surface with name, description, bundle state,
and asset count; add a Skill section to Agent settings/details; label
`read_skill_asset` activity as skill use; and provide an asset browser plus
portable export action.

Principle: Content and information architecture.
Principle: Interactions, feedback, and system status.

### P1 — The review drawer does not perform the review task

The “Files” tab contains only deployed-revision metadata. Revision rows provide
summary/date/size and restore/download actions, but selecting a revision does not
show files, a diff, or build/deploy outcome.

Impact: users cannot verify what the model changed before restoring, promoting, or
trusting a preview. The core review workflow is missing.

Fix: replace the placeholder with a three-part review surface: revision list, file
tree, and selected-file diff/details. Revision selection should reveal source hash,
build/deploy result, changed files, and the exact source-to-preview relationship.

Principle: User intent and task success.
Principle: Content and information architecture.

### P1 — The compact viewport layout competes with itself

In the captured 1280×720 state, the chat composer and open drawer occupy the same
visual band, and revision content sits immediately beneath the composer. The page
technically renders, but the primary authoring control and review content visually
collide.

Impact: common laptop-height users lose confidence about which region owns focus
and may obscure the content they are trying to inspect.

Fix: use explicit workspace rows (`minmax(20rem, 1fr)` plus a bounded drawer row),
make the drawer resizable/collapsible, and enforce a minimum chat/preview height.
At compact heights, open review content as a sheet or replace the lower workspace
instead of compressing both simultaneously.

Principle: Layout, spacing, and responsive behavior.
Principle: Accessibility and inclusive use.

### P2 — The action hierarchy does not follow the build stage

Undo, Download source, Open app, and Request promotion are presented together while
status chips and raw revision IDs compete for attention.

Impact: the workspace does not make the next meaningful action obvious. A new
source revision, a deployed preview, and a promotable build should not present the
same hierarchy.

Fix: make the stage-appropriate action primary: Describe/continue while authoring,
Open preview after a green deploy, and Request promotion only after readiness.
Move revision IDs and secondary actions into a quieter status rail or overflow menu.

Principle: Visual hierarchy.
Principle: User intent and task success.

### P2 — Preview and unavailable states are underpowered

The preview supports route entry and reload but no device presets. `Open app`
explains unavailability through a tooltip attached to a disabled control.

Impact: authors cannot validate responsive output, while keyboard and touch users
cannot discover why the app cannot open.

Fix: add labeled desktop/tablet/mobile viewport presets, an explicit preview
toolstrip, and persistent readiness copy such as “Deploy a green revision to open
the app.” Keep unavailable controls focusable only when they can explain recovery,
or render the explanation adjacent to them.

Principle: Affordance and signifiers.
Principle: Layout, spacing, and responsive behavior.
Principle: Accessibility and inclusive use.

## Persona Red Flags

**Alex, the power user:** cannot inspect a change quickly, compare revisions, use
device presets, or identify the exact skill/tool source of a result.

**Jordan, the first-time solution author:** sees Source, Preview, revision IDs,
sessions, and promotion without a clear staged path. “Files” promises content it
does not deliver.

**Sam, the keyboard or assistive-technology user:** disabled-action explanations
depend on hover, asynchronous status changes are not consistently announced, and
the compact-height collision can disrupt reading and focus order.

**Riley, the deliberate reviewer:** cannot verify files, diffs, hashes, build
outcomes, or skill provenance before promotion.

## Minor Observations

- Generic “Session 1” naming becomes weak as history grows.
- Raw eight-character revision IDs are useful metadata but too visually prominent.
- Promotion belongs in a readiness panel, not permanently beside routine authoring
  actions.
- The workspace needs a workbench identity, but should keep Bifrost’s existing
  component system rather than introduce a detached visual language.

## Questions to Consider

- Should the default view optimize first for creating a change, verifying a change,
  or governing a promotion?
- What minimum evidence must a user see before they can confidently say “this
  Agent used the intended skill”?
- When the review drawer is open on a laptop-height screen, which region should
  yield: chat, preview, or review?
