# Chat V2 Master Plan

**Status:** Active orchestration plan
**Created:** 2026-04-27
**Author:** Jack Musick + Claude
**Program spec:** `docs/superpowers/specs/2026-04-27-chat-v2-program-design.md`
**Sandbox findings:** `docs/superpowers/specs/2026-04-27-chat-v2-sandbox-bwrap-findings.md`

This is the durable orchestration artifact for the Chat V2 program. It names every sub-project, tracks its state, and points at the detailed specs and plans. Future sessions resuming this work should read this file first.

## Program goal (one paragraph)

Bring Bifrost's chat experience to feature parity with Claude.ai, ChatGPT, and Copilot, so an org can credibly use Bifrost as their primary LLM chat interface. Differentiation is org-managed cost/model/access controls plus first-class custom tooling owned by the org and authored against the Bifrost SDK — without the MCP-server-installation overhead end-users face on Copilot or Claude. See the program spec for full motivation and architectural decisions.

## Sub-project status board

| # | Sub-project | Status | Spec | Plan | Worktree | PR |
|---|---|---|---|---|---|---|
| 1 | Chat UX overhaul (M1 Workspaces) | In progress | [chat-ux-design](../specs/2026-04-27-chat-ux-design.md) | (this file) | `.worktrees/140-workspaces-foundations` | TBD into `feature/chat-v2` |
| 2 | Code Execution | Not started | TBD (uses sandbox findings doc) | TBD | TBD | — |
| 3 | Skills | Not started | TBD | TBD | TBD | — |
| 4 | Artifacts | Not started | TBD | TBD | TBD | — |
| 5 | Web Search | Not started | TBD | TBD | TBD | — |

Status legend: `Not started` → `Brainstorming` → `Spec written` → `Plan written` → `In progress` → `In review` → `Merged` → `Done`.

## Phase order (locked in, as of 3fc616ca)

```
Phase 1: Chat UX (1)         ──merge──▶
                                        Phase 2: Code Execution (2)  ──merge──▶
                                                                                Phase 3: Skills (3) + Artifacts (4) ──merge──▶
                                                                                                                                Phase 4: Web Search (5) ──merge──▶
```

Web Search has no real dependencies past Phase 1 and can be slotted in earlier if there's bandwidth, but it's small enough to wait.

## Why this order

- **Chat UX is the visible foundation.** Branching, projects, attachments, compaction. None of the other sub-projects have a UI surface to integrate with until this lands.
- **Code Execution before Skills/Artifacts** because real-world skills (Anthropic's docx/pdf/pptx/xlsx, plus most of the open-source ones) shell out to npm/pandoc/soffice/pdftoppm. Without sandboxing, Skills v1 would be inert markdown and Artifacts v1 couldn't do binary formats. We learned this from inspecting `anthropics/skills` directly; the original ordering was wishful and got corrected.
- **Code Execution ships as a "built-in" first.** It's the first agent capability that exercises the sandbox, available on every agent (gated by org config) before the Skills loader exists. When Skills (3) lands, it generalizes the loading mechanism but the built-in run_code tool stays.

## Worktree + branch strategy

Each phase gets its own worktree off `main`:

- Phase 1: worktree at `~/GitHub/bifrost-chat-ux/`, branch `feature/chat-ux`.
- Phase 2: worktree at `~/GitHub/bifrost-code-exec/`, branch `feature/code-execution`. Created from main *after* Phase 1 merges.
- Phase 3: worktrees `~/GitHub/bifrost-skills/` and `~/GitHub/bifrost-artifacts/` (parallel), both from main *after* Phase 2 merges.
- Phase 4: worktree `~/GitHub/bifrost-websearch/`. Slotted whenever.

Each worktree boots its own isolated test stack (`./test.sh stack up` produces a per-worktree Compose project). Tests, hot reload, and dev URLs work in parallel across worktrees without conflict.

**Merge gate between phases:** the previous phase's PR must be merged to main before the next phase's worktree is created. This avoids parallel divergence on shared files (especially the chat content-block plumbing in (1) and the agent_executor.py wiring touched by (2)).

**Subagents within a worktree:** when a sub-project has internally parallelizable work (e.g., "build the branching UI" and "build attachment upload" in Phase 1), the active session dispatches in-session subagents per the `superpowers:dispatching-parallel-agents` skill. The orchestration agent for each phase is the regular Claude Code session running in that worktree.

## Cross-phase coordination notes

- **Sub-project (1) introduces structured content-block types** to the message protocol. Sub-projects (3) and (4) extend those types. (1) should leave the message-block schema versioned/extensible enough that adding new block types in (3) and (4) doesn't break wire compatibility.
- **Sub-project (1) introduces "projects" / scoped instructions.** Sub-project (3) Skills can later be scoped to projects too. (1) should consider the "scope" abstraction generally enough to extend.
- **Sub-project (2) adds an `execution_runtime` dimension** to the existing execution model. Diagnostics page changes are part of (2). Sub-project (3) Skills will reference `execution_runtime: sandbox-python` etc. when declaring what runtime a skill needs.
- **Sub-project (4) Artifacts needs (2) for binary generation.** A docx-generating skill in (3) calls (2) under the hood to invoke `npm install -g docx && node generate.js`, with the result captured as an artifact handled by (4).

## Cross-cutting concerns (program-wide)

These appear in every sub-project's spec and are not separately tracked here:

- Org / role / personal scoping (matches existing forms, agents, workflows model).
- Cost accounting (per-message, per-skill, per-sandbox-execution, per-search).
- Permissions enforcement (every new endpoint goes through the existing access_level/role model).
- Test coverage (unit + e2e + vitest + playwright).
- Pre-completion verification (pyright, ruff, tsc, lint, full test suite).

## Decisions log (program-wide)

Key cross-phase decisions, with the commit that recorded them. The committed program spec is the source of truth; this is a quick-scan record.

| Date | Decision | Commit |
|---|---|---|
| 2026-04-27 | First-party tools, not MCP-as-primary | ab759e1d |
| 2026-04-27 | Adopt Agent Skills spec verbatim | ab759e1d |
| 2026-04-27 | Code Execution = sandbox-as-child-process inside existing workers | ab759e1d |
| 2026-04-27 | Sandbox has zero SDK access | ab759e1d |
| 2026-04-27 | Three artifact rendering tiers (initially four; collapsed to three after sandbox investigation) | ab759e1d → 3fc616ca |
| 2026-04-27 | Privilege model corrected: pod stays unprivileged, but seccomp=Unconfined required (Docker default blocks the bwrap path) | fd725f2c, 744eb3df |
| 2026-04-27 | Phase order: Code Execution before Skills+Artifacts (was reversed) | 3fc616ca |
| 2026-04-27 | Skills scoping = global / org / role / personal (mirrors existing forms/agents) | 3fc616ca |
| 2026-04-27 | Anthropic docx/pdf/pptx/xlsx skills are source-available NOT OSS — cannot ship as global skills | 3fc616ca |
| 2026-04-27 | Code Execution ships as a built-in agent tool first (option A), Skills loader generalizes it later | (this doc) |
| 2026-04-27 | Admin error UX: preflight check + auto-detected per-platform fix instructions banner | (this doc) |
| 2026-04-27 | Chat UX sub-project: linear-only, no branching | 4a1f0356 |
| 2026-04-27 | Workspaces as first-class concept with personal/org/role scoping; synthetic Personal workspace per user | 4a1f0356 |
| 2026-04-27 | Tool layering = intersection of agent + workspace | 4a1f0356 |
| 2026-04-27 | Attachments = files only (images/PDFs/CSVs/text); Bifrost entities go through knowledge sources | 4a1f0356 |
| 2026-04-27 | Compaction is lossless (DB unchanged, only model context summarized); per-model-aware threshold | 4a1f0356 |
| 2026-04-27 | Model resolver as shared infrastructure; allowlist chain platform→org→role→workspace→conversation→message with provenance tooltips | 4a1f0356 |
| 2026-04-27 | Cost surfaced as 3-tier symbolic glyphs (⚡/⚖/💎); dollars only in admin dashboard | 4a1f0356 |
| 2026-04-27 | Logical model aliases (bifrost-fast/balanced/premium) + deprecation remap table to insulate from provider churn; Message.model is immutable history | 4a1f0356 |
| 2026-04-29 | M3 ships branching (parent/leaf model); linear-only non-goal removed; retry-with-different-model dropdown dropped | (M3 PR) |

## How a future session resumes this work

1. Read this file to see status.
2. Read the program spec (`2026-04-27-chat-v2-program-design.md`) for context.
3. Read the sandbox findings doc (`2026-04-27-chat-v2-sandbox-bwrap-findings.md`) before touching Phase 2.
4. For the current sub-project, read its spec and plan.
5. Check the worktree status (does the expected worktree exist? Is it on the right branch? Is its test stack up?).
6. Continue from where the plan left off, or update this status board if something has shifted.

## Open program-level decisions

- **Where each sub-project's plan + spec lives.** Specs go in `docs/superpowers/specs/`. Plans go in `docs/superpowers/plans/`. Naming convention: `YYYY-MM-DD-<sub-project>-{design,plan}.md`.
- **Whether Web Search (5) gets dragged forward.** It has no real dependencies after Phase 1 and is small. If there's developer bandwidth during Phase 2 or Phase 3, it can ship in parallel. Decision deferred to when that bandwidth question is real.
- **Cost accounting design** is folded into each sub-project's spec but the cumulative dashboard ("show me what Chat V2 costs this org per month") may need a small dedicated piece of work after all sub-projects ship. Not currently tracked as a sub-project; revisit.

## Branching strategy

Chat V2 is a long-lived, multi-PR program. Rather than merge each milestone (M1, M2, …) directly to `main`, all sub-project work targets a long-lived integration branch — `feature/chat-v2`. Individual milestones merge into that branch via small PRs; the integration branch merges to `main` once the program is shippable end-to-end.

- `feature/chat-v2` is the integration branch, branched from `main` at program start.
- Each milestone gets its own short-lived branch (e.g. `140-workspaces-foundations`) that PRs **into** `feature/chat-v2`, not main.
- Re-base or merge `main` into `feature/chat-v2` periodically to keep drift small.
- The final `feature/chat-v2 → main` merge is a single big PR at program end.

## Cross-program follow-ups (gaps surfaced during M1)

These are tracked as separate issues, **scheduled into existing milestones where possible**. They aren't lost.

| Concern | Where it lands |
|---|---|
| Image OUT (model-returned image content blocks) + per-provider capability matrix (`supports_images_in/out`, `supports_pdf`, `supports_audio`) with a graceful fallback when user picks an incompatible model | Extends M2 model registry + M4 attachments rendering |
| Live context-budget indicator on the composer (filling-circle dot that opens a flyout containing the manual Compact button) — promote from M7 polish to be co-located with M5 Compaction since they share the same UI surface | M5 |
| Per-user default model (the most personal layer in the resolver chain — platform → org → role → workspace → **user** → conversation → message) | M2 |
| Memory (cross-conversation persistent memory; distinct from compaction which only summarizes within a chat) | New sub-project candidate, post Phase 4 — to be added to the program design |
| Floating composer (§16.4) — currently described as M1 but in practice ships in M7. Move formally to M7 | M7 |
| Conversation rename UI (today rename is implicit-on-first-message) | M7 |
