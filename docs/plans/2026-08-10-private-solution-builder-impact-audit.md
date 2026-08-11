# Private Solution Builder impact audit

**Date:** 2026-08-10  
**Branch:** `codex/code-builder-recovery-20260807`  
**Recovery checkpoint:** `fa0f0efc9`  
**Scope:** UX, regression/compatibility, product completeness/parity, and test coverage

## TL;DR

The audit found one multi-tenant release blocker and six P2 product/test gaps.
All seven are now remediated in the recovery working tree and have focused
automated evidence. Current-main integration, the final exact backup inventory,
and the complete post-integration matrix are now closed. Customer acceptance
and explicit push/merge approval remain.

| Rank | Finding | Disposition |
| --- | --- | --- |
| P1 | Publishing to another customer loaded role assignees from the source customer, and the server accepted those IDs without target-scope validation. | **Resolved:** the picker follows the target organization; server validation rejects inactive, foreign-organization, or unknown users and invalid roles; cross-org E2E added. |
| P2 | The support-wide build catalog fetched and rendered every matching private Solution. | **Resolved:** server-side total/limit/offset paging with 50-row pages and familiar Previous/Next controls. |
| P2 | Several list/detail query failures were rendered as legitimate empty states. | **Resolved:** retryable query-specific error states gate actions whose source state is unavailable. |
| P2 | Runner provisioning progress could not be restored after an admin reloaded setup. | **Resolved:** setup returns the active provisioning PlatformJob and the UI rehydrates live progress. |
| P2 | Builder setup received actionable readiness blockers but discarded their message/action details. | **Resolved:** server-authored blocker messages and actions are rendered in setup. |
| P2 | The Builder enable switch remained visually changed when saving failed. | **Resolved:** failed saves restore persisted state and retain the server error. |
| P2 test gate | Critical turn-finalization compensation and checkpoint retry branches had only partial direct coverage. | **Resolved:** focused tests cover fenced completion, changed-revision enqueue failure, staged/accepted harness cleanup, and partial-promotion compensation. |
| P3 / post-release | Aggregate user/org quotas, Cloudflare invoice ingestion, favorites, review comments, presence, and simultaneous co-editing remain deferred. | Keep explicit in release notes; do not imply they exist. |

## Lens 1: UX and user trust

### P1 — Cross-customer promotion offers and accepts the wrong users

The target organization is editable in
`client/src/pages/SolutionPromotions.tsx:105`, but the user query remains bound
to `review.organization_id` at `client/src/pages/SolutionPromotions.tsx:117`.
The selected target is submitted at `client/src/pages/SolutionPromotions.tsx:165`.
On the server, `_assign_role_users` looks up only the role and existing
assignment at `api/src/services/builder/promotion.py:501`; it never verifies
that each user belongs to the resolved target organization at
`api/src/services/builder/promotion.py:561` before applying assignments at
`api/src/services/builder/promotion.py:614`.

This is a confirmed cross-tenant administration defect, not just stale picker
copy: an administrator can publish to customer B while globally assigning the
new role to a customer A user.

### P2 — Query failures masquerade as empty work

The support query is declared at `client/src/pages/Build.tsx:107`, but the
catalog render at `client/src/pages/Build.tsx:358` distinguishes only loading,
empty, and data. A failed request therefore tells support staff that no builds
exist or that filters have no matches.

The workbench similarly declares sessions, revisions, and turns queries at
`client/src/pages/SolutionBuilder.tsx:282`, then coerces missing data to empty
arrays at `client/src/pages/SolutionBuilder.tsx:313`. Its session panel at
`client/src/pages/SolutionBuilder.tsx:1371` can consequently offer “Start
session” after a failed session-history request, while revisions and turn state
can appear absent.

### P2 — Setup can lose durable provisioning progress on reload

The admin UI owns the provisioning `jobId` only in component state at
`client/src/pages/settings/Builder.tsx:116` and learns it only from the current
mutation at `client/src/pages/settings/Builder.tsx:175`. The setup contract at
`api/src/models/contracts/sandbox_runner.py:154` and GET handler at
`api/src/routers/sandbox_runner_admin.py:45` do not return an active provisioning
job. The PlatformJob continues durably, but the setup page loses its progress
and permits an ambiguous “Provision and test” experience after refresh.

### P2 — Setup hides the server's actionable blocker details

The readiness contract carries `code`, `message`, and `action` at
`api/src/models/contracts/sandbox_runner.py:131`, while the setup checklist at
`client/src/pages/settings/Builder.tsx:461` reduces readiness to five generic
labels. The separate unready Build page does show blocker details, so this is a
setup-surface omission rather than missing backend information.

### P2 — Failed enablement leaves an optimistic lie

The switch updates `draft.enabled` before the request at
`client/src/pages/settings/Builder.tsx:390`. The save mutation's error handler
at `client/src/pages/settings/Builder.tsx:172` only shows a toast. A 409 or other
failure can therefore leave the page saying “Enabled” until it is reloaded.

## Lens 2: regression and compatibility

### Confirmed safe

- Existing V1/V2 apps keep `runtime_mode="trusted"`; only Builder-owned apps
  enter the isolated runtime unless an administrator explicitly promotes them
  as trusted (`api/src/models/orm/applications.py:92`).
- The isolated app host is mounted under the existing API origin and public
  `/apps/*` launch experience; it requires no second hostname, forwarded port,
  or permanent app-host container (`api/src/routers/solution_app_host.py:1`).
- Opaque-origin runtime CORS is explicitly restricted to `Origin: null` on the
  Builder runtime prefix (`api/src/main.py:529`).
- Solution-app tokens are actor-marked, short-lived, bound to one user,
  Solution, app, and organization, and receive only the reviewed SDK route
  scopes (`api/src/services/builder/app_session.py:93` and
  `api/src/routers/solution_app_runtime.py:50`).
- Builder work uses canonical PlatformJobs. Workflow execution remains on the
  existing execution worker; Cloudflare creates ephemeral runner containers,
  and the optional local runner is hoster-chosen rather than a default Bifrost
  service.
- External completion and retry paths are attempt-fenced before durable state
  changes (`api/src/services/platform_jobs.py:569` and
  `api/src/services/platform_jobs.py:609`).

### Broad-suite signal — disposition complete

The pre-integration `./test.sh all` run collected 7,698 tests while source was
being live-mounted and exposed a failure in
`tests/e2e/api/test_events.py::TestDeliveryRetry::test_cannot_retry_pending_delivery`.
The deterministic harness was corrected so API/scheduler fixtures are ready
before execution services start, and the exact event test now passes. The same
clean-stack work exposed missing real build-plane provisioning; the test matrix
now provisions the authenticated Local provider through a durable PlatformJob
and exercises the canonical sandbox image end to end.

## Lens 3: product completeness and parity

### Confirmed present

- Agent Skills use canonical `SKILL.md` instructions with upload, validation,
  bundle browsing, download/export, and explicit Skill-use badges in the agent
  and Builder UI.
- Native Builder and the external MCP harness share the same Builder Agent,
  Solution workspace, tool contract, durable conversation, revisions, and
  checkpoint artifacts.
- Provider support has a focused “My work” view plus an explicit “All customer
  work” view with organization, owner, and search filters.
- Private work supports owner, edit, view, and support access without cluttering
  a support operator's default view.
- Promotion reviews a pinned green revision and publishes a separate shared
  release while preserving the private source, history, and subsequent edits.
- Platform admins have a guarded Global Workspace proposal flow with validate,
  explicit apply, conflict detection, and rollback.
- Builder is hidden from ordinary users until AI, runner connectivity, and the
  enable switch are ready; admins receive a setup route.
- Promotion explicitly selects isolated or trusted browser runtime while legacy
  apps preserve trusted behavior.

### P2 — The support catalog was unbounded (resolved)

The support catalog now returns a paged response with total, limit, and offset;
the UI requests 50 rows at a time and provides Previous/Next navigation while
preserving organization, owner, and search filters. The personal “My work”
view remains intentionally compact and unpaged.

### Explicitly deferred, not silently missing

- Persistent favorites across apps, agents, forms, and other entities.
- Review comments, presence, and true simultaneous co-editing.
- Monthly/per-user/per-organization aggregate Builder quota policies.
- Cloudflare invoice/charge ingestion into Bifrost; the external run and its
  duration are recorded, while provider charges remain in Cloudflare.

Per-turn AI call/token limits are enforced and shown as raw and percentage
usage. AI provider/model spend remains attributable by user and organization in
the existing usage report.

## Lens 4: test coverage

### Strong coverage

- Private Solution capability, ownership, support access, sessions,
  collaboration, promotion, separate release preservation, and MCP handoff have
  backend E2E coverage in `api/tests/e2e/platform/test_private_solutions.py`.
- Isolated launch, cookie/token binding, runtime routes, revocation, and CORS
  have E2E coverage in `api/tests/e2e/platform/test_solution_app_host.py`.
- Sandbox capability fencing, callback uploads/completion, retry, cancellation,
  and checkpoint streaming have focused router/service tests.
- Global Workspace validation/apply/rollback has service and browser-path
  coverage.
- Build, Builder workbench, promotion review, Skill editor/bundle UI, and admin
  setup have React component coverage; the administrator happy path has
  Playwright coverage on desktop and mobile.
- Python and JavaScript runner harnesses have direct suites, including
  cancellation and OpenCode state restoration.

### P2 test gates — resolved

- `BuilderAgentTurnService.finalize_agent_turn` directly covers changed
  workspaces followed by deploy enqueue failure, completion fencing,
  revision-storage compensation, and staged/accepted harness cleanup.
- Checkpoint coverage includes digest mismatch, promotion failure, fenced
  terminal completion, retry cleanup, and partial-promotion compensation.
- Promotion component coverage asserts that user discovery follows the selected
  target organization and resets stale role selections.
- Promotion E2E publishes across organizations and proves target-organization
  role-assignment validation.

## Completed repair sequence

1. Fixed and tested promotion target/user validation on client and server.
2. Restored provisioning jobs across setup reloads, surfaced blocker details,
   and reverted failed enablement.
3. Added truthful Build/workbench error states.
4. Added bounded support-catalog pagination and tests.
5. Closed changed-revision completion/checkpoint compensation coverage.
6. Classified and resolved the event-delivery harness signal.
7. Proved the real build plane through durable Local-provider provisioning,
   authenticated dispatch, compilation, callbacks, artifact staging, and live
   deployment, then completed the post-`origin/main` verification matrix and
   exact backup inventory.

## Release gate

The P1/P2 repair sequence, final backup inventory, and required scoped
verification matrix are complete. Do not publish or merge the reconstructed
Builder into `main` until customer acceptance and explicit approval.
