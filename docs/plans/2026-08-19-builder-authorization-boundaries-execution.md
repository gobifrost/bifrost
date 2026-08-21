# Builder authorization boundaries and end-to-end execution plan

**Owner:** Jack Musick / Bifrost engineering
**Prepared:** 2026-08-19
**Status:** Approved — implementation in progress
**Execution branch:** `codex/code-builder-pydantic-integration-20260816`
**Supersedes:** the authorization design in the July Builder status, Phase 5
proposal, and any earlier use of reach suffixes such as `.all`, `.global`, or
`.own`

## 1. Outcome

Bifrost will have one authorization framework for the UI, REST API, native AI
Builder, CLI, and MCP:

1. **Capabilities say what a person can do.** They use a small vocabulary such
   as `agents.read`, `agents.readwrite`, `workflows.execute`, and
   `builder.execute`.
2. **Role-assignment boundaries say where those capabilities apply.** A single
   assignment may select organizations, reusable organization groups, Managed
   organizations, and Platform.
3. **Resource access says which particular object may be touched.** Solution
   ownership/collaboration, Agent roles, app/form/table/workflow access, and
   table/file policies remain exact object gates.
4. **One maintained coding profile drives every native Builder session.** The
   target and the caller's effective authority determine its tools. A private
   Solution does not create or own an authorization-bearing coding Agent.
5. **Native Builder, MCP, and CLI converge on the same operation catalog and
   REST/domain behavior.** They do not maintain separate authorization or
   product implementations.

The final implementation must look native to this framework. Legacy
`is_superuser`, provider-organization, and unbounded role-union checks are
migration inputs, not permanent fallback branches.

## 2. The decision about “Global”

**Global is not a scope and does not appear in a capability name.** Global is
the user-facing label for the **Platform authorization boundary**.

| UI label | Stored boundary | Covers | Does not imply |
|---|---|---|---|
| Organization | `organization` + organization ID | That exact organization | Other customers, global records, or `_repo` |
| Organization group | `organization_group` + group ID | Current members of a named MSP-maintained group/pod | Future or unrelated organizations unless added to the group |
| Managed organizations | `managed_organizations` | All current and future customer organizations the platform manages | The provider organization, platform-global records, or `_repo` |
| Global | `platform` | Platform context and records whose `organization_id` is null | Source-repository access unless `repository.*` is also granted |

This separation prevents names such as `workspace.readwrite.global` and avoids
repeating reach on every capability. A role can be reused at more than one
boundary; the assignment, not the role definition, carries the boundaries.

Platform never automatically includes Managed organizations. The default
Platform Builder assignment selects both because that role is intended for
cross-customer and Global building. Other roles may select either one without
the other.

The provider/MSP's own organization is still an ordinary specific Organization
boundary. It is not silently included in Managed organizations or Global.

Platform is the **authoring and administration boundary** for global records.
It does not remove Bifrost's inherited-resource behavior: a person working in
an Organization context may still discover and use a globally defined Agent,
app, form, workflow, or other resource when that resource's access level, role
grant, and applicable policy permit it. That inherited read/execute access does
not allow the person to edit the global definition. Tests must distinguish
“consume an authorized global resource” from “administer Global.”

## 3. Default roles

Role definitions remain globally reusable capability bundles. Assignments bind
a user and role to one or more boundary selections.

### Platform Admin

- Immutable built-in role.
- Assigned only at the Platform boundary.
- Carries the internal `platform.superuser` wildcard.
- Replaces legacy superuser branching after migration.
- Its wildcard covers all boundaries and resource gates, but actions still
  record the selected/effective boundary and actor in audit data.

### Platform Operator

- Built-in, platform-maintained default for MSP staff who support customers.
- Assigned at Managed organizations by the upgrade migration for existing
  non-admin members of the provider organization.
- Contains the approved customer-administration and support capabilities.
- May include `builder.read` so operators can find and diagnose customer build
  sessions, but does **not** include `builder.execute` or the standard build
  bundle.
- An operator who should actively build receives Builder separately at the
  appropriate boundaries.
- Does not grant Platform/Global or repository authority.
- Is a sticky assignment during migration: leaving the provider organization
  later does not silently remove an explicitly migrated role. Administrators
  manage it like any other assignment after cutover.

### Builder

- Mutable default role for people allowed to build.
- Contains `builder.execute`, `solutions.readwrite`,
  `solutions.build.execute`, `solutions.deploy.execute`, and the read
  capabilities needed to discover resources they may reference.
- Can author a complete private Solution without receiving organization-wide
  write capabilities for every entity declared inside that inert source
  bundle.
- Its Solution targets are limited to the person's own private Solutions plus
  Solutions explicitly granted to the person or one of their effective Roles.
- May be assigned to any selected organizations/groups or Managed
  organizations.
- Direct creation of loose organization resources still requires the relevant
  domain `readwrite` capability and exact target access.
- Does not contain `repository.readwrite` and therefore cannot modify the
  platform source workspace.
- Does not contain publish authority. Its deploy capability is constrained by
  the private-Solution resource gate to its fenced preview; it cannot create a
  shared organization or Global release.
- Actual resources remain constrained by the covering assignment boundary and
  their Solution/access/policy gates.

### Platform Builder

- Mutable default role for builders who may work across customers and in the
  Global workspace.
- Uses the private Builder bundle plus the standard direct-workspace domain
  capabilities and `repository.readwrite`.
- Its default grant is a **multi-boundary assignment** selecting Managed
  organizations and Platform. Platform does not imply Managed organizations;
  the preset deliberately selects both.
- An administrator may select any combination of individual organizations,
  organization groups, Managed organizations, and Platform instead.
- There is no separate “Global Builder” role unless a hoster chooses to create
  one as a custom role.

### Organization Member

- Baseline default for ordinary users in their home organization.
- Preserves the non-administrative behavior users had before the migration.
- Does not automatically include `builder.execute`; a hoster chooses whether
  Builder is part of its onboarding defaults.

The precise capability arrays for the defaults are generated and reviewed from
the canonical capability catalog before the seed migration is written. They
must not be duplicated in migration scripts, UI constants, and runtime code.

The private Builder bundle is intentionally narrow: Builder may edit any
portable content inside its owned/collaborative private Solution, but those
files have no organization-wide effect until a separately authorized promotion.
Direct organization-workspace operations are still domain-based and require
the caller's corresponding `readwrite`/`execute` capabilities. Platform
Operator is a separate customer-management role and does not add build rights.
Platform Builder adds direct-workspace capabilities and repository authority.
A custom role can add or remove any independently catalogued capability.

## 4. Capability language

### Grammar

```text
<resource>[.<subresource>].<action>
```

Approved actions are:

- `read` — inspect or list;
- `readwrite` — read plus create, change, and delete;
- `execute` — initiate an operation that performs work.

`readwrite` implies `read` for the same resource. It does not imply `execute`.
There are no reach suffixes and no general `build`, `write`, `manage`, or
`publish` actions. `platform.superuser` is the one internal wildcard exception.

Subresources are used only when they protect a materially separate authority,
not to encode arbitrary endpoint names. For example, `solutions.deploy.execute`
is distinct from editing Solution source. The initial lifecycle capabilities
are `agents.execute`, `workflows.execute`, `builder.execute`,
`solutions.build.execute`, `solutions.deploy.execute`,
`solutions.publish.execute`, and `apps.deploy.execute`. Publish review is
separately discoverable through `solutions.publish.read`. Starting compute
or creating an externally visible release requires `execute`; ordinary
create/update/delete remains `readwrite`. This replaces the old `publish` and
`build` verbs without losing least-privilege separation.

### Initial domains

The catalog review must cover at least:

- `agents`, `apps`, `forms`, `tables`, `workflows`, `solutions`;
- `builder` and `repository`;
- `organizations`, `organizationgroups`, `roles`, `integrations`, `configs`,
  `events`, `claims`;
- `managedfiles`, `filepolicies`, `policyrules`, and `tabledocuments`;
- `executions`, `knowledge`, and explicitly authorized Platform operations.

Canonical capability nouns use the existing public product/CLI plural (`apps`,
not `applications`). Compound public CLI groups become unpunctuated capability
nouns (`managedfiles`, `filepolicies`, `policyrules`, `platformjobs`) while
operation IDs and CLI/MCP labels retain their established readable forms. The
catalog, CLI, MCP, generated Skill references, UI, and tests change together.
Do not introduce aliases without a separately approved compatibility need.

### Files and policy concepts

These are deliberately separate:

| Capability family | Controls |
|---|---|
| `repository.read/readwrite` | Source files in the platform `_repo` workspace |
| `repository.access.readwrite` | Delegating runtime access to `_repo`; does not itself edit files |
| `managedfiles.read/readwrite` | Managed file data governed by Bifrost policies |
| `filepolicies.read/readwrite` | File-policy definitions |
| `policyrules.read/readwrite` | Reusable policy-rule definitions |
| `tabledocuments.read/readwrite` | Table document data, still subject to table policy |

This replaces the confusing `files.content.*` dialect. A file or table policy
can still deny access after the capability and boundary checks succeed.

## 5. Authorization data model

### Role assignment

Replace the two-column `user_roles` association with a durable assignment plus
boundary selections. Capabilities remain stored once on the Role; selecting
twenty organizations does not copy twenty scope arrays.

```text
RoleAssignment
  id: UUID
  user_id: UUID
  role_id: UUID
  assigned_by_user_id: UUID | null
  assigned_at: timestamp

RoleAssignmentBoundary
  id: UUID
  role_assignment_id: UUID
  boundary_kind: organization | organization_group |
                 managed_organizations | platform
  organization_id: UUID | null
  organization_group_id: UUID | null

OrganizationGroup
  id: UUID
  owner_organization_id: UUID
  name: string
  member_organizations: many-to-many Organization
```

Database constraints:

- a user has one editable assignment record per Role;
- an assignment contains one or many boundary selections;
- `organization_id` is required only for `organization`;
- `organization_group_id` is required only for `organization_group`;
- both IDs are null for `managed_organizations` and `platform`;
- duplicate boundary selections within an assignment are rejected;
- deleting a user or role cascades its assignment and selections;
- group membership and assignment changes invalidate authorization caches and
  are audited.

An Organization group is owned by the MSP/provider organization and may
contain only organizations that provider manages. Managing groups requires the
`organizationgroups.readwrite` capability in Managed organizations; this does
not confer capabilities inside member organizations by itself.

The UI supports bulk organization selection in one assignment action. Named
organization groups provide durable MSP pods: adding a customer to a pod
updates every assignment targeting that group without editing each user. An
assignment may combine a pod, individual exceptions, Managed organizations,
and Global; overlapping coverage is harmless because capabilities are unioned.

Use a new forward-only Alembic revision after the branch's current migration
head. Do not edit the withdrawn Builder revision bodies or reuse their IDs.

The legacy free-form `Role.permissions` JSON is inventoried at the same time.
Its remaining product meaning (currently including Agent-promotion behavior)
must become a named capability or a typed resource policy, then the JSON field
and UI editor path are removed if no independent use remains. The completed
system must not require callers to understand both `permissions` and `scopes`
as competing authorization languages.

### Resource grants remain separate

Agent-role links, Solution collaborators, application/form/table/workflow role
links, ownership, and policy decisions do not move into `RoleAssignment`.
Those answer “which object?” after a capability answers “what?” and a boundary
answers “where?”.

Solutions support both direct-user and Role resource grants:

```text
SolutionUserGrant(solution_id, user_id, access: view | edit)
SolutionRoleGrant(solution_id, role_id, access: view | edit)
```

The existing user-only `SolutionBuilderCollaborator` data migrates into the
direct-user grant. A Role grant applies only when the person holds that Role in
a boundary covering the Solution's organization. Neither grant supplies
`builder.execute` or any other capability by itself.

For a person with the default Builder capability bundle, the ordinary Build
list contains:

- private Solutions they own;
- Solutions granted directly to them;
- Solutions granted to one of their effective Roles.

It does not contain every private Solution in the same organization. `view`
allows review; `edit` allows Builder sessions and revisions. Ownership retains
sharing, deletion, and publish-request management unless those management
rights are designed separately later.

### Effective authorization context

Resolve a typed context for every human request:

```text
AuthorizationContext
  requester                 # authenticated person
  effective_actor           # normally requester; support action is explicit
  selected_boundary
  effective_capabilities
  role_assignment_ids
  request/correlation ID
```

Support staff do not become the customer's identity. They act as themselves
inside a Managed organizations boundary. The audit trail records requester,
effective actor, customer organization, affected owner, operation, and before/
after state. “Last updated by” shows the real support person.

System, application-runtime, preview-runtime, and external runner principals
remain typed principals with their own narrow contracts. They are not modeled
as human Platform roles.

## 6. Effective authorization algorithm

Every converted human operation follows this order:

1. Authenticate the requester.
2. Resolve or require the selected boundary.
3. Load role assignments that cover that boundary.
4. Union their capabilities within that boundary.
5. Apply capability implications such as `readwrite` → `read`.
6. Require the operation's catalogued capability.
7. Resolve the requested resource and enforce its exact access/policy gate.
8. Execute domain behavior and write audit data.

Boundary coverage rules:

- an Organization assignment covers only its stored organization;
- an Organization group covers the group's current member organizations;
- Managed organizations covers customer organizations designated by the MSP
  relationship, including customers added later;
- Platform covers global records and platform context;
- Platform Admin's wildcard may cross boundaries, but the operation still
  selects and records the effective one;
- Platform does not imply repository access;
- grants from unrelated boundaries are never unioned into the active one.

There is no deny precedence in v1. Authorization is additive within an active
boundary, followed by resource gates. Explicit deny semantics can be designed
later without changing capability names.

### Tokens, cache, and revocation

- Do not put a growing list of customer organization IDs or the full effective
  capability set into long-lived browser tokens.
- Tokens identify the principal and selected context; the server resolves
  assignments through a short-lived, versioned cache.
- Assignment changes increment an authorization version and invalidate cached
  decisions so revocation is prompt.
- Organization-group membership changes use the same invalidation path.
- Workers and Platform Jobs store requester, selected boundary, target, and
  requested operation. They re-authorize at execution and again before any
  irreversible publish/deploy/apply step.
- Cloudflare receives only the existing job capability envelope, never the
  caller's general Bifrost credentials or role set.

### Future workflow delegation compatibility (not implemented in this program)

The evaluator must accept typed capability sources rather than assuming every
capability came from a human RoleAssignment. This preserves the intended future
replacement for minted superuser execution tokens:

1. a workflow declares the additional capabilities it requires;
2. registration/deployment policy approves which of those capabilities the
   workflow is allowed to request;
3. execution starts as the requesting user in the selected boundary;
4. a short-lived execution grant adds only the approved workflow capabilities,
   exact resource constraints, workflow/run identity, and expiration;
5. every use is audited as delegated workflow authority.

A workflow must never elevate itself merely by editing its own declaration.
Approval and runtime grant issuance are separate trusted steps. This program
does not replace current execution tokens, but the `AuthorizationContext`,
evaluator, cache, and audit model must leave a first-class
`delegated_execution` grant source so that later work does not require another
authorization rewrite.

## 7. REST and route cutover

The present branch has roughly 663 REST endpoints and 55 router/handler files
containing at least one legacy admin/provider check. Completion requires an
inventory, not a search-and-replace.

### Route classification

Every route receives one explicit classification:

1. human platform operation governed by the operation catalog;
2. public/authentication route;
3. application or preview runtime route;
4. worker/system callback;
5. internal health/diagnostic route.

Every human platform operation records:

- stable operation ID;
- required capability;
- permitted boundary kinds;
- resource resolver/gate;
- audit event;
- execution policy (request, Worker, or Platform Job).

The inventory is checked into tests or generated evidence so unclassified new
routes fail CI.

### Central evaluator

Create one dependency/service used by REST handlers and the thin MCP/CLI
adapters. Handlers request an operation by stable catalog ID; they do not
manually reproduce role or boundary logic. Domain repositories retain resource
access rules.

The final human route path must not contain:

- `CurrentSuperuser` as its authorization decision;
- `user.is_superuser` or `is_platform_admin` bypasses;
- implicit provider-organization power;
- `legacy_allowed or has_new_scope` fallbacks;
- a Builder-only permission evaluator.

Development may use a temporary read-only comparison report that evaluates old
and new decisions over fixtures. It must be deleted before merge.

## 8. Builder architecture

### One maintained coding profile

Replace per-Solution coding Agents with one platform-maintained coding profile.
It owns the transport-neutral `bifrost-build` Skill and orchestration behavior,
not a static list of every platform tool.

The shared Chat harness does **not** require an Agent: `Conversation.agent_id`
and `ConversationCreate.agent_id` are nullable, and `AgentExecutor.chat()`
already accepts `Agent | None`. Builder currently forces an Agent only to reuse
its system prompt, tool list, iteration ceiling, and token budget. Remove that
coupling rather than creating a hidden adapter Agent.

Generalize the shared executor around a typed runtime profile containing
instructions/Skill, model profile, request/token ceilings, and tool provider.
A normal Agent adapts its stored configuration into that profile; Builder
supplies the maintained coding profile directly. Builder conversations remain
agentless and retain Builder session/target identity. `SolutionBuilderTurn` and
its PlatformJob remain the durable run records.

Remove `ensure_builder_agent()` and its Solution-specific identity once
existing sessions are migrated. Remove `builder_agent_id` from Builder DTOs and
clear the obsolete conversation association before deleting orphaned generated
Agent rows. Existing Builder conversations retain their history, attachments,
artifacts, workspaces, and attribution.

Runs record provenance—the Bifrost release, Skill digest, operation-catalog
revision, runner image, and selected model—but administrators do not manage a
second set of “coding profile versions.”

### Session target, not Agent identity

A Builder session selects one target:

| Target | Required authority | Writable result |
|---|---|---|
| Private Solution | `builder.execute`, `solutions.readwrite`, `solutions.build.execute`, `solutions.deploy.execute`, and owner/edit access | New inert, immutable Solution revision plus its fenced private preview; portable declarations inside it do not mutate shared organization resources |
| Organization workspace | `builder.execute`, coverage through an Organization, Organization group, or Managed organizations selection, and relevant domain capabilities | Loose organization-owned Agents, forms, tables, workflows, apps, and related entities |
| Global workspace | `builder.execute`, Platform boundary, relevant domain capabilities, and `repository.readwrite` for source changes | Reviewed proposal/diff applied to `_repo` and global entities |

The existing `global_repo` snapshot/proposal/diff/apply/rollback machinery is
retained. Its hard-coded Platform Admin gate is replaced by the Platform
boundary plus explicit capabilities.

### Private authoring and promotion

Private authoring and publication are deliberately split:

“For themselves” does not require a Personal boundary. The private Solution is
still associated with the person's home organization for tenancy and billing,
while `owner_user_id`, visibility, and collaborator grants form the resource
gate. Its contents remain inert source until private preview or promotion.

1. A Builder creates and validates a private Solution revision. Ownership or an
   edit collaboration grant is the resource gate.
2. The owner requests promotion of one pinned, green revision. Requesting does
   not grant publication authority.
3. A reviewer with `solutions.publish.read` covering the source
   organization can inspect the pinned review without gaining access to the
   owner's unrelated private work.
4. Publishing requires `solutions.publish.execute` covering the destination
   boundary. An organization destination may be covered directly, through a
   group, or through Managed organizations. A global destination requires a
   Platform selection.
5. The controlled promotion operation creates or updates the shared Solution
   and materializes its reviewed bundle. It does not require the publisher to
   hold every domain `readwrite` capability because the promotion capability is
   the explicit authority to install that validated bundle as one unit.
6. Extra choices require their own capabilities: creating/assigning roles
   requires `roles.readwrite`; delegating global `_repo` runtime access requires
   `repository.access.readwrite`; connection/config approval uses the relevant
   integration/config authority.

Platform does not imply source visibility across customers. A person publishing
customer work globally therefore needs `solutions.publish.read` at source
coverage (for example Managed organizations) **and**
`solutions.publish.execute` at the Platform destination. One multi-boundary
Role assignment may contain both.

There is no seeded Publisher role. **Publish Solutions** is an independently
assignable capability that an administrator may add to an existing or custom
Role. Default Builder does not contain it. Platform Admin receives it through
the wildcard.

`repository.readwrite` is not required for ordinary Global promotion: promotion
writes a shared Solution release, not `_repo`. It is required only for direct
Global workspace source changes. `repository.access.readwrite` is required when
the reviewer explicitly grants the promoted runtime access to `_repo`.

### Domain and repository intersection

- A Solution-owned workflow needs `workflows.readwrite` and Solution access;
  it does not need repository authority.
- A loose repository-backed workflow needs both `workflows.readwrite` and
  `repository.readwrite` because it changes domain registration and source.
- Executing either workflow needs `workflows.execute`.
- Creating an inline Agent or form in an Organization target does not gain raw
  repository access merely because Builder is being used.

### Dynamic tools

Capability discovery and execution both enforce the same context:

- the Builder sees only catalog operations permitted for its target and
  current effective authorization;
- execution rechecks instead of trusting the discovery result;
- target IDs and organization IDs are server-bound, not model-selected escape
  hatches;
- MCP, CLI, and Builder adapters call the same REST/domain operation;
- tool progress, attachments, ArtifactRefs, generated artifacts, usage, and
  compaction continue through the merged Pydantic AI/Chat harness.

The existing Worker handles local coding turns and app compilation. Cloudflare
uses the same runner envelope and `ghcr.io/gobifrost/bifrost-build` image.
Platform Jobs retain durable orchestration, status, retries, cancellation, and
deployment/apply work; no new container or feature-specific job system is
introduced.

### Code-execution isolation

Path-confined file tools are not a security boundary for arbitrary commands.
The existing local Worker must not expose a shell merely by adding a working
directory, timeout, reduced environment, or output cap. The empirical
bubblewrap findings in
`docs/superpowers/specs/2026-04-27-chat-v2-sandbox-bwrap-findings.md` remain
authoritative: strong nested local isolation needs explicit host/container
seccomp or AppArmor support and a passing preflight. There is no silent weaker
fallback.

Cloudflare command execution uses two ephemeral Sandbox instances within the
same durable Workflow attempt:

1. The **runner Sandbox** executes the shared Python/Pydantic AI harness from
   `ghcr.io/gobifrost/bifrost-build`. It may receive the one-attempt Bifrost
   capability and decrypted model credential, but it never executes generated
   commands.
2. The **workspace Sandbox** holds the hydrated workspace and executes file and
   command tools. It receives no model credential, Bifrost capability, caller
   token, Cloudflare token, or general platform credential.
3. The provisioned Cloudflare Worker is the authenticated broker between them.
   The runner receives only a short-lived, job-bound broker capability. The
   Worker resolves the fixed workspace Sandbox ID, validates the requested
   tool against the server-issued definition, bounds input/output/runtime, and
   invokes the Sandbox SDK. IDs, paths, network policy, and resource limits are
   not model-selected escape hatches.
4. The Worker hydrates and extracts the input archive into the workspace
   Sandbox and returns the final bounded archive to Bifrost. The job capability
   stays in the Worker/runner trust side and is never written into the
   workspace Sandbox.
5. Workspace internet access is disabled by default. When an authorized policy
   enables it, Cloudflare's Sandbox egress policy uses an allowlist; the UI and
   audit log show the effective network mode. The workspace never receives
   provider credentials merely because network is enabled.
6. Both Sandboxes are destroyed after the terminal callback. Cancellation,
   progress, usage, retries, and terminal state remain the existing Platform
   Job/runner contracts.

Cloudflare documents that processes within one Sandbox share filesystem,
process, and network state, while separate Sandboxes are isolated. Therefore
running arbitrary code beside the secret-bearing Pydantic process is forbidden.
This two-Sandbox arrangement adds ephemeral Cloudflare compute, not another
hoster-managed Docker service or another coding harness.

Cloudflare references used for this boundary: [Sandbox security
model](https://developers.cloudflare.com/sandbox/concepts/security/),
[container isolation](https://developers.cloudflare.com/sandbox/concepts/containers/),
and [outbound traffic policy](https://developers.cloudflare.com/sandbox/guides/outbound-traffic/).

Local execution continues to use the same semantic tool/runtime contracts.
Safe file operations and the fixed-contract app build remain available on the
existing Worker. Arbitrary command tools are advertised locally only after a
strong sandbox provider passes preflight. It is acceptable for secure command
execution to be Cloudflare-only initially; it is not acceptable to substitute
an unsandboxed local command path.

## 9. UI behavior

### Roles and assignments

- Role editor describes capabilities only; it never asks for `.all` or Global
  variants of each scope.
- User/role assignment provides a multi-select for individual organizations,
  named organization groups/pods, Managed organizations, and Global. The Role's
  capability bundle is shown once.
- Selecting the default Platform Builder explains that it grants both Managed
  organizations and Global through two boundary selections; neither selection
  implies the other.
- Effective-access details show the contributing role, boundary, capability,
  and resource grant in plain language.
- Platform Admin is visibly immutable. Mutable default roles can be copied or
  adjusted without changing the platform-owned template contract.

### Context and support

- Normal navigation defaults to the person's own work and explicitly shared
  work.
- Authorized MSP staff can switch to customer context and use **All customer
  work**, then filter by organization, owner, status, and search.
- Global is a deliberate context, never mixed into ordinary customer lists.
- Support edits show the actual operator in activity history and “last updated
  by”; there is no invisible identity substitution.

### Builder target selection

- “Start building” offers only targets for which the caller has
  `builder.execute` and an applicable boundary assignment.
- Organization selection lists only authorized organizations.
- Global workspace appears only with the Platform boundary and required
  capabilities.
- An existing Solution opens its durable sessions, transcript, artifacts,
  revision, build state, and preview state. Starting another session does not
  create another coding Agent.
- AI/runner setup gating, connectivity diagnostics, loading/restore animation,
  and preview behavior remain as already designed.

### Portable usage governance

- Enforcement uses provider-neutral quantities Bifrost can measure across
  runtimes: model requests, input/output/cache tokens, canonical total tokens,
  runner duration, and sandbox compute duration. Provider-reported dollars are
  observed accounting, not the sole quota unit.
- Per-run policy precedence is Solution, user, organization, then platform;
  the first configured level wins. Aggregate daily/monthly ceilings remain
  cumulative, so every configured Solution, user, organization, and platform
  owner must admit the projected usage.
- A stricter Agent or resource run ceiling intersects the winning hierarchical
  per-run policy; it never widens it.
- Policy administration reuses `metrics.read` and `metrics.readwrite` at an
  exact selected boundary. Managed organizations is a support collection, not
  a mutation identity. Policy changes are recorded in the Audit Log.
- Builders can see their own effective limit, current usage, and percentage,
  plus the effective status of a Solution they can access, without receiving
  broad platform metrics visibility. Support/admin views can select admitted
  organization, user, and Solution targets.
- The UI explains which level supplied the active per-run policy and shows
  every cumulative aggregate ceiling. Undefined lower levels inherit rather
  than displaying a misleading zero. Empty policies are removed, not retained
  as no-op rows.

## 10. Migration and compatibility

### Forward-only data migration

1. Add boundary-aware role assignments with constraints and indexes.
2. Seed/update canonical capability definitions and default role templates from
   one source.
3. Backfill legacy superusers to Platform Admin at Platform.
4. Backfill existing provider-organization non-admin users to Platform
   Operator at Managed organizations.
5. Backfill ordinary users with an Organization Member assignment at their
   home organization, preserving their prior baseline behavior.
6. Bind custom-role assignments to the user's appropriate existing
   organization boundary unless an unambiguous broader legacy grant is
   documented.
7. Preserve explicit resource-role and collaborator grants.
8. Migrate `SolutionBuilderCollaborator` rows into direct Solution-user grants
   and add the parallel Solution-Role grant surface without widening access.
9. Migrate current Builder users to Builder at the appropriate boundary.
10. Do not auto-assign Platform Builder; its repository/global authority is an
   administrator decision.
11. Translate and then remove obsolete capabilities such as
    `organization.impersonation`, `solutions.build`, `.all`, and `.write`.
12. Migrate Builder sessions from per-Solution coding Agents to agentless
    conversations using the maintained runtime profile without changing
    conversation history.
13. Remove legacy columns/checks only after route inventory and parity gates
    pass.

Ambiguous users are emitted in a migration report and block release; the
migration must not silently broaden them.

### Rollout rule

There is no production dual-authorization period. We may compare decisions in
development and fixtures, but deployment performs the backfill and atomically
switches converted routes to the central evaluator. Rollback is a forward
repair migration or application rollback compatible with the migrated schema,
not restoration of old authorization branches.

## 11. Execution phases and gates

### Phase A — freeze the contract and inventory

Deliver:

- approve this document;
- generate the complete route classification;
- generate capability usage by route, operation, CLI, MCP, UI, and Skill;
- materialize the approved canonical nouns and lifecycle capabilities above
  into a complete operation-to-capability map;
- record every legacy privilege check and its replacement.

Gate: no operation, route, or legacy check is unaccounted for. No runtime
behavior changes yet.

### Phase B — canonical catalog and schema

Deliver:

- final capability registry and implication rules;
- RoleAssignment, boundary-selection, and Organization-group models/DTOs;
- new forward-only schema/data migration;
- default role templates from one canonical source;
- API contracts for assignment and effective-access explanation.

Delete/replace:

- the current uncommitted scope vocabulary;
- the two-column-only `UserRole` semantics;
- duplicate default-role scope arrays.

Gate: migration tests cover new, upgraded, ambiguous, external, and
multi-boundary users; catalog membership and DTO/contract tripwires pass.

### Phase C — principal resolver and evaluator

Deliver:

- typed authorization context;
- boundary resolver/switching contract;
- cached assignment resolution and invalidation;
- central operation authorization dependency;
- audit fields and Worker/PlatformJob reauthorization envelope.

Gate: a principal × boundary × capability matrix proves no cross-boundary
union, prompt revocation, and real-actor audit attribution.

### Phase D — one complete vertical slice

Convert Agents end to end: UI, REST, catalog, MCP, CLI, Builder discovery and
execution, Solution/resource gates, audit, and tests.

Gate: no legacy authorization remains in the slice; all surfaces make the same
allow/deny decision for the same context. Architecture review approves the
shape before broad conversion.

### Phase E — all human platform routes

Convert by bounded domain: forms/tables/files/policies; workflows/executions;
apps/Solutions/deployment; organizations/roles/integrations/configs; remaining
platform administration.

Gate for each domain:

- REST behavior and resource gates covered;
- CLI/MCP/native Builder parity updated;
- audit event registered;
- legacy checks removed, not bypassed;
- route classification remains complete.

Final gate: repository search and structural tests reject human legacy auth
patterns.

### Phase F — maintained coding profile

Deliver:

- typed maintained coding runtime profile;
- removal of per-Solution Agent creation/export;
- session migration and provenance recording;
- dynamic catalog tool discovery/execution through shared harness.

Gate: old conversations resume; new and existing Solutions start sessions
without new Agent rows; Skill/attachments/compaction/artifacts/usage/progress
all remain intact.

### Phase G — all Builder targets

Deliver:

- Solution, Organization workspace, and Global target creation;
- target-specific workspace adapters and validation;
- repository/domain intersection rules;
- reviewed global proposal/diff/apply path;
- exact operation filtering and reauthorization.

Gate: Builder can achieve the approved CLI/MCP operation parity without being
attached to a Solution, while every negative boundary/resource case fails.

### Phase H — role, context, support, and Builder UI

Deliver:

- boundary-aware role assignment UI;
- organization multi-select plus named group/pod membership management;
- default Builder and Platform Builder presentation;
- Solution sharing with direct people and Roles, including view/edit access;
- context switcher and customer/global list separation;
- effective-access explanation;
- Builder target picker and restored-session states;
- promotion review filtered by source coverage, with destination choices
  filtered independently by destination coverage;
- admin setup/diagnostics integration.

Gate: component tests plus focused Playwright journeys for ordinary Builder,
customer support operator, Platform Builder, Platform Admin, and denied user.
Run the UX review against the live worktree and close accessibility/loading/
empty/error-state findings.

### Phase I — cleanup, upgrade proof, and release evidence

Deliver:

- delete temporary comparison tools and old scope constants;
- delete legacy authorization helpers made unreachable;
- remove per-Solution coding Agent code and stale manifests;
- update administrator/user documentation;
- produce upgrade report and Builder behavior inventory;
- run scoped tests throughout, then the explicitly approved broad release
  matrix.

Gate: no known failure, no unexplained omission, no compatibility branch, and
no unclassified route. Do not merge without Jack's explicit approval.

## 12. Required verification matrix

At minimum, test each relevant operation across:

| Dimension | Cases |
|---|---|
| Principal | ordinary member, Builder, Platform Operator, Platform Builder, Platform Admin, external user, system/runtime principal |
| Boundary | home organization, directly assigned customer, group-assigned customer, Managed organizations customer, unauthorized customer, provider organization, Global |
| Resource | owned, shared edit, shared view, role-granted, policy-allowed, policy-denied, Solution-managed, absent |
| Surface | UI/REST, CLI, MCP, native Builder, Worker/PlatformJob continuation |
| Timing | immediate, after role change, queued before revocation, resume after restart |

Critical negative cases:

- Builder in customer A cannot discover or mutate customer B.
- Default Builder sees owned Solutions plus direct/Role-granted Solutions, not
  every private Solution in its organization.
- A Solution-Role grant works only while the user holds that Role in a boundary
  covering the Solution organization; removing either side revokes access.
- A private Builder can declare every supported Solution resource but cannot
  materialize it into an organization or Global without
  `solutions.publish.execute` at the destination.
- A person cannot review private publish requests outside their
  `solutions.publish.read` source coverage or publish into a destination not
  covered by `solutions.publish.execute`.
- Global promotion requires both source coverage and Platform destination
  coverage; Platform alone does not expose customer-private requests.
- Ordinary Global promotion does not require `_repo` authority, while enabling
  Global repository runtime access requires `repository.access.readwrite`.
- Adding/removing an organization from a named group grants/revokes its
  coverage without changing the Role's capability bundle.
- Managed organizations does not expose Global or provider-org resources.
- Platform boundary without `repository.readwrite` cannot change `_repo`.
- Repository authority without a domain capability cannot register/change that
  domain entity.
- `readwrite` does not permit execution.
- a view-only Solution collaborator cannot write through Builder, MCP, or CLI.
- a revoked queued job cannot publish/apply later.
- support work records the operator, not the resource owner.
- external users do not inherit authenticated/global visibility.
- old sessions resume without exporting a coding Agent into the Solution.

Required contract checks include operation-catalog completeness, capability
membership, CLI/MCP thin wrappers, DTO parity, contract-version tripwire,
generated Skill freshness, manifest round trips where applicable, migration
upgrade tests, API quality, client type/lint, focused Vitest, backend e2e, and
focused Playwright. Exact commands and broader suites not run must be reported
at every handoff.

## 13. Definition of complete

This program is complete only when:

- capability names contain no reach and use the approved verb vocabulary;
- Global exists only as the Platform boundary label;
- one Role assignment can select individual organizations, organization
  groups, Managed organizations, and Platform independently;
- Builder and Platform Builder defaults behave as defined above;
- upgraded users retain intended access without runtime legacy fallbacks;
- every human platform route uses the central evaluator;
- every route has an explicit classification;
- resource and policy gates remain effective on every surface;
- native Builder can target Solutions, organization-owned loose resources, and
  the Global workspace according to the caller's authority;
- native Builder, MCP, and CLI share the catalog and domain behavior;
- one maintained coding profile replaces per-Solution coding Agents;
- conversations, artifacts, compaction, usage, worktrees, previews, builds,
  deployments, local Worker execution, and optional Cloudflare execution still
  work;
- UI makes boundary, effective access, support identity, loading, and denial
  states understandable;
- migrations are forward-only and upgrade tests pass;
- obsolete authorization code and temporary comparison code are deleted;
- the full inventory and verification evidence is reviewed before merge.

## 14. Current branch disposition

The working tree currently contains an uncommitted Phase 5 patch. Do not commit
it as designed.

- **Retain:** the operation catalog's declared-capability membership validation
  concept; the policy-rule MCP organization-selector fix and its parity tests.
- **Rewrite:** capability names, catalog declarations, scope tests, and role UI
  expectations against this plan.
- **Preserve:** all previously committed Builder reconstruction, Pydantic
  AI/Chat harness reuse, Skill hydration/parity work, operation catalog work,
  Worker/Cloudflare envelope, Platform Jobs, private Solution collaboration,
  global workspace proposal machinery, and UI restoration behavior.

No commit, push, PR, rebase, or merge is part of approving this document.
