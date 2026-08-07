# Private Solution Builder — Implementation Status

**Updated:** 2026-07-31
**Branch:** `code-builder` (worktree only; `main` untouched)
**Worktree:** `.worktrees/ai-solution-builder-spec`
**Design:** `2026-07-25-private-solution-builder-design.md`

## Outcome

The original private Solution Builder implementation is present on this branch,
but it is not yet the production contract. Product review identified required
authorization, collaboration, runtime-routing, and operational changes that
must land before release. The branch was rebased onto `origin/main` on
2026-07-31; the new canonical platform-job system and four-tool Agent MCP
gateway now replace several assumptions in the first implementation.

A user with `solutions.build` can open `/build`, describe an app, create a
private workspace and builder session, run the bundle-backed builder Agent,
produce immutable source revisions, build source in an isolated fixed-toolchain
runner, durably deploy the successful revision, and open a sealed preview on the
separate app host. An administrator can review the exact deployed revision and
promote it to company or global scope.

The previous handoff in this file said the builder could only author files.
That is no longer true.

## Implemented surfaces

| Area | State |
|---|---|
| Private Solution ownership, visibility, slug identity, and central parent gate | complete |
| Builder project, revision, session, turn, build-job, and promotion-pinning persistence | complete |
| Immutable content-addressed revision storage, download, and history-preserving undo | complete |
| AI-readiness-aware `/build`: hidden from ordinary users until configured, administrator setup guidance, centered prompt, staged launch transition, “your builds,” and existing-app edit path | complete |
| Familiar app-builder workbench with Agent chat beside Preview, Code, and Changes; resizable desktop split and explicit mobile pane switching | complete |
| Responsive Preview with desktop, tablet, and mobile presets; route entry, reload, stale/source status, and sealed app launch | complete |
| Searchable real revision file tree and source viewer in Code; revision history and real unified diffs in Changes | complete |
| Stage-aware status rail and actions for source creation, preview deployment, stale preview updates, and promotion readiness | complete |
| `builder_model` API setting, fallback behavior, and AI settings UI | complete |
| Bundle-backed builder Agent using the platform `AgentExecutor` | complete |
| Visible `bifrost-build` identity and provenance in the workbench, including explanation that it guides each generated change | complete |
| Portable Agent Skill UI with validated `.skill`/`.zip` drag-and-drop, actual SKILL.md instructions, read-only Solution management, bundle file explorer/preview, export, and runtime `read_skill_asset` labeling | complete |
| Shared TipTap Markdown editor/viewer restored for inline Agent instructions, with Edit/Preview switching and rendered/source views for bundle Markdown | complete |
| Canonical `bifrost-build` skill copied into each scaffold at the Solution-root-relative `skills/bifrost-build`, plus deterministic Agent Skill projection/download APIs | complete |
| Agent-owned `_agent_skills/{agent_id}/` storage for direct uploads; uploaded bundles never grant or mutate `_repo/` | complete |
| Full-width Agent access selector with explicit Private support and menu-only descriptions | complete |
| Eight hidden builder workspace tools plus traversal-safe `read_skill_asset` | complete |
| Portable `Agent.bundle_path` across ORM, REST, MCP, CLI, manifests, capture/export/git sync, and Solution deploy | complete |
| External builder coordinator and credential-free fixed-toolchain runner protocol | implemented, coordinator superseded by platform jobs |
| Source-only app build, staged artifact validation, and real deploy materialization | complete |
| Durable deploy input staging, integrity hashes, encrypted options, leases, idempotency, recovery, and worker consumer | complete |
| Separate app-host process and exact Solution/app/org/JTI actor-token seal | implemented, routing contract must move behind transparent `/apps/*` URLs |
| Zero-DNS preview routing | redesign required; no public second port, DNS record, or alternate app origin |
| Actor-only table, file, execution, artifact, launch, renewal, and revocation routes | complete |
| Actor WebSocket subscriptions for exact table/file/execution scope | complete |
| Private deploy suppression for roles, connections, events, schedules, autonomous agents, and generated Python activation | complete |
| Admin Promotion review queue with pinned revision, exact hash, build/deploy result, entity/change evidence, blockers, scope, role/connection approvals, and guarded confirmation | complete |
| Docker Compose and Kubernetes services for builder, runner, and app host | implemented, dedicated builder coordinator must be removed |

The bespoke `InternalLoopRuntime` / builder model gateway were removed. Builder
turns now use the same Agent execution path as the rest of the platform.

## Security properties now enforced

- Private Solutions and their children are invisible outside the owner surface;
  ordinary platform-admin catalog access does not bypass that rule.
- The app host is a separate process and does not mount the normal user API.
- App tokens bind the exact Solution, app, organization, and actor-session JTI.
- Actor HTTP and WebSocket routes reject normal user tokens and sibling Solution
  access.
- The runner receives only a staged source artifact and a fixed protocol. It has
  no platform, database, object-store, or integration credentials.
- Build output is accepted only after path, size, hash, manifest, and staged
  artifact validation.
- Model-authored Python, events, schedules, and autonomous agents remain inert
  through private preview and promotion replay. Human review remains the
  activation boundary.
- Promotion replays the exact pinned green revision under a per-Solution write
  lock; it does not promote mutable “latest” source.
- Scope promotion re-homes every Solution-owned ORM table carrying
  `solution_id` and `organization_id`, with an introspection test preventing
  omissions as the schema evolves.
- A bundled Agent has one instruction source: its real `SKILL.md`. Upload and
  Solution deploy materialize that exact markdown for runtime compatibility;
  the UI and REST update path prevent a second editable prompt from drifting.
- Direct Skill archives are zip-slip/symlink/duplicate/size checked, require
  portable frontmatter, and may contain only `SKILL.md`, `assets/`,
  `references/`, and `scripts/`.

## Verification performed

The results below were recorded before the 2026-07-31 rebase. They are useful
historical evidence, not a post-rebase release claim. The complete required
verification suite must run again after the architecture changes below land.

Green gates:

- Dockerized API quality: pyright `0 errors`; ruff `All checks passed`.
- OpenAPI client contract regenerated from the live isolated API.
- Client typecheck: `npm run tsc` passed.
- Client lint: `npm run lint` passed with one existing
  `FormRenderer.tsx` React Hook Form compiler warning and no errors.
- Final full backend suite: 7,395 tests passed and 57 skipped.
- Final full client Vitest: 241 files and 1,787 tests passed.
- Final full Playwright suite: 110 tests passed and 2 intentionally skipped.
- Focused final builder/skill/promotion UI Vitest: 35/35 passed.
- Builder-model settings Vitest: 1/1 passed.
- Final builder backend E2E regression batch: 64/64 passed, covering the real
  build plane, private lifecycle and promotion, isolated app host and actor
  WebSockets, durable deploy, execution scope, and connected-bundle validation.
- Final Agent Skill/revision/deploy/DTO/contract unit batch: 92/92 passed.
- The earlier wider builder/bundle/deploy/manifest/access batch passed all 545
  logical tests after updating the promotion-FK metadata expectation and the
  additive contract fingerprint.
- Playwright builder happy path: setup plus desktop and mobile scenarios passed
  (4/4): create from `/build`, create the real private Solution/session, hand off
  the prompt, verify Skill provenance, inspect real Code and Changes, return to
  Preview, exercise the Agent/Preview/Code/Changes mobile workspace, persist the
  selected mobile pane across reload, then clean up.
- Builder continuity increment: 51/51 focused frontend tests passed for
  transcript pagination, selected-session/layout/route/device restoration, and
  the two-phase secure-session/iframe loading transition. The chat pagination
  endpoint passed its focused 5/5 E2E class, including newest-page and
  earlier-history ordering.
- Netbird debug overlay and shell validation passed; the generated-app origin is
  now the same Netbird hostname on port 8100. Port-mode debug continues to
  allocate distinct localhost control-plane and app-host ports automatically.
- Production Compose, test Compose, and Kubernetes kustomize rendering passed.
- Latest Agent Skill/AI-readiness increment: backend pyright and ruff are green;
  20/20 focused storage/import/runtime/deploy tests, 1/1 real upload/browse/
  detach API E2E, 2/2 final capability-envelope API E2E checks, and 61/61
  focused frontend tests passed. The focused frontend
  batch covers the Build setup/transition, hidden entry point, access selector,
  Skill uploader/browser, settings, overview, and service contracts.
- TipTap Agent instruction restoration: 22/22 focused component tests and
  TypeScript are green; lint has no errors beyond the previously documented
  `FormRenderer.tsx` warning. The real Agent Settings Edit/Preview browser flow
  passes with semantic keyboard-accessible controls.

UX verification:

- The project-local Impeccable review improved from 20/40 to 39/40 with no P0
  or P1 findings.
- Independent source and rendered-layout passes at 1440×900 and 390×844 found
  no unusable mobile panes. A final manual pass caught one flex-shrink issue in
  the Agent Skill overview card; it was fixed and recaptured without clipping
  or overlap.
- The remaining point is an expert-efficiency refinement: keyboard
  next/previous-change controls and file-status filters for very large reviews,
  not missing app-builder or Agent Skill UI capability.

The final monolithic backend run also verifies the regressions found by earlier
interrupted runs: agent mutation responses, coordinator/runner event-loop
isolation, execution-scope fixtures, connected invalid-bundle rejection,
organization-scoping classification, and summary-backfill worker timing all
pass in the complete suite.

## Product follow-up plan

These are follow-on product decisions discovered during implementation. They
are intentionally tracked separately from the completed original design so
new questions do not disappear into chat history.

### Authorization foundation decision

The earlier #473 RBAC work was recovered from
`.worktrees/473-role-authorization-scopes`. Its durable decisions now govern
Builder authorization:

- scopes use the lowercase Graph-inspired
  `<resource>[.<subresource>].<action>[.all]` convention;
- roles are validated bundles of first-class scopes;
- effective scopes are minted into access tokens;
- `principal.has_scope()` is the only action-capability API;
- organization reach, resource audience, and data policy remain independent
  gates;
- human and non-human tokens use one code-owned scope catalog;
- `actor_type` selects a credential contract but grants no permission by
  itself.

`solutions.build` is the first custom-role-assignable Builder action
scope. It is registered in the code-owned catalog, stored in first-class
`Role.scopes`, included in effective human-token scopes, and enforced by
`principal.has_scope("solutions.build")`. The earlier free-form
`Role.permissions` lookup has been removed. Deterministic built-in Platform
Admin and Platform Operator roles preserve the existing superuser and provider
operator semantics while making their grants visible and immutable in the
roles UI.

The `solution_app` credential now uses the same catalog. Its attenuated
`scopes` claim is validated against the fixed app-runtime subset, while its
separate API surface and immutable Solution/app/org/JTI bindings continue to
constrain where those actions apply. Runtime SDK routes require both their
cataloged action scope and the existing exact resource/data gates.

`solutions.build` is a human authorization to use Builder on projects the
principal can reach. It is not the permission used by a process that happens to
execute a build. Once Builder coordination uses canonical platform jobs, the
role catalog should not expose `solutions.jobs.execute`,
`solutions.builds.execute`, or a second synonym. The trusted scheduler executes
the registered platform-job handler. The isolated runner receives only a
single-job, lease-bound capability for its fixed protocol and exact artifact
hashes; that capability is not assignable to a human role and cannot be used as
a general SDK/API token.

Platform Admin migration is a compatibility backfill from legacy
`is_superuser`, followed by a staged period in which the old and new checks run
in parallel until every route family has moved to `principal.has_scope()`.
Platform Operator is different: provider-organization membership is only
eligibility and must not silently create a durable Operator grant. During the
transition the legacy provider check remains available in shadow/rollout mode,
the admin receives a readiness report of affected users, and an administrator
explicitly assigns Platform Operator before legacy enforcement is removed.

Platform Operator initially grants `organization.impersonation`. Normal
product access will move into a granular Organization Member role, so a support
operator is the composition `Organization Member + Platform Operator`, not a
new mega-scope. Builder access remains independent. Builder setup may offer to
create a mutable **Platform Builder** role containing `solutions.build`, but an
administrator owns its assignments and may create a differently named role
instead. Platform Admin remains the immutable built-in wildcard role; Platform
Builder is not silently bundled into Operator.

Role grants and removals become effective when the user's access token is
minted or refreshed in this foundation release. The planned token-version and
revocation work will make privileged removal immediate without changing the
scope names, role data, principals, or route dependencies introduced here.

Broader migration remains incremental. Initially, existing route families keep
their current authorization while Builder uses the new scope foundation. Each
family later gains a cataloged action dependency, shadow parity, enforcement,
and removal of legacy `is_superuser`, provider-membership, role-name, or
free-form-permission checks. A route-classification test must eventually fail
for every new protected endpoint that declares neither a human action scope nor
an explicit non-human actor contract.

### A. Platform jobs, preview, and publication contract

**Current behavior:** source generation starts from an immutable revision in a
temporary Agent workspace. App compilation runs in the credential-free fixed
toolchain runner, verified artifacts are finalized under the durable app
prefix, and private preview is served by the separate-origin `app-host`
process. Preview is therefore isolated from the control-plane application, but
it is not a separate staging environment: private and promoted Solutions use
the same Bifrost database and object store.

There is no per-build or per-app runtime container. The first implementation
currently adds three shared components:

- two shared `bifrost-app-host` pods (`uvicorn src.app_host:app`) for all
  generated apps; each requests 100m CPU / 256 MiB and is limited to 1 CPU /
  1 GiB;
- one shared builder coordinator pod requesting 50m CPU / 128 MiB and limited
  to 500m CPU / 512 MiB; and
- one shared, network-isolated builder-runner pod requesting 250m CPU / 256 MiB
  and limited to 2 CPU / 1 GiB, with a 1 GiB in-memory scratch volume.

The shipped Kubernetes configuration has no HPA for these components and
allows one build at a time. The app host streams already-built static
artifacts from object storage and exposes only its launch, artifact, bootstrap,
and Solution-runtime routes. The separate origin is primarily a browser
security boundary: generated JavaScript cannot read the Bifrost control
plane's DOM, local storage, or cookies even though both services use the same
platform data plane.

Builder continuity is durable. Sessions, conversations/messages, turns, and
current/deployed revision pointers live in PostgreSQL; immutable source and
built artifacts live in object storage. Reopening a builder restores its
selected conversation, active tab/mobile pane, Agent split, preview route, and
preview device. A navigation-provided session remains authoritative when the
user follows an explicit session link. The transcript opens at the newest 100
messages and exposes **Load earlier messages** for complete older history; the
Agent continues to load the complete persisted conversation for model context.

When a deployed revision exists, the client automatically requests a fresh
one-time preview launch. It does not rebuild the app just to resume. The preview
shows a two-phase restoration transition while it creates the secure app
session and then waits for the saved app document to load. The renewable
app-host cookie lives in Redis for eight hours, but an expired cookie is
normally replaced by that fresh launch flow. A queued/running turn is
rediscovered and polled, while a missing app origin, missing first deploy, or
failed launch still produces the corresponding preview state.

The second-public-port/app-origin design is superseded. It is not compatible
with common public proxies and creates setup work that should not be necessary.
The public application URL remains `/apps/{slug}` for every application. A
server-authoritative release field selects `isolated` or `trusted` runtime
mode; there is no user-controlled query flag. Existing V1/V2 applications stay
trusted and keep their current SDK behavior. Builder previews are always
isolated. The `/apps/*` router renders the trusted runtime directly or an
isolated shell whose sandboxed iframe is backed internally by `app-host`.
`app-host` is not exposed as a separately configured public origin, and the
iframe omits same-origin authority so generated JavaScript cannot read control
plane DOM, storage, or cookies.

The app origin currently uses one host-wide cookie name and path; opening
another generated app replaces the first app's browser session, so multiple app
previews cannot remain independently authenticated. Promotion exposes another
authorization gap: the current app-host validator accepts only a still-private
owner launch, so the promoted shared-app serving contract must be completed
before publication is considered end-to-end.

**Decisions / work remaining:**

- The canonical platform-job kernel from PR #533 is already merged. Before
  Builder adds sustained load, finish issue #532's scheduler HA/capacity phase:
  one elected schedule-trigger leader, platform-job claiming on every scheduler
  replica, one execution slot per replica initially, and measured resource
  diagnostics. Builder then registers ordinary durable job types (initially
  `solution.builder.turn`, `solution.build`, and where useful
  `solution.deploy`) instead of keeping a parallel coordinator, poller, and
  recovery system.
- Remove the dedicated trusted builder coordinator container. The existing
  scheduler owns trusted Builder orchestration, AI/provider access, database
  state, leases, progress, retries, and recovery through platform jobs.
- Keep the credential-free, network-isolated fixed-toolchain runner as a
  separate execution boundary. Model-authored source and package/toolchain
  processing must not inherit scheduler/API secrets. The runner consumes only
  a staged artifact and exact one-job capability.
- Keep `app-host` as a separate narrow runtime service, routed internally by
  the existing client/API proxy under `/apps/*`. It is a long-lived serving
  boundary, not a scheduler job.
- Make promotion preserve `isolated` runtime by default. An administrator may
  explicitly promote a reviewed revision to `trusted` runtime with a clear
  warning; existing V1/V2 apps remain trusted without migration or SDK change.
- Add shared-user app-host session authorization and post-promotion
  release/iteration behavior while retaining the transparent `/apps/{slug}`
  address.
- Scope app-host session cookies so multiple generated apps can stay open
  concurrently.
- Add a blocking administrator readiness flow. It configures the AI provider
  and Builder model, then reports live health for the scheduler/platform-job
  system, isolated runner, app host, artifact storage, and an end-to-end launch
  probe. Readiness is persisted; ordinary users do not see Builder until it is
  enabled. There is no DNS, public-port, or app-origin field in the normal
  setup flow.
- Add concurrency/resource guidance and load tests for the existing scheduler,
  shared app host, and isolated runner. Scheduler capacity and runner replicas
  are the normal scale controls; the UI should explain detected saturation.
- Give `app-host` an explicit NetworkPolicy and least-privilege configuration
  and secret projection. It currently imports the whole Bifrost ConfigMap and
  Secret even though its ASGI router is intentionally narrow.
- Bring local/Compose runner networking to parity with Kubernetes' explicit
  egress deny. The runner is already offline and credential-free, but Compose
  does not enforce the Kubernetes network policy boundary.

### B. Build collaboration and administrator support

**Current behavior — product mismatch:** private builder access is owner-only,
including against a platform administrator or another provider-organization
user. Sessions are also listed only for their creator. Administrators can
review requested promotion metadata, but cannot open another user's builder
conversation, source, preview, diff, or logs. That implements the original
private-draft invariant, but it is not the right default for an MSP product
where provider staff discover, review, hand off, collaborate on, and publish
work for customers.

**Revised product contract:**

- The long-lived collaborative builder project is distinct from deployed
  Solution releases/installs. Publishing pins a revision and creates or updates
  a release for one or more targets; it must not convert the source project
  itself into one shared install and make the workbench disappear.
- Ownership represents responsibility and attribution, not invisibility.
  Provider-team builds should be discoverable to provider staff by default.
- The build catalog defaults to **My work** (owned projects plus projects where
  the viewer is an explicit participant). Privileged users receive an **All
  builds** tab with server-side owner, participant, organization/customer,
  activity, build status, reviewer, and release-state filters. **Needs review**
  is a focused queue/filter. This keeps everyday navigation uncluttered while
  allowing administrators and scoped support staff to find any build they are
  authorized to manage.
- Provider staff may discover and preview work across managed customer scopes.
  Customer users may only see builds for their own organization when their
  role permits it. No customer user may discover another customer's work.
- Explicitly invited customer reviewers may preview and comment on a pinned
  review revision without receiving provider-wide discovery, Agent edit, or
  publish authority.
- A project view should expose the current preview, source tree, diffs,
  revisions, build/deploy logs, conversations, participants, review comments,
  and activity history according to one central project authorization result.
- Viewing/reviewing, editing/running the Agent, approving, and publishing are
  separate capabilities. Provider administrators receive ordinary
  policy-backed access for their job; this should not be modeled as exceptional
  owner impersonation.
- Multiple people may open the same project and continue it. Builder sessions
  remain useful as conversation branches, but participants need a shared
  project activity view and access to the relevant session history. The
  project revision graph remains the canonical source state.
- The existing single-writer Solution lock is a safe first concurrency rule.
  The UI must show presence and an active edit/build, offer handoff/retry, and
  explain conflicts instead of returning an unexplained 409. True simultaneous
  source co-editing can be a later capability.
- Optional genuinely personal/sensitive drafts can remain owner-private.
  Time-boxed, reason-required, audited break-glass access belongs only to that
  exceptional visibility tier, with visible owner notification.
- Promotion/publishing must record requester, reviewers, approver, pinned
  revision, target customer(s)/scope, and resulting release/install(s). Review
  should happen against the exact preview and revision that will be published.
- Every preview launch binds to the actual viewer and evaluates that viewer's
  project permissions. A collaborator must never run a preview under the
  owner's identity.

**Implementation work remaining:**

- Separate collaborative source-project identity/lifecycle from deployable
  Solution install identity/lifecycle, including one source revision publishing
  to multiple customer targets.
- Replace the binary `private`/`shared` gate with a project audience and
  capability model that understands provider staff, target customer scope,
  explicit participants, optional personal drafts, and publication state.
- Remove owner-only filters from build/session discovery in favor of that
  central authorization model, and update app-host launch/runtime validation to
  use the same decision.
- Replace the ordinary chat endpoint's conversation-owner check on builder
  transcripts with the same project authorization decision; widening only the
  builder session list would still leave collaborators unable to read it.
- Add team/customer discovery, presence, participants, comments/review,
  handoff, notifications, and an auditable approval/publish workflow.
- If one hosted Bifrost deployment can contain more than one MSP/provider,
  introduce an explicit provider-to-managed-customer tenancy relationship
  before provider-team discovery is enabled. `Organization.is_provider` alone
  is not a sufficient boundary for multiple independent providers.
- Add cross-tenant and cross-customer authorization tests before enabling the
  collaborative surfaces.

### C. Administrator global AI workspace

**Current behavior:** platform administrators can already edit the live global
`_repo` through the browser Shell/code editor and its source-control surface.
The AI Builder only targets owner-private Solutions and cannot target `_repo`.

**Decisions / work remaining:**

- Add an explicit Admin Global Workspace target that produces an immutable
  proposal/revision rather than writing directly into live `_repo`.
- Require a full diff, validation/tests, explicit apply/commit, and a
  recoverable rollback path before global changes become active.
- Treat `.bifrost` manifests as read-only/export evidence in the workspace
  editor; entity mutation should continue through the platform APIs, CLI, or
  Solution lifecycle rather than raw manifest editing.
- Clarify the current “Shell” label as Global Workspace or Code Editor so its
  scope and consequences are apparent.

## Operational state

- The branch was checkpointed and rebased onto `origin/main` at `de93eb6ae`.
  Backup branch: `codex/code-builder-pre-rebase-20260731`.
- Conflict resolution produced a clean checkpoint; this status document was
  then updated with the post-rebase decisions. The test stack is currently
  down; post-rebase verification has not yet been run.
- The current branch still contains `BIFROST_APP_ORIGIN`, second-port routing,
  a dedicated builder coordinator, and their deployment manifests. They are
  implementation debt to remove, not the target production setup.
- The canonical skill source remains `.claude/skills/bifrost-build`; scaffolded
  private Solutions receive it at the workspace-root-relative
  `skills/bifrost-build`, while `.bifrost/agents.yaml` only stores that portable
  relative pointer.

## Remaining handoff

The original implementation is a substantial foundation, but the product is
not complete until the follow-up plan is implemented and the full verification
sequence passes. The critical path is:

1. finish issue #532's scheduler HA/capacity phase on top of the already-merged
   platform-job kernel;
2. rebase Builder again and replace its trusted coordinator with platform-job
   handlers while preserving the isolated runner;
3. integrate actual bundled `SKILL.md` instructions and asset context into the
   rebased four-tool MCP Agent gateway;
4. complete the staged Platform Admin migration and explicit Platform Operator
   assignment/readiness flow;
5. implement My work / All builds / Needs review, central project
   authorization, participants, and privileged support management;
6. add the admin Global Workspace proposal/diff/test/apply/rollback flow for
   `_repo`;
7. replace public app-origin configuration with transparent `/apps/*`
   trusted/isolated routing and finish the blocking admin readiness wizard; and
8. run API quality, generated contracts, frontend checks, full backend/client
   tests, Playwright, and production manifest rendering.
