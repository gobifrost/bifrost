# Program handoff: RBAC (R1 → R4) → Builder (B1 → B4) → release → desktop app

**Written:** 2026-09-26 by the "Code Builder Continued" session, for a fresh session to resume.
**Owner/reviewer:** Jack. Every slice is its own PR to `main`; merges need Jack's explicit approval.
Nothing ships as a release before Jack's substantial review.

## The larger plot

1. **Land the permission restructure (RBAC)** safely, in small reviewable slices (R1–R4 below).
   It is the highest-risk piece and everything else depends on it. The bar is "110% solid".
2. **Land the Code Builder** on top (B1–B4), with its UI rebuilt on main's modernized shell.
3. **Release** after Jack's review.
4. **Then a desktop app** that works more like the new Microsoft Copilot
   (https://blogs.microsoft.com/blog/2026/09/25/introducing-the-new-copilot-with-home-code-and-autopilot/):
   chat-first, the standard left list of chats, with Home, Artifacts, etc. as the top-left items
   (like today's chat plus Home). Code happens either inline in chat or in an expanded code view.
   Main already has Home collections (`home.py`) and durable realtime chat runs (#698) to build on.
   Desktop shell choice (Tauri/Electron/PWA), and whether the desktop app ever runs code locally,
   are still open questions to ask Jack when that phase starts.
   - Copilot's "Autopilot" is **out of scope**: Jack sees it as triggering your own webhooks and
     schedules, which Bifrost already has.
   - Copilot "Code" context: natural-language building of apps/dashboards/workflows for
     non-developers, sandboxed, hosted internal apps, grounded in work context. Builder UX should
     converge on "a chat with a workspace attached expands into the workbench" — same shape as the
     desktop app, so the UX gets built once.

## Status at handoff

- **#810** (engine SDK over worker-local socket) merged before review. Post-merge review found the
  socket app lacked the API's request-context middleware and exception handlers (audit rows silently
  dropped; 409/422 became 500s). Fix is **PR #811** (`fix/sdk-socket-audit-parity`, worktree
  `.claude/worktrees/sdk-socket-parity`): shared `src/core/app_wiring.py`, caller attribution via
  signed `engine_caller_*` token claims (audit only), `audit_logs.execution_id`. Open, not merged;
  watch its CI/reviews; merge only with Jack's approval. Clean up its worktree/branch/test stack after.
- **Code Builder reference stack**: debug stack for `.worktrees/code-builder` may still be running
  (port mode, `http://localhost:38479`) with a test app "Onboarding Checklist". Tear it down with
  `./debug.sh down` from that worktree when no longer needed.
- **R1**: this branch (`rbac-r1-operation-catalog`, worktree `.claude/worktrees/rbac-r1`) contains
  only this doc. Nothing ported yet.

## Why this program exists

Draft PR #620 (`codex/code-builder-pydantic-integration-20260816`, reference worktree
`/home/jack/GitHub/bifrost/.worktrees/code-builder`) built the native Builder **and** a complete
authorization redesign together: ~860 files, 57 commits, 170 conflicting files vs current main.
It is not rebased. It is a **reference branch**: each slice below is re-cut from current `origin/main`
carrying only its own files, porting code from #620 and resolving against main as it is today.

Main moved a lot since #620 was cut (merge-base `1d91e5a58`, 2026-08-24), notably:
#732 design-system modernization (1,349 files), #781 Kubernetes build execution, #788 supervised
services (new `services.py`, `kubernetes.py` routers), Home collections (`home.py`), #777/#778
Solution inbound/outbound access, #783/#785 output caps + model failover, #698/`chat_run_agentless`,
and #810 (engine SDK over a worker-local HTTP socket — router logic moved into `api/shared/sdk_*.py`).
#811 (follow-up to #810: audit attribution + exception parity on the socket) is open.

## The authorization model (settled; read `docs/plans/2026-08-20-builder-authorization-execution-resume.md` on the reference branch)

- Capability = **what** (`read` / `readwrite` / `execute`); Role-assignment **boundary** = **where**
  (exact Organization, Organization group, Managed organizations, Platform); resource grant/policy = **which object**.
- Platform Admin = immutable wildcard Role. Platform Operator = cross-customer support at Managed
  organizations, can inspect Builder work, cannot build, no Global/repo authority.
- Default roles must **reproduce today's access**: legacy superuser → Platform Admin at Platform;
  provider-org non-admin → Organization Member (home) + sticky Platform Operator at Managed orgs;
  everyone else → Organization Member at home org; legacy custom `roles.scopes`/`permissions` translate.

## Slices (decided with Jack 2026-09-26)

| Slice | Content | Behavior change |
|---|---|---|
| **R1** Operation catalog | Port `api/src/services/operation_catalog.py`, `operation_inventory.py`, contracts, generator, generated inventory/skill appendix, the "canonicalize X operations" commits, and the 18 MCP tool-name migrations (`20260817_*_mcp_tool_names.py` … `20260819_skill_file_tool_name.py`). Reference commits on #620: `6a0c5511a` through `2b1803831`, plus `32d6f5c4c`, `5d487df3c`, `3564d350c`. | MCP tool names only. **No authorization change.** |
| **R1 revision (2026-09-26)** | The reference "make X canonical" commits also rewrite each domain's MCP tools into thin REST wrappers (the MCP/REST drift CLAUDE.md describes). That changes MCP authorization/scoping behavior, so R1 is split: **R1a** framework only (catalog, inventory, generator, inert `action_scopes`, `operation_route` metadata on REST decorators; route dump must show metadata-only diff; no migrations, no MCP changes). **R1b…** one PR per domain batch (agents first, then forms, tables, …): MCP thin-wrapper rewrite + that domain's tool-name migration + parity tests, each with a persona × MCP-tool decision table (current MCP vs REST-wrapper outcome); every changed cell is an intended change for Jack to approve. | R1a none; R1b… MCP-only, Jack-approved. |
| **R2** Schema + evaluator, unenforced | Role assignments, boundaries, organization groups, capability catalog, default roles, `AuthorizationContext`/evaluator, backfill migration. Split `20260819_builder_authorization_boundaries.py`: RBAC half here (re-dated after main's head), Builder half later. Must cover tables new on main. **Prod-shaped migration test**: snapshot prod users/roles/role-assignments/org memberships (read-only, pseudonymized, kept out of the repo), run the migration, diff every user's effective access before/after. | None — nothing enforces yet. |
| **R3a** Identity/admin cutover | Users, roles, orgs, org groups, role-assignment UI, WebSocket channel auth, boundary header. **Propose to Jack**: replace the persistent "Working in" selector with page-inferred boundaries (the UX review found it produces dead ends and double org selectors). | Only Jack-approved decision changes. |
| **R3b** Entity domains | Agents, forms, tables, apps, workflows, knowledge, configs, claims, policies, files. | Same. |
| **R3c** Platform ops | Integrations/OAuth, MCP, GitHub, schedules, jobs, diagnostics, AI settings, usage, and main's new `home`, `kubernetes`, `services` routers plus ~115 inline legacy checks added on main since #620. | Same. |
| **R4** Lock-down | Remove provider-membership bypass; structural gate forbids `CurrentSuperuser`/`get_current_superuser`/`RequirePlatformAdmin` **and** inline `is_superuser`/`is_platform_admin`/`is_provider` checks in routers/services; only exception: execution-token path. | Deliberate tightening. |

Jack's rules (2026-09-26), apply in every slice:
- Only platform-org principals (bypass) may write global entities or `_repo`, ever. V1 apps have no regular-user write path.
- MCP must match REST exactly. System tools stay locked down.
- Solutions shared with people or roles (Builder collaborators and Solution Role grants from #620) must protect every read and write path of their entities with the same scoping rules. No side doors via app, file, table or MCP routes.

- Regular (non-bypass) users mutate nothing platform-managed except their own private agents. Allowed personal state: own profile/MFA/sessions, chats, Home pins/personal collections, notifications, memory, own OAuth credentials, and executing what they're permitted to run.

**Route access control list (R2 requirement, Jack 2026-09-26):** a checked-in manifest declaring an access class for EVERY HTTP route and MCP tool (personal / execute / table-policy / own-private-agent / embed-public / capability+boundary). CI fails on any unclassified route or tool, or a mismatch between the declared class and the code. Seed it from the 2026-09-26 mutation-route audit. No route or tool can be added without an explicit access decision. R3 slices convert routes by changing their manifest entries, and the persona tests assert each entry.

**Workflow execution identity (Jack, 2026-09-26):** workflow code runs under a platform execution identity rather than the invoking user's permissions. Jack's decision: solve this in RBAC through the delegated-execution phase (workflows act with the invoking user's permissions, plus explicit grants), not with an interim org-scoped engine token.

Invariant for every R3 slice: a persona × operation matrix (Platform Admin, Operator, Platform Builder,
Builder, org member, external user, other-org user × catalog operations) asserts **legacy decision ==
new decision**; intended differences are listed in a table in the PR for Jack to approve.

Required gates across the program: route-inventory tripwire (every route classified with capability +
boundary), structural gate incl. inline checks, persona matrix, prod-shaped migration test (R2),
Codex adversarial review of each auth diff, live persona walkthrough. **No** prod shadow evaluation.

Builder after RBAC: **B1** runtime via PlatformJob placement — Cloudflare is the *recommended production*
runner, Kubernetes (#781) supported, built-in local is the no-dependency default; local = file edits +
builds only, no command execution; reconcile with main's `chat_run_agentless`. **B2** usage limits vs
#783/#785. **B3** Builder domain. **B4** UI rebuilt on main's shell (see UX review below).

## Builder UX review (2026-09-26, live on the #620 reference stack)

Driven: admin setup → enable → private app brief → one Local turn (Sonnet 5 via OpenRouter, 3m46s)
→ workbench/preview, desktop 1440×900 and mobile 390×844.

**Design system rule (Jack):** UI must follow or improve the design system — no from-scratch
components unless they contribute to/improve it; nothing out of place; mobile must work. Canonical
contract is main's `DESIGN.md` (from `gobifrost/design-system`, local clone `~/GitHub/design-system`)
plus `docs/design-modernization/`. Use its primitives: WorkspaceHeader, contained workspace +
attached inspector, ExecutionSectionHeading, LogEntryRow, WorkspacePrimaryAction, ResourceCatalogCard,
tenant primary tokens. The #620 Builder pages predate #732 (old blue, pill badges, nested cards) —
**rebuild them, do not merge them.**

Keep (behavior): setup gating (Build hidden until AI + runner verified + explicit enable, readiness
checklist, runner self-test); durable turns you can leave and return to; workbench anatomy
(conversation | Preview / Code / Changes, revisions, Undo, Share, Download, Request promotion);
mobile focused panes.

Defects to fix regardless of redesign:
1. Turn status stays `queued` in the DB for the whole run, then flips to `succeeded`; UI says "Queued" for minutes.
2. Preview blank on revisit: runtime assets 401; relaunch renders raw JSON "This launch link is invalid or has already been used" in the frame (seen headless; confirm in a real browser).
3. A simple two-page app exhausted the turn budget before preview/deploy, then told a web user to "run the Solution's local dev server" (CLI advice leaking into the web product).
4. Setup CTA sends a Platform Admin to a red-shield "Choose another working context" dead end.
5. Cloudflare's HTTPS-callback requirement appears only as a post-save toast, no inline validation.
6. Worker logs "Agent 'Onboarding Checklist Builder' has 16 tool definitions" — verify the agentless runtime doesn't create per-Solution Agent identity.

Redesign direction: one "describe what you want" composer instead of three entry modes (Global
workspace / Private app / Organization workspace), one primary action; no double org selector
("Working in" + "Build in" → feeds the R3a page-inferred-boundary proposal); transcript uses main's
run Activity grammar instead of raw tool-name lists; header = one primary action + overflow, labeled
mobile buttons; chat-first convergence with the desktop app.

## R1: first steps for the fresh session

1. Read this file, `AGENTS.md`, and on the reference branch `docs/plans/2026-08-17-builder-capability-parity-execution.md`.
2. Decide how R1's catalog expresses per-operation authorization *without* R2's vocabulary. The catalog on
   #620 declares capabilities; R1 must stay inert (no enforcement). Options: port declarations as data only
   and validate membership in R2, or omit them in R1. Bring the choice to Jack if it changes surfaces.
3. Port against current main, where many router bodies now live in `api/shared/sdk_*.py` (#810).
4. MCP tool-name migrations: re-date after main's alembic head, verify single head, and check agents/apps
   bound to old tool names are migrated (these are user-visible renames — call them out in the PR).
5. Verification: operation-catalog/inventory tests, DTO parity, thin MCP wrapper test, contract version,
   skill appendix freshness, MCP e2e parity, `./test.sh quality api`.

Execution style Jack asked for: the orchestrating session owns design, review, and verification; cheap
executors (OpenCode `opencode-go/deepseek-v4.1-flash`, Sonnet subagents) do bounded mechanical work from
precise specs; never trust an executor's report without re-running checks.
