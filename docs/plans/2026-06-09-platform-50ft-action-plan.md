# Bifrost Platform — 50-Foot Action Plan

Date: 2026-06-09
Status: orchestration index — each workstream is independently plannable/executable in its own worktree
Produced by: high-level second-opinion review (Solutions design + whole dev story), 8-agent research fan-out
Audience: an orchestrator (human or agent) splitting these into independent plans

## The intent this plan serves

Bifrost competes by giving an MSP team a **layered, agent-native development story**: a shared
library of solved problems (`_repo/`), sealed deployable units (Solutions) installed per-customer
with multi-tenant separation enforced by execution context, a CLI-first offline `npm run dev`-class
loop, and surfaces (CLI/MCP/API) consistent enough that coding agents understand the platform
implicitly. The compounding bet: one team builds a solution once, AI accelerates each build, and the
platform deploys it to N customers from one place.

Every workstream below either removes a contradiction with that story or closes a gap in it.

---

## Verdict on the Solutions design (summary — full reasoning in session notes)

The core decisions are right and mutually reinforcing: one-writer/read-only-by-construction kills
drift structurally instead of by merge; "installable surface, not a repo" keeps identity separate
from source; `solution_id` on the ExecutionContext (not a header) gives one resolution chokepoint;
v2 standalone apps beside v1 avoids a flag-day; non-destructive uninstall + orphan/reattach respects
customer data. The architecture review found **no HIGH bugs and no architectural flaws** after the
adversarial QA pass.

The one strategic hole: **there is no upgrade/versioning story for an installed solution** (no
`version` field, no upgrade-in-place; disconnected installs can only uninstall→reinstall). For a
platform whose pitch is "deploy solutions to many customers and keep compounding," upgrade is the
marketplace primitive, not an edge case. That is WS-2.

---

## Workstreams

### WS-1 — Solutions: finish the tail (close-out)
**Size:** S-M · **Worktree:** existing `solutions-success-criteria` · **Blocks:** WS-2, WS-13

Remaining verified-open items from the QA fan-out (D3 BifrostHeader styling and D1/F4 watch-refusal
are already FIXED in branch — do not redo):
- Drive forms/agents **as resolution sources** (form-submit and agent-tool-invoke resolving their own
  solution workflows/tables) end-to-end — designed, never reproduced live.
- Drive the git-connected install lifecycle live (auto-pull, `bifrost deploy` refusal, app rebuild
  from clone, app-dist S3 sweep).
- Full browser walkthrough of install/manage UI; remaining LOW UI/UX findings.
- F3: manifest field-list duplication — add ORM↔Pydantic↔collector round-trip parity test.
- Decide multi-install config-key collision (namespace per solution vs document org-global keys).
- README prose: `global_repo_access` seals **code imports**, not data fallback (QA verdict: ungated
  data fallback is safe and intended).
- Land PR #347 after human review focused on: auth/concurrency code, zip-install atomicity,
  orphan/reattach logic.

Docs: `docs/plans/2026-06-04-solutions-success-criteria.md` ·
`docs/plans/2026-06-09-solutions-qa-fanout-findings.md` ·
`docs/plans/2026-06-07-solutions-shakeout-RESUME.md` ·
`docs/superpowers/specs/2026-06-08-solution-workflow-resolution-chokepoint-design.md`

### WS-2 — Solutions: versioning + upgrade-in-place
**Size:** M · **Depends:** WS-1 · **Blocks:** WS-13 (catalog), any marketplace ambition

- Add `version` to `bifrost.solution.yaml` + the install record now (cheap, future-proofs bundles).
- Define **upgrade = deploy-over-install**: full-replace reconcile already preserves table rows and
  config values; the orphan/reattach machinery already solves "entity removed then re-added." Most
  of the mechanism exists — this is a design doc + a `bifrost solution upgrade` (or
  install-over-existing) path + compat checks (schema migrations on tables, config declarations
  added/removed) + UI "update available" affordance for connected installs.
- Defer signing/security-review/marketplace distribution, but write the one-page position now so
  community sharing isn't promised before the trust boundary exists (installing third-party
  solutions = running third-party Python with org credentials).

Docs: `docs/superpowers/specs/2026-06-06-solutions-orphan-and-reattach-design.md` ·
`docs/superpowers/specs/2026-06-06-solutions-configs-and-management-ui-design.md`

### WS-3 — Agents: rescue + finish the Chat V2 branch, then run the rest of the program
**Size:** L-XL · **Independent of** Teams (see WS-4)

The work lives on **`origin/feature/chat-v2`**: 5 unmerged commits, ~104k insertions — M1
Workspaces foundations (#144, the "projects" feature), M2 model resolver + curation (#146), M3
backend branching + per-conversation instructions (#156), with the M3-frontend handoff scoped in
#197. It is 133 commits behind main. (A redundant local docs-only `feat/chat-v2` was deleted
2026-06-09 after verifying every file already exists on main — the Chat V2 specs/plans are all on
main.) First move: **catch-up merge from main** (expect the `v1.d.ts` regen conflict — recipe in
CLAUDE.md), re-verify M1–M3 live, then execute the M3-frontend handoff, then continue the program
(Code Execution → Skills/Artifacts/Web Search) per the master plan.

Docs: `docs/superpowers/specs/2026-04-27-chat-v2-program-design.md` ·
`docs/superpowers/plans/2026-04-27-chat-v2-master-plan.md` (on `feat/chat-v2`) ·
`docs/superpowers/specs/2026-04-27-chat-v2-sandbox-bwrap-findings.md` (required reading for the
code-exec sub-project)

### WS-4 — Teams agent delivery: build it as a Solution on the CSP/GDAP rails
**Size:** M-L · **Depends:** WS-1 (solution as the packaging), CSP buildout (exists, live)

Research verdict: **partially achievable, and the hard part is already built.** No single global
"deploy to all tenants" API exists, but with GDAP the per-tenant pipeline is fully scriptable with
zero customer clicks: Partner Center programmatic consent → Graph `teamsApp publish` to the tenant
org catalog (delegated token only — must impersonate a GDAP admin; the workspace's
partner-refresh-token→tenant-token exchange in `bifrost-workspace/modules/microsoft/auth.py` is
exactly this) → `teamsAppInstallation` for users/teams (app permissions OK) → pin via setup policy
(weakest API). One multi-tenant Entra app + one bot endpoint + one Teams app package serves all
tenants; no Copilot licensing needed for a Bot Framework bot.

Architecture split:
- **Platform-core (small):** one stable "conversation turn in/out" API for agents (webhook in →
  agent run → reply out), so any channel adapter is just a client. The Agent `channels` field today
  is a placeholder with zero delivery infrastructure behind it — don't build per-channel logic into
  the platform.
- **Solution (the rest):** Bot Framework endpoint, tenant→org mapping + allowlist, JWT validation,
  the GDAP onboarding pipeline, catalog re-publish on update. Owner's instinct confirmed: this is a
  published Solution managed in an app, riding `bifrost-workspace`'s CSP integration
  (`features/microsoft_csp/workflows/*`, `apps/microsoft-csp/`).

Sources: Teams-feasibility research report (session notes; key cites: Graph teamsapp-publish,
team-post-installedapps, GDAP secure application model on learn.microsoft.com).

### WS-5 — GitHub UI sync: deprecate
**Size:** M (~1 wk removal) · **Depends:** WS-1 decision on connected-mode plumbing

**Target model (Jack, 2026-06-09):** the platform integrates with GitHub in exactly two ways, both
one-writer, both files-only:
1. **Solution source** — git-connected installs (already designed; pull → deploy).
2. **Optional `_repo/` mirror, pull-and-discard** — when a repo is connected, GitHub is the writer
   and `_repo/` becomes **read-only in the platform**; sync = pull files + discard local state.
   Code files only — **no entity manifest sync in this path** (entities are API/CLI-managed,
   consistent with the workspace model). UI shrinks to: connection status + "sync now" + last-pull.
   No diff/commit/conflict/push surfaces anywhere.
This applies the Solutions one-writer philosophy to `_repo/` itself: drift becomes impossible by
construction instead of being managed by a 14k-LOC bidirectional sync (engine ~3.2k + UI ~2.8k +
tests ~6.1k LOC — all removable; no FKs from entities to GitHub config). The manifest machinery
(~4.4k LOC) survives only as the Solutions bundle format.

**Jack's own migration path (2026-06-09), which removes the watch-dependency himself:** ~95% of his
development moves to Solutions once live; `_repo/` work happens offline with **`bifrost run` as the
primary test surface** (so local run STAYS for developers — the WS-8 gate is end-users only); then
git is re-enabled globally as the read-only mirror. Removal of watch/push/sync sequences AFTER that
migration, not before.

**Decision (Jack, 2026-06-09): the CLI sheds its file-sync + git surface too.** `bifrost
watch/push/pull/sync` and `bifrost git *` are removed (graceful "this command was removed —
here's the new model" messages; `CONTRACT_VERSION` bump per the contract-gate pattern), and the
matching MCP file-sync tools are disabled the same way. Entity management already left `_repo/`;
code now follows: `_repo/` is edited in the platform editor, or git-mirrored read-only. This also
drops the heavyweight CLI/server deps the perf review flagged (GitPython server-side; awscli/npm
shrink from the image as their callers die). **Sequencing caveat (hard):** Jack's own daily
workspace loop runs `bifrost watch --mirror` — the read-only GitHub mirror mode (or a
solution-start equivalent for `_repo/` work) must exist and be driven BEFORE removal lands, and
the bifrost:build skill's SDK-first mode needs rewriting against the new model in the same change.

Sequence: extract the slim pull-and-import primitive Solutions connected-mode needs → build the
read-only mirror mode → migrate Jack's workspace loop onto it → remove desktop engine, router,
scheduler git-op dispatch, all commit/diff/conflict UI, CLI sync/git commands, MCP file tools.

Docs: `docs/plans/2026-02-25-git-sync-refactor-design.md` ·
`docs/plans/2026-03-11-incremental-manifest-import.md` · inventory in session notes.

### WS-6 — Performance: import closures, not images
**Size:** S-M · **Independent** · Quick wins, do early

Measured in prod (`bifrost` ns, all roles already share ONE 354 MiB image, differing only by
command — the feared breaking image split is **not needed and not the lever**):
idle ≈ 3.7 GiB total; 6 workers ≈ 2.7 GiB (73%).
1. **Template spawn re-imports the worker `__main__` closure** (`template_process.py:443`,
   spawn ctx): ~110 MB × 6 pods ≈ **650 MiB pure waste**. Launch template from a minimal entry
   module. Biggest single win.
2. **Eager heavy imports in shared modules**: `anthropic`/`openai` via `llm/factory.py`;
   `from fastapi import Depends` in `core/database.py` (drags Starlette into worker/scheduler/
   template); `mcp`→`uvicorn` via `mcp_client`; `numpy` via pgvector ORM import. Lazy-import pass ≈
   **400–550 MiB** fleet-wide. (Honor the no-unrequested-fallbacks rule — these are mechanical moves,
   not behavior changes.)
3. Right-size: 6 workers idle at 5–8m CPU → trial 3–4 replicas (~1 GiB); api requests 256Mi but uses
   400Mi → raise request.
Process pool is already spawn-on-demand (no warm pool to shrink). Together: worker pods ~455 Mi →
~250–280 Mi with zero deploy-shape change.

### WS-7 — RBAC: close the live authz gaps now; then capability scopes
**Size:** S now + L program · **Blocks:** WS-8

**Immediate (small, verified 2026-06-09):** `/api/files/*` is properly `CurrentSuperuser`-gated
(an earlier finding claiming otherwise was wrong), so non-admin local code cannot touch `_repo/`.
What's true: `/api/sdk/sessions*` and the SDK data-plane endpoints in `routers/cli.py` accept any
authenticated user (org-scoped via the C2 gate) — so `bifrost run local.py` *works* for regular
users with their own org-scoped privileges. That is privilege-equivalent to them calling the REST
API directly (no escalation), but it should be an explicit product decision, and the session
endpoints need an ownership audit (can user B poll/continue user A's session id?). Decide + e2e.
**Program:** the scopes/capabilities migration (replace is_superuser/is_provider; mint scoped tokens
instead of in-process on-behalf-of impersonation) is **designed but zero code merged** — the
`entity-access-phase1` worktree is test infra only. Direction docs are consistent; needs an
implementation plan with phases that land independently.

Docs: `docs/superpowers/specs/2026-05-21-design-decisions-summary.md` ·
`docs/superpowers/specs/2026-05-21-roles-ux-rethink.md` ·
`docs/superpowers/specs/2026-04-30-table-policies-design.md` ·
`docs/superpowers/specs/2026-05-21-table-policies-custom-claims.md` · RBAC inventory in
`feat/table-access` worktree + Obsidian design.

### WS-8 — User-grade CLI
**Size:** M · **Depends:** WS-7 immediate fixes; benefits from capability scopes

The CLI as local-first product surface for *users*, not just developers: login + run cloud
workflows + power local skills. With WS-5 removing sync/watch/git outright, this workstream is
mostly graceful end-user targeting: ship the ephemeral-sessions/keyring auth design
(`docs/superpowers/specs/2026-04-29-cli-auth-ephemeral-sessions-design.md`, designed, unshipped);
gate remaining dev-only commands (solution deploy, entity mutations) by capability with
server-side enforcement (WS-7); a `bifrost workflows run <name>` cloud-execute path; policy
introspection ("what can I run/see"). **Decision (Jack, 2026-06-09): block local `bifrost run`
for non-dev users as a UX gate** — server-side check on the SDK session endpoints + a friendly
CLI message. Not a security boundary (it's privilege-equivalent to their REST access), just
keeping end users and their LLMs off a confusing surface.

### WS-9 — Files Web SDK + File Policies: restart from spec — **APPROVED, active**
**Size:** M-L · **Sequence:** after table-policies shared-engine extraction · Jack 2026-06-09:
green-lit; first task is the commit inventory of the stale worktree (rebase vs cherry-pick is
decided by what that shows — the spec is the source of truth either way)

Spec is complete and good (`.worktrees/170-file-policies/docs/superpowers/specs/2026-05-01-file-policies-design.md`);
core resolver built (`api/shared/file_policies.py` in that worktree); ORM/REST/SDK/UI never landed
and the worktree is stale/entangled (58 mixed commits). Restart in a fresh worktree, cherry-pick the
resolver + tests, follow the spec's own rollout order (shared-engine refactor → schema → REST relax
from CurrentSuperuser → web SDK signed-URL batch → admin UI → subscriptions). This closes "file
interactions in apps require workflows."

### WS-10 — Execution engine: per-SOURCE burst caps (DEFERRED)
**Size:** M · **Independent** · Jack 2026-06-09: defer; when picked up, the shape is per-source
(event source / schedule / any known burst generator, including global ones) concurrency caps —
not per-org fairness, since the offender can be global.

Landed (April, PR #141): webhook ingress rate limit, schedule overlap SKIP, stuck-execution
detection. Still true: **post-ingress is one FIFO queue + consumer-wide prefetch + one shared
process pool** — a 1000-event burst from one org delays every org's scheduled work until drained
(`jobs/consumers/workflow_execution.py:36,62`, `services/execution/process_pool.py:305-310`).
Bounded fix, not a rearchitecture: per-org in-flight cap at pool admission (requeue/delay over-cap
messages) and/or a second "scheduled/interactive" consumer lane with reserved slots. Design doc
explicitly deferred this — write the small follow-up spec against
`docs/superpowers/specs/2026-04-27-execution-hardening-rate-limits-design.md`.

### WS-11 — Buildfrost: keep the centralization, outsource the harness
**Size:** M (in `bifrost-workspace`, not platform)

Survey verdict: the **centralization layer is the real asset and already a daily driver** (v1.2:
projects/runs/tasks/conversation-turns tables, plan.md as source of truth, standing-context docs,
Kanban + project UI). The **bespoke OpenRouter loop is the weak half** (HTTP file ops ~200ms/op,
context-budget ReadError, no multi-user locks, Gemini Flash executor).

**Direction settled (Jack, 2026-06-09): Buildfrost orchestrates external agentic PLATFORMS, hosts
no harness.** Self-hosting the Claude Agent SDK was rejected (it ships the Claude Code node bundle —
the heavy container dependency Jack avoided once already; non-interactive Claude sessions now bill
API usage anyway). Instead: Buildfrost's control plane (specs/plans/tasks/runs in Bifrost tables +
UI) dispatches work to cloud agent platforms — Claude Code cloud sessions / GitHub `@claude`
Actions, Codex cloud, or OpenRouter loops where cost demands — and tracks results. The enabling
work is making the bifrost dev workflow run headless inside those sandboxes: non-interactive auth
(token env, WS-8 ephemeral-sessions design), `pip install <instance>/api/cli/download`, offline
`bifrost run` against a dev instance, `bifrost solution deploy` — plus the WS-17 skill shipped as
a plugin so cloud agents know the platform. WS-17 is therefore a prerequisite of this workstream.

Paths: `bifrost-workspace/buildfrost_*.py`, `bifrost-workspace/apps/buildfrost/`,
`bifrost-workspace/projects/buildfrost/{spec,plan}.md`, `projects/_system/*.md`.

### WS-12 — UI facelift
**Size:** M · **Independent** · Lower priority, parallelizable

Hypothesis confirmed: styling is largely centralized — 55 components in `client/src/components/ui/`,
semantic-token usage outnumbers raw palette classes ~719:310, single `index.css` + tailwind config.
A facelift = (1) design-token re-theme (palette/radius/typography/density in CSS vars + tailwind
config), (2) restyle `ui/` primitives, (3) bounded sweep of ~310 raw `bg-<color>-N` drift usages
outside `ui/`. Use the frontend-design skill; do it after or alongside Chat UX (WS-3) so the chat
surface lands in the new skin rather than being repainted twice.

### WS-13 — Community solution distribution (the flywheel seed)
**Size:** M-L ongoing · **Depends:** WS-1, WS-2

Bifrost is self-hosted — no SaaS catalog. Distribution model (Jack, 2026-06-09): store solutions in
the **`bifrost-workspace-community`** repo (`solutions/*/*`), and/or host portable zips on
gobifrost.com; users download and **drag-drop install into their own instance**. Later: "connect a
publish repo" (a git-connected source pointed at the community repo). The flywheel logic stands:
port the top `bifrost-workspace` features (CSP/GDAP, proposals, HaloPSA reporting, troubleshooting)
into versioned solutions — every port is a brutal real-world QA pass on Solutions AND becomes
community inventory. WS-2 (version field + upgrade) is the prerequisite that makes downloaded zips
upgradeable rather than reinstall-only. WS-4 (Teams) is the flagship port.

### WS-14 — Deterministic dead-code / deslopify loop — *expanded from owner's item*
**Size:** S setup + recurring

Make it tooling, not vibes: `vulture` (or dead) + ruff's unused rules for Python, `knip` +
`ts-prune` for TS, with checked-in baseline files so CI fails only on NEW dead code; one-time
fan-out sweep to burn down the baseline. Complements the existing lint-test pattern
(`test_dto_flags.py`, thin-wrapper tests) and the CLAUDE.md no-dead-code rule.

### WS-15 — Sandbox/runtime hardening — **Phase 1 DONE; probes done; Phase 2 queued**
**Size:** M · Jack 2026-06-09: green-lit ("execution should access nothing but the API endpoints").
Plan + empirical probe addendum: `docs/plans/2026-06-09-execution-sandbox-hardening-plan.md`.
Phase 1 (non-root/caps/readonly-fs/no-SA-token + compose parity + posture guardrail) landed on the
Solutions branch, all drives green. Open decisions: T2 (S3 fallback → recommend API module-fetch
endpoint), T3 (bwrap needs CAP_SYS_ADMIN — Phase 3 posture trade-off needs an explicit call).
Phase 2 (env scrub + parent-minted engine token) starts AFTER the memory-slimming branch lands
(same files).

Workers run as root (known gap); customer code and (eventually) third-party solution code runs in
those workers. MSP security diligence will ask. The bwrap findings doc
(`docs/superpowers/specs/2026-04-27-chat-v2-sandbox-bwrap-findings.md`) already maps the options;
non-root workers is the cheap first step independent of the full Chat V2 sandbox.

### WS-16 — Per-org AI cost governance — **DEFERRED**
**Size:** M · **Depends:** none (AIUsage data exists) · Jack 2026-06-09: defer — not hard enough
to spend premium-model cycles on now; when picked up, spec via a mid-tier agent and execute cheap.
Do not let this displace WS-1/2/4/9/15.

Split platform vs product (Jack, 2026-06-09): the **platform** ships governance *primitives* only —
per-org metering (exists: `AIUsage`/`AgentStats`), budgets/caps enforcement, burn-rate query API,
alert hooks. The MSP-facing *product* experience (e.g. managing an Integrations-as-a-Service
offering, pricing, customer reporting — and later memory/usage analytics) is a **Bifrost app/
solution each MSP builds or installs**, not platform UI. Bifrost's own version of that app is a
WS-13 community solution candidate.

### WS-17 — bifrost:build skill overhaul (hub + curated subskills + accuracy gate)
**Size:** M · **Sequence: NOW, against the settled Solutions spec** (Jack, 2026-06-09: "since
we're settled on the spec… rebuild the skill for codex/claude in a worktree"). The skill teaches
the Solutions-era workflow (`solution start`, offline `bifrost run`, deploy; `_repo/` as shared
lib) — which doesn't use watch/push/sync, so the pending WS-5 removals don't churn it. Only
legacy-sync content is excluded. Both Claude (`skills/`, plugin-distributed) and Codex plugin
variants, plus the Solutions plan gets a "skill second-pass" line item.

Problem: the central build skill grasps the development plot but fumbles specifics — CLI flags,
API endpoints, and especially the tables **web SDK vs Python SDK signature differences**. Direction:
keep one hub skill that routes to **curated subskills per surface** (CLI, API endpoints, patterns,
and per-entity guides: apps, agents, forms, tables, solutions); **kill `llm.txt`** as a source of
truth. The load-bearing new piece is a **deterministic accuracy gate**: generate ground truth from
the system itself (CLI `--help` dump, the OpenAPI spec, SDK signatures via introspection) and diff
it against the skill docs in CI — doc drift becomes a red check, not a vibe. The gate machinery is
content-agnostic and could be built early, but the curated content must wait: export/import already
removed, watch/push/sync/git scheduled for removal, `solution start` new — writing the docs now
means rewriting them in weeks. Relevant artifact: `docs/superpowers/plans/2026-04-19-unified-versioning.md`
(on main) feeds WS-2; `feat/llms-txt-and-design-workflow` (Feb branch) is the llm.txt origin and
dies with this.

### WS-18 — CI/merge pipeline restructure (speed + release channels)
**Size:** M-L · **Independent** · Scoping in flight (dedicated review running 2026-06-09)

Jack's framing: ~17-minute merge critical path; GitHub merge queue requires moving the repo to the
gobifrost org (risks not yet understood); instinct says e2e runs in the wrong place. Proposal under
review: main merges run unit/lint/types only; full e2e runs on a new **pre-release** tag channel +
releases; `../kubernetes` (prod) tracks pre-release; users advised onto pre-release/release instead
of `:dev`. The review covers: per-job timing breakdown of real runs, harvesting the three abandoned
CI-speed experiment branches, Kodiak-vs-merge-queue, a fact-checked org-transfer risk list (ghcr.io
namespace does NOT redirect — every manifest reference breaks; git remotes do), and alternatives
(path-filtered e2e, sharding, batched merges, continuous e2e-on-main with auto-revert). Output doc:
`docs/plans/2026-06-09-ci-pipeline-restructure-plan.md` (forthcoming).

---

## Sequencing at 50 feet

```
NOW (small, high-leverage):  WS-7 authz gap fix · WS-6 perf quick wins · WS-14 setup
SHIP THE FLAGSHIP:           WS-1 close-out → PR #347 → WS-2 upgrade path → WS-13 catalog (+WS-4 Teams as flagship port)
LONG PROGRAMS (parallel):    WS-3 Chat V2 program · WS-7 capability-scopes program → WS-8 user CLI
CLEANUP/POLISH (slot in):    WS-5 GitHub deprecation (after WS-1 connected-mode decision) · WS-9 files
                             · WS-10 fairness · WS-11 Buildfrost · WS-12 facelift · WS-15 · WS-16
```

Dependency spine: **WS-1 → WS-2 → WS-13/WS-4** is the competitive arc (sealed installs → upgrades →
inventory → Teams flagship). Everything else removes drag.

## Source index (read before planning a workstream)

- Solutions: success criteria + QA findings + shakeout RESUMEs (paths in WS-1/WS-2)
- Chat V2: program design + master plan on `feat/chat-v2` (WS-3)
- Teams feasibility: session research report, Microsoft Learn cites (WS-4)
- GitHub sync inventory: session report; refactor/manifest docs (WS-5)
- Perf measurements: session report (kubectl + empirical import RSS deltas) (WS-6)
- RBAC/CLI: 2026-04-29/2026-05-21 spec set + session gap report (WS-7/WS-8)
- File policies: spec in `.worktrees/170-file-policies` (WS-9)
- Execution hardening: 2026-04-27 spec + session starvation map (WS-10)
- Buildfrost/CSP: session survey of `~/GitHub/bifrost-workspace` (WS-11/WS-4)
