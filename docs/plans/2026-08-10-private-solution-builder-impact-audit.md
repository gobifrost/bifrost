# Private Solution Builder impact audit

**Date:** 2026-08-10  
**Branch:** `codex/code-builder-recovery-20260807`  
**Recovery checkpoint:** `fa0f0efc9`  
**Scope:** UX, regression/compatibility, product completeness/parity, and test coverage

## TL;DR

The reconstructed Builder has the intended durable architecture and most of the
first-class product surface, but it is not yet a release candidate. One
multi-tenant promotion defect must be fixed before release, and the admin setup
and workbench need to tell the truth after reloads and request failures. The
support catalog also needs bounded pagination before it is credible at the
stated MSP scale of hundreds of customers and thousands of apps.

| Rank | Finding | Disposition |
| --- | --- | --- |
| P1 | Publishing to another customer still loads role assignees from the source customer, and the server accepts those IDs without target-scope validation. | Release blocker; fix UI and server, add cross-org E2E coverage. |
| P2 | The support-wide build catalog fetches and renders every matching private Solution. | Add server pagination and a familiar incremental catalog control. |
| P2 | Several list/detail query failures are rendered as legitimate empty states. | Add retryable, query-specific error states. |
| P2 | Runner provisioning progress cannot be restored after an admin reloads setup. | Return the active provisioning PlatformJob and hydrate progress from it. |
| P2 | Builder setup receives actionable readiness blockers but discards their message/action details. | Surface the server-authored blockers in setup. |
| P2 | The Builder enable switch remains visually changed when saving fails. | Revert to the persisted value on error and test the failure path. |
| P2 test gate | Critical turn-finalization compensation and checkpoint retry branches have only partial direct coverage. | Add focused unit tests before release. |
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

### Broad-suite signal requiring disposition

The pre-integration `./test.sh all` run collected 7,698 tests and exposed a
failure in `tests/e2e/api/test_events.py::TestDeliveryRetry::test_cannot_retry_pending_delivery`.
The Builder branch has no diff from `origin/main` in the event router,
processor, or this test, but repository policy does not permit calling the
failure “unrelated.” The failure must be classified from the final traceback
and either fixed as a product/harness defect or split into a blocking repair
before release.

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

### P2 — The support catalog is unbounded

The list handler accepts filters but no limit/cursor at
`api/src/routers/solution_builder.py:240`. The service executes the full ordered
query and calls `rows.all()` at
`api/src/services/builder/private_solutions.py:267`. The UI then mounts every
row at `client/src/pages/Build.tsx:361`. This does not meet the stated operating
shape of roughly 1,000 apps, especially on support workstations and mobile.

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

### P2 test gates

- `BuilderAgentTurnService.finalize_agent_turn` has a no-change success test,
  but no direct test for a changed workspace followed by deploy enqueue failure,
  completion fencing, revision-storage compensation, and staged/accepted
  harness cleanup (`api/src/services/builder/agent_turns.py:247`;
  `api/tests/unit/test_builder_agent_turns.py:267`).
- Checkpoint preservation has a matching-digest happy path, but digest mismatch,
  promotion failure, fenced terminal completion, and retry cleanup do not all
  have direct service-level assertions
  (`api/src/services/builder/agent_turns.py:383` and
  `api/src/services/builder/agent_turns.py:452`).
- Existing promotion UI coverage changes the target organization but mocks the
  user hook without asserting its scope
  (`client/src/pages/SolutionPromotions.test.tsx:20` and
  `client/src/pages/SolutionPromotions.test.tsx:136`).
- Existing promotion E2E coverage publishes to the source organization and does
  not exercise target-organization role assignment validation
  (`api/tests/e2e/platform/test_private_solutions.py:633`).

## Suggested repair sequence

1. Fix and test promotion target/user validation on both client and server.
2. Restore provisioning jobs across setup reloads; surface blocker details and
   revert failed enablement.
3. Add truthful Build/workbench error states.
4. Add bounded support-catalog pagination and tests.
5. Close changed-revision completion/checkpoint compensation coverage.
6. Classify and resolve the broad event-delivery test failure.
7. Re-run the focused Builder/shared scheduler matrix, then the final broad
   gates after integrating current `origin/main`.

## Release gate

Do not publish or merge the reconstructed Builder into `main` until the P1 and
P2 repair sequence is complete, the backup inventory is rerun against the final
integration commit, and the required verification matrix is green. Customer
acceptance remains outstanding.
