# Private Solution Builder — implementation status

**Updated:** 2026-08-10

**Integration branch:** `codex/code-builder-recovery-20260807`

**Original Builder source:** `1696d8693`

**Recovery ledger:** `2026-08-07-code-builder-recovery.md`

## Release position

The private Solution Builder has been reconstructed on the scalable scheduler
and canonical PlatformJob system. It is a release candidate on an isolated
integration branch, not a merged or public feature. The original `code-builder`
branch remains untouched and is backed up remotely.

Current `origin/main` at `0a78b311f0fc5f6bfcc7f85fc4e785945f26eee2`
was deliberately integrated in merge commit `d277f0368`. Corrected shared
scheduler, Solution deploy, table-policy publication, diagnostics, and
debug/test behavior remain canonical.

The current design removes the first implementation's dedicated coordinator,
permanent runner, public app-host port, alternate origin, and DNS requirement.
The existing scheduler coordinates durable work; Cloudflare starts a sandbox
container only while a job runs; the existing API mounts the narrow generated
app runtime internally. A hoster can enable Builder without adding a Bifrost
container or exposing another port.

Current-main reconciliation, the complete scoped matrix, and the exact backup
inventory are closed. Customer acceptance and explicit push/merge approval are
still required.

## User experience now implemented

### Build catalog and creation

- Ordinary users do not see Build until AI, runner connectivity, and the admin
  enable switch are ready. Administrators always receive a setup path with
  direct links to missing AI configuration.
- The creation surface uses a centered app-builder prompt, private-workspace
  explanation, staged loading/creation feedback, and a direct animated handoff
  into the workbench.
- The default **My work** view contains owned and explicitly shared projects.
  Users with cross-organization support authority can deliberately open
  **All customer work**, then filter by organization and owner or search app,
  customer, and owner text. Other customers' work never clutters the default.
- Cards distinguish owned, collaborator-view, collaborator-edit, and support
  access and show current review state.

### Builder workbench

- A resizable desktop Agent/workbench split and explicit mobile
  Agent/Preview/Code/Changes pane switcher provide a familiar app-builder
  layout.
- The conversation, selected session, active revision, deployed preview,
  route, device preset, tab/pane, and split position restore when a user
  returns. A queued/running durable job is rediscovered without creating a
  duplicate turn.
- **Preview** provides responsive desktop/tablet/mobile frames, route entry,
  reload, first-deploy and stale-preview states, two-phase session/document
  loading, and an open-app action.
- **Code** contains a navigable Solution file tree and source viewer.
- **Changes** contains real unified diffs and immutable revision history.
- The status rail shows build phase, cancellation, source-to-preview state,
  runner provider, review readiness, exact AI call/token consumption, and the
  percentage of both enforced turn limits used.
- Failures preserve a resumable workspace/OpenCode checkpoint when safe. The
  next turn resumes the harness session and worktree instead of discarding all
  coding context.

### Skill and Agent experience

- A bundled Agent has one instruction source: `SKILL.md`. When `bundle_path`
  points at a valid bundle, runtime and UI use that markdown and do not maintain
  a second drifting instructions value.
- Solution-managed bundle paths are relative to the Solution root, for example
  `skills/expense-tracker`; they are not relative to `.bifrost` or `_repo`.
- The Agent UI supports validated `.skill`/`.zip` drag-and-drop, file browsing,
  Markdown/source preview, bundle export, and visible Skill use/provenance.
- Direct uploads live in Agent-owned storage. Solution-managed Skills remain
  read-only from the Agent editor and are changed through the Solution.
- Inline non-bundled instructions use the shared TipTap Markdown editor with
  Edit/Preview modes.
- The Builder workbench names the active `bifrost-build` Skill. Tool execution
  also labels `read_skill_asset`, so Skill usage is visible rather than hidden
  in backend-only capability support.

### Collaboration and publishing

- Owners can invite view or edit collaborators. The centralized project gate
  applies to project, conversation, source, revision, preview, and app-runtime
  access.
- Platform administrators and eligible support operators can discover and
  manage private customer builds through the separate support view without
  impersonating the owner or polluting My work.
- Edit and build operations retain a safe single-writer Solution lock.
- Review requests pin an exact green revision. The review UI shows diffs,
  entity evidence, blockers, requested scope, target customer, runtime mode,
  roles/connections, and explicit publish confirmation.
- Publishing creates or updates a separate shared Solution release for the
  selected customer or global target. The private source project, sessions,
  history, and future work remain intact.
- Isolated runtime is the default. Trusted mode is an explicit administrator
  choice. Existing V1/V2 apps remain on their established trusted runtime and
  SDK contract.

### Administrator Global Workspace

- Platform administrators can create/open a dedicated Global Workspace from
  Build. It targets global `_repo` source without pretending it is an ordinary
  customer Solution.
- AI edits only an immutable proposal. The workbench removes app preview,
  sharing, and publishing actions and replaces them with Proposal/Live state,
  diff, validation, refresh, explicit apply, and rollback.
- Validation parses Python without importing or executing it and protects
  `.bifrost` manifests from direct mutation.
- Apply is transactional and verifies the live digest before and after writes.
  Rollback succeeds only while live `_repo` still matches the last apply, so it
  never overwrites a newer administrator edit.

### Administrator setup and operations

- **Settings > Builder** is a guarded readiness wizard, not a collection of
  environment-variable instructions.
- The administrator selects Cloudflare or the explicit self-hosted provider,
  saves encrypted/masked credentials, confirms the existing Bifrost callback
  address, provisions/tests through a PlatformJob, watches live progress, and
  can enable users only after the blocking checks are green.
- The Cloudflare path clearly states that it needs the same public HTTPS
  Bifrost address and no second hostname, DNS record, or forwarded port.
- The page explains the version-matched runner image and that Cloudflare
  containers scale to zero.
- A usage section links to the existing AI Usage report. Model provider, model,
  tokens, estimated cost, user, and organization are recorded by Bifrost.
  External run identity and duration are stored; Cloudflare container charges
  remain in the hoster's Cloudflare account.

## Runtime and container model

| Concern | Where it runs | Permanent new Bifrost container? |
| --- | --- | --- |
| authorization, staging, callbacks, preview runtime | existing API | no |
| durable claiming, retry, cancellation, provisioning | existing scheduler replicas via PlatformJob | no |
| workflow and Agent execution outside Builder | existing workflow worker | no change |
| coding harness and fixed app build toolchain | short-lived Cloudflare Sandbox container | no |
| optional local/self-hosted execution | hoster-operated `builder-runner` image | only when the hoster chooses this provider |

The Cloudflare runner image is a release artifact, not a long-running Bifrost
service. It receives a strict job envelope through Cloudflare's Workflow/
Container binding. The image does not expose a public callback server to the
internet; it starts with the envelope, downloads staged input using a
short-lived job capability, calls Bifrost's scoped AI proxy, uploads bounded
artifacts/checkpoint state, and reports completion.

## Authentication and scope foundation

Authorization scopes follow the Graph-inspired convention
`<resource>[.<subresource>].<action>[.all]`. The code-owned catalog is shared by
roles and attenuated actor credentials.

- `solutions.build` is the human permission to use Builder on a project that
  separately admits the principal. There is no assignable
  `solutions.jobs.execute` or `solutions.builds.execute` synonym.
- `platform.superuser` belongs only to the immutable built-in Platform Admin
  role and satisfies action checks without bypassing resource/business gates.
- `organization.impersonation` is the initial Platform Operator capability.
  Provider-organization eligibility remains a separate condition.
- Provider members receive an interim Platform Operator shadow assignment
  while legacy provider checks migrate. The sync is compatibility behavior,
  not the final role design; moving out of the provider org or becoming a
  Platform Admin removes the redundant assignment.
- App-runtime tokens use the same catalog names—such as
  `tables.documents.read`, `files.content.write`, and `workflows.execute`—but
  are also bound to one app, Solution, organization, viewer session, and the
  narrow runtime router. The names evolve the common scope system; they are not
  a parallel permission framework.
- Sandbox capabilities are not human scopes. They bind one job, type,
  dispatch attempt, artifact digest, operations, and expiration and are
  accepted only by the internal sandbox callback routes.

## Native and external harness parity

The native OpenCode runner and the progressive MCP Agent gateway use the same
Builder Agent, Skill package, access gate, workspace limits, and filesystem/
validation tools:

- list, read, search, write, patch, delete, and create directories;
- validate the complete Solution workspace;
- read the canonical `SKILL.md` and companion Skill assets;
- commit mutations through the same immutable Builder revision service.

An external harness therefore does not receive a second set of nearly
identical direct-ORM tools. MCP routes bridge into the same REST/domain
contract and require `solutions.build` plus project access.

## Security properties

- Ordinary users cannot discover builds outside ownership, collaboration, and
  organization reach. Cross-customer support access requires the explicit
  operator capability and remains auditable.
- Generated documents run in a sandboxed opaque-origin iframe even though the
  public URL uses the normal Bifrost host. They cannot read control-plane DOM,
  cookies, or storage.
- Runtime cookies are host-only and scoped to the exact app path. Runtime
  tokens are short-lived and re-check the live viewer/project/release access.
- SDK tables, files, workflows, executions, and WebSockets require both action
  scope and exact Solution/app/org/resource binding.
- Cloudflare credentials and upstream AI keys never enter the sandbox. The
  sandbox has only a single-job callback capability.
- Input, output, Skill archives, and checkpoint state are bounded and checked
  for traversal, duplicate/case-collision, symlink, file-count, size, and hash
  violations.
- Model-authored Python, schedules, events, autonomous agents, and connections
  remain inert in private preview. Human-reviewed promotion is the activation
  boundary.
- Per-turn model reservations are fenced transactionally before every request,
  preventing concurrent calls from oversubscribing call or token limits.

## Durable continuity

Builder projects, sessions, conversations/messages, turns, revision pointers,
review pins, releases, and PlatformJobs live in PostgreSQL. Immutable source,
build artifacts, and resumable harness state live in object storage. Reopening
a project restores the most recent conversation and preview immediately when a
deployed revision exists; only a new one-time app session is minted. A running
external job is reattached by ID and updates through the existing PlatformJob
notification transport.

OpenCode provides the coding harness, tool loop, session persistence, and
compaction behavior. Bifrost owns the immutable Solution boundary,
authorization, staging, AI proxy, metering, revision finalization, and preview
deployment.

## Known limitations before production approval

- No platform-wide monthly quota policy yet exists for Builder spend or
  Cloudflare usage by user/organization. The implemented limits are hard
  per-turn Agent call/token fences. AI spend remains fully attributed and
  reportable.
- Bifrost does not ingest Cloudflare invoice data or calculate authoritative
  per-run Cloudflare dollars. It records the provider run and duration; the
  hoster controls Cloudflare account alerts/budgets.
- The catalog does not yet have favorites. Review comments and real-time user
  presence/handoff indicators are also deferred; collaboration/access and the
  single-writer lock are implemented.
- True simultaneous source co-editing is not supported.
- The local runner is an explicit hoster responsibility and is not an automatic
  outage fallback for Cloudflare.
- Customer acceptance and explicit push/merge approval remain release
  blockers.

## Verification state

Final post-integration evidence on the isolated branch includes:

- API Pyright and Ruff; generated OpenAPI/Skill truth and contract tripwires;
- 423 focused Builder/shared-scheduler backend unit tests and 126 live E2E
  tests, plus the 14-test form-embed conflict-preservation check;
- 41 Python runner tests, two OpenCode JavaScript tests, and one Cloudflare
  runtime-helper test;
- 178 focused client tests across 22 files, TypeScript, lint with one unchanged
  React Hook Form compiler warning, and five no-retry Playwright passes with
  desktop/mobile screenshots inspected;
- production, development, and test/sandbox Compose rendering plus Kubernetes
  rendering with no permanent Builder container; and
- an exact post-integration inventory of 76 backup paths: 35 exact, 31
  deliberately evolved, and 10 intentionally omitted with replacements.

The repository-wide backend, Vitest, and Playwright suites were not rerun; the
bounded Builder and affected shared-scheduler surfaces were selected under the
repository's scoped-verification policy.
