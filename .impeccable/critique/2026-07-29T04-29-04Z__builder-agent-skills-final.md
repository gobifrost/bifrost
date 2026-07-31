---
target: private Solution Builder readiness, launch, and Agent Skill management
total_score: 39
max_score: 40
na_heuristics:
p0_count: 0
p1_count: 0
timestamp: 2026-07-29T04-29-04Z
slug: builder-agent-skills-final
---

Method: dual-agent review combining an independent source/design assessment
with an independent detector/responsive-risk pass, reconciled against the live
Playwright states and focused component tests.

## Design Health Score

| # | Heuristic | Score | Evidence |
|---|---|---:|---|
| 1 | Visibility of System Status | 4 | AI readiness, Skill provenance, validation, managed state, launch progress, build state, preview state, revisions, and recoverable errors are visible where decisions happen. |
| 2 | Match System / Real World | 4 | Build, Agent, Skill bundle, files, Preview, Code, Changes, and staged review use familiar app-builder language and interaction models. |
| 3 | User Control and Freedom | 4 | Users can browse, replace, or detach owned bundles; managed bundles are clearly read-only; workbench panes, revisions, preview devices, undo, download, and retries remain available. |
| 4 | Consistency and Standards | 4 | The access selector, dropzone, file tree, progress state, setup card, tabs, diffs, and responsive workspaces reuse established Bifrost patterns. |
| 5 | Error Prevention | 4 | AI readiness fails closed, archives are validated before storage, managed instructions cannot drift, duplicate launch is prevented, and promotion remains gated on deployed source. |
| 6 | Recognition Rather Than Recall | 4 | Bundle source, root-relative path, canonical `SKILL.md`, file inventory, active Skill, revisions, changed paths, and next actions stay visible. |
| 7 | Flexibility and Efficiency | 3 | Search, responsive presets, file browsing, revision selection, resizable panes, and mobile switching are strong; very large reviews could still add keyboard next/previous-change controls and file-status filters. |
| 8 | Aesthetic and Minimalist Design | 4 | The centered Build start/setup states, concise launch progress, selected-value-only access control, and two-pane Skill browser keep dense detail behind the relevant task stage. |
| 9 | Error Recovery | 4 | Upload, bundle read, builder, code, diff, preview, and promotion failures expose a retry, corrective instruction, cleanup, or reversible detach path. |
| 10 | Help and Documentation | 4 | Admin AI setup guidance, archive requirements, canonical-instruction behavior, source badges, managed-state copy, and readiness blockers explain what users need at the point of action. |
| **Total** |  | **39/40** | **Production-grade app-builder and Agent Skill experience** |

## Verdict

The surface now expresses one coherent model: an inline Agent owns editable
instructions, while a bundled Agent owns a canonical portable `SKILL.md`; a
Solution-managed bundle is root-relative and read-only. Build appears only when
it is actionable, except that administrators receive a direct configuration
path. Launch feedback is tied to real asynchronous stages and does not add an
artificial delay.

No P0 or P1 design findings remain. The remaining opportunity is optional
expert acceleration for navigating very large source reviews.

## Evidence

- Desktop Build setup, Build launch, Builder workbench, Agent settings, and
  Agent Skill browser states.
- Shared TipTap Edit/Preview instructions and bundle Markdown
  Preview/Source states, including labeled formatting controls.
- Mobile Agent / Preview / Code / Changes workspaces at 390×844.
- Focused component, service, API, and Playwright regression coverage.
- Shared select sizing, reduced-motion launch behavior, read-only managed
  state, archive validation, and accessible progress messaging in source.
