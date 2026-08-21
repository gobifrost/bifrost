# Builder authorization execution resume

**Updated:** 2026-08-21
**Status:** implementation and Local/Cloudflare execution acceptance complete; Jack approved commit, push, and PR delivery on 2026-08-21; merge remains unapproved
**Canonical design:** `docs/plans/2026-08-19-builder-authorization-boundaries-execution.md`
**Worktree:** `/home/jack/GitHub/bifrost/.worktrees/code-builder-pydantic-integration-20260816`
**Branch:** `codex/code-builder-pydantic-integration-20260816`
**Pre-delivery branch HEAD:** `c5a68619e0f655e4332a2259c33668378bd468e3`
**Canonical main base:** `16e317e628d57ff4a4e57d179ff71d370445f6b5` (PR #608)

This document is the operational handoff for continuing the approved Builder
authorization, parity, and UI program. It records implementation state; it does
not supersede the canonical design.

## Non-negotiable constraints

- Work only in the worktree above. Do not edit the primary checkout.
- The pre-delivery tree contained 379 modified, two deleted, and 153 untracked
  paths spanning the reconstructed Builder and authorization program.
- Commit, push, and PR delivery were approved on 2026-08-21. Do not rebase or
  merge without separate explicit approval.
- Do not restore withdrawn Builder migration bodies. All schema changes are
  forward-only after the tombstone revision.
- Keep corrected main authoritative for scheduler, Platform Jobs, diagnostics,
  fixtures, `solution_deploy.py`, and `tables.py` shared behavior.
- Do not create a second AI loop, job system, runner contract, artifact model,
  or authorization evaluator.

## Settled product and authorization model

- Capability says **what**, Role-assignment boundary says **where**, and the
  resource grant/policy says **which object**.
- Capability actions are limited to `read`, `readwrite`, and `execute`.
- Global is the user-facing label for the Platform boundary; it is not a scope
  suffix. Platform does not imply Managed organizations or repository access.
- Boundaries are exact Organization, Organization group, Managed organizations,
  and Platform. A Role assignment may contain several selections.
- Platform Admin is the immutable wildcard Role.
- Platform Operator supports customers across Managed organizations and can
  inspect Builder work, but cannot start builds and receives no Global or repo
  authority.
- Builder is private-Solution-first, can build complete inert Solution bundles,
  and cannot publish them.
- Platform Builder adds direct domain authoring and repository authority and is
  normally assigned at both Managed organizations and Platform.
- Builder targets are `solution`, `organization`, and `global_repo`.
- One agentless, maintained runtime profile reuses the merged Pydantic AI/Chat
  harness. Builder sessions must not create per-Solution coding Agent rows.
- Native Builder, MCP, and CLI converge through the operation catalog and REST
  domain behavior. Builder tool discovery and execution re-authorize the same
  target and exact boundary.

## Completed implementation in the current tree

### Shared Builder and Pydantic AI integration

- Preserved Solution worktrees, sessions, revisions, preview restoration,
  attachments, artifacts, activity/progress, usage, compaction, and Worker /
  Cloudflare runner envelopes while reusing the shared Chat harness.
- Added the maintained agentless `BuilderRuntimeProfile`; existing/new Builder
  sessions no longer need Solution-specific Agent identity.
- Added Organization and Global Builder targets while retaining private
  Solution fencing and reviewed Global proposal/apply behavior.
- Builder REST/MCP tool discovery is filtered by the operation catalog and
  forwards the exact `X-Bifrost-Boundary` context.
- Chat and Builder now share the neutral workspace file-operation runtime.
  Public Chat tool names remain stable, while list/read/search/write/patch/
  delete behavior delegates to the same implementation used by local and
  Cloudflare Builder turns. Chat retains its ArtifactRef hydration,
  persistence, and tombstone lifecycle around that shared core.
- Workspace tool results now carry distinct model and display content, so
  structured tool metadata remains available to the model without leaking the
  appended JSON envelope into the human-visible Chat transcript.

### Canonical authorization foundation

- Added canonical capability/default-role definitions and forward-only
  migrations for Role assignments, boundary selections, organization groups,
  Solution Role grants, agentless runtime, and Organization Builder targets.
- Added central `AuthorizationContext`, boundary parsing/resolution, capability
  implications, exact resource-boundary checks, and operation-catalog checks.
- Added boundary-aware Role assignment services and cache invalidation.
- Added direct and Role-based private Solution grants without widening private
  visibility.
- Converted core Agents, Forms, Tables, Applications/app-code, Organizations,
  Users, promotions, Builder, and substantial Workflow operations to the
  central evaluator.
- Converted Role-to-resource assignment operations for Forms, Agents, Apps,
  Workflows, and Knowledge with exact resource-boundary checks.
- Converted Configs, Custom Claims, and reusable Policy Rules to catalogued
  capabilities, exact Organization/Platform mutation boundaries, Managed-only
  customer collection views, real requester attribution, and audit events.
- Converted the canonical `_repo` file contract (list, search, read, stat,
  exists, write, patch, and delete) from the legacy superuser gate to
  `repository.read/readwrite` at the explicit Platform boundary. Managed
  runtime file locations remain governed by file policies.
- Updated the Python SDK and `bifrost files` CLI to forward the inferred
  Platform boundary for `_repo` operations, so the Role cutover does not leave
  native Builder/MCP working while workstation CLI users fail authorization.
- Converted user lifecycle and user-Role assignment administration to explicit
  Platform/Managed/exact Organization boundaries.
- Provider-org membership no longer powers these converted routes. New
  non-admin provider members receive a sticky Platform Operator Role assignment
  at Managed organizations; the forward-only upgrade migration applies the
  same sticky assignment to existing provider staff.
- Converted the remaining Builder-critical human administration routes:
  Solutions/deploy/capture/runtime, Workflows, Events, Integrations and OAuth,
  MCP definitions/connections/config, Knowledge, GitHub, Schedules, packages,
  export/import, embeds, audit, authentication session revocation, dependencies,
  workers, Platform Jobs, scheduler diagnostics, notifications, maintenance,
  AI configuration/memory/instructions/sandbox settings, metrics, usage/ROI,
  and AI pricing.
- Added a structural route gate that rejects new `CurrentSuperuser`,
  `get_current_superuser`, or `RequirePlatformAdmin` dependencies. The only
  declared exceptions are the separately deferred workflow execution-token
  migration and engine-token-only SDK module transport.

### UI and MSP support behavior

- Role assignment UI supports Organization, Organization groups, Managed
  organizations, and Global selections without copying capability bundles.
- Builder navigation distinguishes `builder.read` support access from
  `builder.execute`. Platform Operators can open the customer-support Build
  library but cannot start a build.
- Organizations and Users navigation/routes are capability-driven rather than
  Platform-Admin-only.
- Users, Organizations, Role assignment dialogs, and bulk actions send explicit
  authorization boundaries.
- Organization-group creation no longer asks the operator to select the MSP
  owner. The API derives the single provider organization; the UI only asks for
  the pod name and customer members.
- Organization-group membership changes invalidate affected users. Deleting a
  group removes the corresponding boundary and deletes a Role assignment if it
  would otherwise have no boundaries.
- Added the persistent **Working in** selector, exact-boundary request header,
  capability-aware navigation/routes, read-only Settings presentation,
  direct-authoring capability gates, Managed customer support catalog, owner /
  organization filters, Solution sharing by person and Role, promotion review,
  Organization and Global Builder target pickers, and visible denial/setup /
  connectivity states.

### Reviewed Global release behavior

- Global proposal application and rollback use the shared durable Platform Job
  contract (`builder.global_release.apply` and
  `builder.global_release.rollback`) rather than a Builder-only job system.
- Released revisions snapshot the actual canonical live repository after
  manifest regeneration, rather than assuming the reviewed proposal is the
  complete post-apply state.
- Rollback preflight fails closed if the live repository moved after release.
  For reviewed operation-backed releases, generated `.bifrost/**` drift is
  handled separately while non-manifest source drift still blocks rollback.
- Reversible reviewed adapters currently cover Agent, Form, and Table create/
  update, Workflow metadata update, and Application metadata update. App
  source registration/build/deploy/publish and other lifecycle operations are
  intentionally not faked as metadata changes; unsupported adapters fail
  closed and remain source/Solution-driven.

### Provider-neutral usage-governance foundation

- Forward migration `20260826_usage_limits` adds `AIUsage.solution_id`, shared
  Platform/Organization/User/Solution limit policies, and a shared aggregate
  usage ledger. This extends the existing AI usage system; it is not a
  Builder-only accounting table.
- Per-run precedence is Solution, user, organization, then platform. Aggregate
  ceilings remain cumulative at every configured owning level.
- Portable dimensions are model requests, input/output/cache read/cache write
  tokens, canonical total tokens, runner wall duration, and sandbox compute
  duration. `total_tokens` is input plus output; provider cache figures are
  independently enforceable breakdowns and are not double-counted.
- Aggregate periods are explicit daily or monthly UTC windows. Ledger lookup is
  keyed by both scope and period, and increments use atomic PostgreSQL upserts.
- Database constraints prevent scope keys and target foreign keys from
  disagreeing. AI model-call recording populates request/token/cache usage but
  deliberately does not mislabel per-provider latency as whole-run duration.
- One shared runtime-governance adapter now enforces these limits for Chat,
  native Builder, Cloudflare Builder, and autonomous Agent runs. The
  most-specific per-run policy wins and is intersected with any stricter
  Agent/resource ceiling; aggregate policies remain cumulative at every
  owning level.
- Input, output, cache-read, and cache-write overruns discovered after a model
  response trigger the shared wind-down path and strip pending tool calls from
  that response before execution. Multi-request turns retain their true model
  request count instead of being recorded as one request.
- Authoritative runner wall duration is recorded once at terminal completion.
  Cloudflare Builder turns additionally report measured sandbox compute time;
  the Local path does not invent a sandbox measurement it cannot observe.
- The strict Cloudflare runner contract carries a typed governance snapshot,
  so remote execution reconstructs the same evaluator rather than maintaining
  separate limit behavior.
- Added typed policy-management endpoints for list, effective diagnostics,
  replace, and delete. Broad reads use `metrics.read`; mutations use
  `metrics.readwrite`; current users and admitted private-Solution viewers have
  a narrow effective-status read without receiving broad metrics access.
- Policy reads and mutations enforce the exact selected Platform or
  Organization boundary. Private Solution access uses the canonical owner,
  collaborator, Role-grant, and delegated-support gate; inaccessible private
  targets return not found before disclosing their organization. List results
  cannot leak null-organization private targets into Global.
- Private policy mutations require Solution `MANAGE`, not merely `VIEW`, in
  addition to `metrics.readwrite`. Policy upsert/delete actions are recorded in
  the Audit Log.
- Personal/private Builder and Chat work accrue to the actor's home
  organization when present; organization-target work accrues to the customer
  organization. User, Solution, and Platform ledgers are recorded alongside
  the applicable organization ledger.
- Added the admin usage-limit UI with Platform, Organization, User, and
  Solution policies, effective-policy diagnostics, percentage presentation,
  validation, replacement/deletion, and exact-boundary requests.

### Cloudflare runner isolation

- The Cloudflare coding turn now uses two Sandboxes under one private Workflow:
  a trusted `bifrost-build` runner for the shared Pydantic AI loop and a
  separate secretless workspace Sandbox for file/search/argv-command tools.
- Workspace operations cross a private `workspace.bifrost.internal` broker.
  No public Worker route, callback endpoint, DNS record, or user-configurable
  Worker name is required.
- The workspace has no model key or job capability and has network egress off
  by default. The trusted runner has restricted egress. Command execution is
  Cloudflare-only, argv-only, cwd-confined, time/output bounded, and kills the
  process group on timeout. Local execution does not advertise an unsafe shell
  fallback.
- Cloudflare app builds remain a separate locked, secretless Sandbox with only
  the scoped build capability and required callback/package-registry access.
- Workspace hydration/archive transfer is streaming, and remote usage reports
  the measured compute time of both active turn Sandboxes.
- `test_solution_build` snapshots the exact tool execution workspace and sends
  it through the canonical Solution build plane. The callback is capability,
  job, turn, message, execution, digest, and Solution fenced; terminal cleanup
  deletes the execution snapshots with the rest of the attempt artifacts.
- Runner-egress setup and workspace hydration use bounded retries for transport
  failures, 408/429 responses, and server errors. Permanent client errors fail
  immediately, and consumed archive streams are never replayed.

## Current implementation state

The broad Builder-critical route cutover is complete for the currently
classified human request paths. Tables and Forms now derive admission,
attribution override, and privileged error disclosure from
`AuthorizationContext`. Human Chat/agent switching no longer falls back to a
legacy superuser bit when authorization context is absent, native Builder
carries its resolved context into the shared loop, and isolated app runtime
recomputes canonical authorization before repository admission.
`CurrentSuperuser`, `get_current_superuser`, and `RequirePlatformAdmin` no
longer appear in human routers except two declared architectural exceptions:

- `executions.py`: workflow execution authorization and minted execution-token
  replacement are the separately deferred delegated-execution RBAC phase;
- `sdk_modules.py`: engine-token-only child transport, not a human route.

Agent-run summary administration now uses `platformjobs.read/execute` at the
explicit Platform boundary. Notifications use owner access or Platform Job
capabilities at Platform. Browser WebSockets carry the selected boundary in
the connection query, authorize privileged channels through the central
capability evaluator, and reconnect with the same subscriptions whenever
**Working in** changes. The old provider-membership bypass for file channels
has been removed.

The UI is capability-driven across navigation, Settings, direct Apps/Forms/
Tables/Workflows/Knowledge authoring, Builder, support, sharing, promotions,
roles, users, organizations, diagnostics, audit, and reports. Managed
organizations is collection/support context and never a mutation identity.
Read-capable Settings users receive a useful read-only view; write and execute
controls are disabled independently.

## Current-run verification evidence

- Consolidated human-route authorization, migration/provisioning, Knowledge,
  maintenance, metrics, MCP, Platform Job, audit, and structural matrix:
  **75 passed**.
- Sticky provider-staff Platform Operator migration regression: **1 passed**;
  runtime provisioning matrix: **5 passed**; fresh forward migrations: **2
  passed**.
- Selected backend E2E matrix for Builder domains, export/import, workers,
  audit, MCP servers, AI config, memory, and metrics: **71 passed**. The first
  run exposed tests that relied on an implicit Global target; callers now set
  `X-Bifrost-Boundary: platform` explicitly and the focused rerun passed **42**.
- Operation catalog, DTO parity, thin MCP wrapper, contract version, Builder
  tools, and CLI boundary matrix: **202 passed** on the initial run plus the
  corrected generated-inventory rerun (**4 passed**). The only failure was the
  expected count changing after six Knowledge operations were added.
- Generated artifacts are current: **171** catalog operations, **146** CLI
  leaves, **116** MCP tools, **110** native Builder operations, **672** REST
  pairs, **16** manifest fields, and **19** app-SDK bindings. OpenAPI client
  types were regenerated from the live worktree API.
- Focused client authorization, Builder, direct-authoring, sharing, promotion,
  Settings, Knowledge, and service matrix: **139 tests** initially with one
  stale silent-fallback expectation; the corrected boundary/Knowledge rerun
  passed **5/5**. Separate Settings verification passed **17**, and direct
  authoring verification passed **34**.
- Playwright with screenshot capture: Builder setup, private creation,
  Organization workspace, mobile workbench, Role assignment, Working-in
  persistence, Users, and AI Settings passed **16/16** after AI Settings was
  corrected to select Global explicitly.
- API quality: Pyright **0 errors / 0 warnings** and Ruff passed after fixing
  an import-shadowing defect and one UUID narrowing issue found by the first
  quality run.
- Client TypeScript passed. Full ESLint reports zero errors; the only remaining
  warning is the pre-existing React Hook Form compiler advisory in unchanged
  `FormRenderer.tsx`.
- Final WebSocket verification: backend authorization **4 passed**, client
  boundary/reconnect behavior **8 passed**, and file-channel E2E **3 passed**.
- Final Builder lifecycle selection: private Solutions, durable app publish,
  artifact retention, and same-origin runtime are green across **28/28**
  behaviors. The initial run exposed one stale test that expected an
  organization user to publish without `apps.deploy.execute`; the corrected
  case assigns that capability at the exact Organization boundary and reaches
  the intended active-job conflict contract.
- Final combined authorization, WebSocket, worker, operation-catalog,
  CLI/MCP/DTO parity, contract-version, and migration matrix: **211 passed**.
- Final focused client matrix: **74 passed** across boundary persistence,
  WebSocket reconnect, navigation, Settings, Knowledge, Build, Solution
  Builder, and promotion surfaces. TypeScript passed; ESLint remains at zero
  errors and the one pre-existing advisory above.
- Final API quality: Pyright **0 errors / 0 warnings** and Ruff passed.
- `git diff --check` passed.
- Builder-owned recovery inventory against immutable `1696d8693`: **87 paths**,
  **66 retained**, **21 explicitly replaced/consolidated/superseded** in
  `2026-08-20-builder-backup-inventory.md`.
- True RBAC migration rehearsal:
  `api/tests/e2e/platform/test_rbac_migration_rehearsal.py` creates a
  disposable PostgreSQL database outside `bifrost_test_template`, upgrades it
  only to `20260819_skill_file_tool`, seeds representative legacy identities
  and `user_roles`/`roles.scopes`/`roles.permissions` rows, upgrades to head,
  asserts the durable Role assignment/boundary/capability/public-bit outcome,
  runs Alembic's supported no-op `upgrade head` idempotence path, and drops the
  disposable database. The focused run passed **1/1**.
- Final RBAC acceptance selection after explicit Platform Builder boundary
  corrections: **85 passed**; DTO parity separately passed **62**; focused
  client boundary/default-Role matrix passed **19**; API quality and generated
  OpenAPI client types passed.
- Shared neutral workspace and reviewed Global release unit selection passed
  **63**. Full scheduler-backed Global release E2E passed **12**, including
  explicit stale-baseline refresh, application metadata operations, durable
  apply/rollback, source drift rejection, manifest regeneration, and rollback
  compensation.
- Public web app SDK contract verification passed **2** tests. This specifically
  protects user-facing compatibility while the internal authorization source
  moves to Roles/capabilities.
- Final Tables/Forms request-facing cutover passed **16** focused unit tests,
  **4** structural route tests, and **12** E2E behaviors. Final Chat routing and
  isolated app-runtime cutover passed **15** focused unit/structural tests and
  **9** E2E/router behaviors. API quality remained clean after both slices.
- Public compatibility regression after the final cutover passed **96** backend
  tests covering contract/DTO tripwires, Python auth-context claims, Builder app
  session claims, X-Bifrost-App table fencing, and Solution app runtime. The
  focused web app SDK transport/hooks selection passed **115** tests across
  provider, Tables, Files, and Workflows.
- Usage-governance foundation verification passed **12** unit/DB-backed tests,
  Python compilation, focused Ruff, and full API quality with zero Pyright or
  Ruff findings.
- Shared runtime enforcement verification passed **39** focused tests across
  usage evaluation, Chat/Builder coordination, wind-down behavior, autonomous
  runs, and the Cloudflare runner contract. The contract-version tripwire
  passed **2** tests, and full API quality remained clean.
- Usage-policy management passed **7** endpoint scenarios, including exact
  boundary admission, self reads, private target non-disclosure, collaborator
  mutation denial, and Audit Log registration. Policy/runtime units passed
  **23**, the contract tripwire passed **2**, and API quality remained clean.
- The usage-limit admin browser flow passed its setup and acceptance spec with
  screenshots (**2/2**), and the focused usage/Builder/Role client selection
  passed **39** tests. A delegated rerun encountered a clean API-container exit
  during stale test-stack startup rather than an assertion failure; after the
  stack was rebuilt, the same screenshot-enabled setup and acceptance spec
  passed again (**2/2**).
- Cloudflare isolation verification passed **15** focused Python tests and the
  runner JavaScript tests. Follow-up command-provider gating passed **2**, and
  the shared workspace runtime passed **5**. API quality remained clean.
- A live Local coding turn created and built a minimal v2 app through the
  existing execution Worker and shared Pydantic AI harness: **19 tool calls**,
  **12 model requests**, an immutable revision, a successful Solution deploy,
  and a successful canonical Vite application build.
- A second prompt resumed the same Builder session/conversation, reused its
  history and worktree, made the requested source-only change, created a new
  immutable revision, and deployed successfully (**2 tool calls**, **4 model
  requests**). These runs did not cross the two-million-token compaction
  threshold; compaction is covered by shared-harness tests, not claimed as a
  live event here.
- Live acceptance exposed and fixed one private-preview defect: private Builder
  deployments now force `runtime_mode=isolated`, while shared deployments keep
  their existing/trusted runtime and promotion applies the reviewed admin
  choice. Focused deploy regressions passed **2/2**. The corrected one-time
  preview launch returned the app HTML and compiled JavaScript with HTTP 200,
  and the bundle contained the requested UI text.
- The two live Local turns appeared in the shared usage report with **474,235
  input tokens**, **3,294 output tokens**, and **$0.10993593** provider-reported
  cost, including both conversation and Organization rollups. OpenRouter
  emitted a cache-bust warning because cache-read usage collapsed to zero; this
  is an optimization signal, not an accounting or execution failure.
- The configured OpenRouter model was capability-tested live for image, PDF,
  and tool calling. The probe initially under-allocated output tokens; its
  bounded allowance is now 128 tokens and has regression coverage. Live
  re-verification passed all three capabilities.
- The downloadable standalone CLI exposed a packaging defect: its runtime
  imports `packaging` without declaring it in the actual CLI distribution.
  `api/bifrost/pyproject.toml` now declares the dependency and a regression test
  checks the distribution manifest rather than the legacy API manifest.
- The first CI-equivalent managed-runner probe exposed separate image packaging
  drift: the shared Pydantic runtime's contract import path loads SQLAlchemy ORM
  declarations and email schemas, but the small runner lock did not include
  their import-time dependencies. The runner manifest/hashed lock now declare
  SQLAlchemy asyncio and email-validator. The rebuilt version-matched image
  returns `{ready: true, schema_version: 1, harness: pydantic-ai}` from the exact
  `runner.py --probe` command used by CI.
- Builder image publication is part of the normal CI artifact chain rather than
  a manual side channel. Merge candidates build and probe a SHA-addressed
  `ghcr.io/gobifrost/bifrost-build` image; main promotes that tested digest to
  version/dev/SHA tags; version releases rebuild, probe, sign, and attest it.
  The runner and CI workflow contract selection passed **15/15**.
- GitHub package access was approved and the final branch-matching candidate was
  published as
  `ghcr.io/gobifrost/bifrost-build:cloudflare-acceptance-20260821` at digest
  `sha256:c5f9dddeebdcf988432f95d33f7d3ccecefe6f7f8798016eff939813281378d5`.
  Its exact `runner.py --probe` returned
  `{ready: true, schema_version: 1, harness: pydantic-ai}`. Docker was logged out
  after publication.
- The exact image used for the full live Cloudflare coding/build turn was also
  retained as
  `ghcr.io/gobifrost/bifrost-build:cloudflare-acceptance-20260821-tool-build` at
  digest
  `sha256:0eee744aa8bf237fb77dcb98a0f602bb8d22cd956ac04f417bb0d0d18221af6c`.
- Nested worktree `node_modules` directories are excluded from the shared Docker
  context. The rebuilt runner image was verified not to contain Cloudflare's
  local development dependency tree.
- The final focused permission-scenario matrix passed **46/46** across ordinary
  Builder denial/admission, private ownership and non-disclosure, explicitly
  shared work, Platform Operator support-without-build, Platform Admin focused
  versus All views, Organization/Global target discovery, exact-boundary
  mutation, promotion, and Role-assignment boundary replacement/deletion.
- The screenshot-enabled Builder browser flow passed **5/5**: disconnected
  setup guidance, private app creation and animated workbench transition,
  Organization workspace behavior, and restored mobile workbench usability.
- Final API quality after the live-acceptance fixes: Pyright **0 errors / 0
  warnings** and Ruff passed.
- Final remote workspace/build callback verification passed **30/30** after the
  bounded setup retry was added; the preceding build-input, SDK, and runner
  bridge selection passed **33/33**. The initial callback/artifact/runner
  selection passed **15/15**.
- Live Cloudflare turn `96edfa69-2a0a-443c-9b21-c2cab1eb1fc7` used the shared
  Pydantic AI harness and two-Sandbox envelope, applied a source patch, invoked
  canonical `test_solution_build`, created immutable revision
  `fa6ae832-d300-40b5-81bd-14dd98732475`, and completed child build
  `d33d7808-443f-4452-b7af-6732d31f3a4d` with two tool calls, two model calls,
  and no tool errors.
- Live acceptance then found that API and Scheduler containers stamped the
  vendored internal SDK package with different environment versions. The stable
  internal package stamp now keeps identical sources hash-identical while the
  embedded contract/fingerprint retains compatibility checks. Turn
  `d27ef29c-6e6d-40e3-b583-3aedfe8615b7` created revision
  `c71053cd-0437-49c3-bc64-3d248145260c`; child build
  `27679915-9461-42e7-bb17-995470f3c50f` succeeded, and deploy
  `8365dd8e-cebb-470e-8008-088deb9714d0` reused that exact build without a
  second compile.
- One turn immediately after a Cloudflare rollout failed during initial
  workspace hydration and accepted no revision. The bounded transport/setup
  retry described above addresses that observed transient. The final retry
  image was published and probed, while the preceding exact runner bridge was
  the version exercised by the full live coding turn.
- `git diff --check` passed with the current uncommitted tree.

## Remaining execution before source-control delivery

Implementation, focused verification, Local acceptance, Cloudflare
provisioning, image publication, remote coding, canonical app build, automatic
deploy, and exact-build reuse are complete. The remaining gates are:

1. **Jack's operator/customer visual review.** Review the live Builder setup and
   workbench experience, including connected diagnostics, activity/progress,
   cancellation/failure presentation, preview restoration, and final visual
   polish. Automated screenshot flows cover the primary Local states; this is
   the remaining human product-acceptance gate.
2. **Broader release-suite decision.** Full backend, full Vitest, and full
   Playwright suites were not run by default. The focused matrices above cover
   the changed Builder, RBAC, runner, build/deploy, SDK/MCP, migration, client,
   and browser boundaries. Decide whether to run the broader release suites
   before merge.
3. **Merge approval.** Commit, push, and PR delivery were approved on
   2026-08-21. Do not queue or merge the PR without separate explicit approval.

### RBAC upgrade rehearsal coverage

The dedicated rehearsal now exercises the real Alembic path from the legacy
cut-point into head against a disposable PostgreSQL database. It intentionally
does not mutate `bifrost_test_template` or the normal cloned test database.

Seeded legacy state:

- active non-system `users.is_superuser=true` platform admin with no explicit
  `user_roles` grant at the cut-point;
- system superuser, which must not receive a human Platform Admin assignment;
- provider-org non-admin user with no explicit Role assignment;
- ordinary customer user;
- legacy custom Role with `roles.scopes=["agents.write", "solutions.build"]`
  and `roles.permissions={"can_promote_agent": true, "custom_flag": "kept"}`;
- legacy `user_roles` row for that custom Role, which has no boundary rows
  until the RBAC migration creates `role_assignment_boundaries`.

Asserted durable outcome:

- the active non-system legacy superuser receives exactly one Platform Admin
  Role assignment at Platform, and `users.is_superuser` remains true;
- the system superuser retains the public legacy bit but does not receive a
  human Platform Admin Role assignment;
- provider staff receives Organization Member at home plus sticky Platform
  Operator at Managed organizations, and Platform Operator has no
  `builder.execute`;
- ordinary customer receives Organization Member at the home Organization;
- the legacy custom Role translates to canonical capabilities while preserving
  arbitrary deprecated `permissions` metadata;
- default Builder does not inherit admin discovery capabilities;
- Alembic's supported no-op `upgrade head` path leaves the assignment,
  boundary, capability, and public-bit snapshot unchanged.

This is DB-level upgrade proof. The API was not booted against the disposable
database because the current test harness runs a single API container against
the normal cloned database. Public endpoint and SDK/DTO compatibility remain
covered by the separate head-schema E2E and contract-version tests.

## Explicitly deferred work, not a hidden fallback

- Workflow execution-token replacement remains later work. The current program
  prepared typed authorization and delegated-grant vocabulary but does not
  change workflow runtime authority.
- JWT/runtime `is_superuser` and provider claims remain compatibility
  materialization for unconverted execution, engine, embed, and transport
  contracts. Converted human routes do not use their unbounded union as an
  authorization source.
- Full backend, full Vitest, and full Playwright suites were not run; the
  selected matrices above cover the changed behavior and its critical shared
  boundaries. Broader release suites remain an explicit pre-merge decision.

## Live debug instance

The isolated worktree debug stack was **up** at this update and was used for the
live Local and Cloudflare acceptance above. It remains configured to the
`cloudflare-acceptance-20260821-tool-build` image used for the successful remote
turn; the later retry-hardened candidate is published and probed but was not
rolled into another live turn. Obtain the current URL and generated credential
with `./debug.sh status`; URLs and credentials are intentionally not persisted
in this document. Source changes hot-reload.
