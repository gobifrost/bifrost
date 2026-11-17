# Identity providers: SCIM provisioning and group mapping

## Outcome and scope

Give every organization, the provider organization included, one or more
**identity provider connections** that keep Bifrost users and role
assignments in step with an external directory. NinjaOne's identity provider
screens are the UX reference: a list of connections, a detail page with
provisioning status, credential expiry and a group-mapping table, and an edit
dialog with a left-hand section nav.

Delivery happens on the MTG contribution fork first. The upstream RFC issue
comes later and carries the open questions listed at the end.

In scope:

- Organization domain claims, verified by DNS TXT. An organization can claim
  several domains.
- A provisioning core that does not depend on the protocol. SCIM 2.0
  (RFC 7643 / 7644) is the first way in; Google Directory sync is the second.
- Group-to-role mapping, revocation and an "Identity Source" for each user.
- Platform events so workflows can react to provisioning.

Out of scope for v1:

- **Sign-in stays instance-global.** The existing Microsoft / Google / OIDC
  configuration does not change. Connection records are shaped so per-IdP
  sign-in can move into them later; that is a question for the RFC.
- **SAML.** OIDC covers sign-in. Google's custom SCIM profile hangs off a SAML
  app in Google Admin, but that app is only a container, so users still sign
  in through Google OIDC.
- **Customer-side workarounds** that federate Google into another IdP. Rejected.

## Current seams

- `User.organization_id` gives each user one home organization, and
  `User.email` is unique across the instance
  (`api/src/models/orm/users.py`).
- `Organization.domain` is one unverified string per org. Login
  auto-provisioning (`api/src/services/user_provisioning.py`) and the
  password-registration check read it. Verified claims are a separate model
  and do not change that path in v1.
- A `user_roles` row is unique per `(user_id, role_id)`. Its scope comes from
  `user_role_boundaries`. Nothing records where an assignment came from:
  `assigned_by` is free text.
- Changing `is_active`, `email`, `organization_id` or `base_role_id` fires the
  deferred `authz_epoch_upd` trigger. Existing access tokens then fail with
  `refresh_required`, and refresh rejects inactive users. Deactivating a user
  therefore revokes access with no extra session bookkeeping.
  `revoke_all_human_sessions_in_transaction` is still called on deprovision
  for an immediate, explicit cut-off.
- The user-lifecycle helpers in `api/shared/sdk_users.py` all take a user
  `Caller` and enforce its permissions. A SCIM request is a machine caller with
  connection-scoped authority, so the provisioning core writes through
  narrow, purpose-built helpers. It reuses the guards
  (`assert_role_assignment_allowed_for_user`, `insert_assignment`,
  `set_user_base_role`, `role_cache.invalidate_user`) without inventing a
  system `Caller`.
- `emit_audit` skips records with no actor. Provisioning passes an
  `ActorContext` that names the connection.
- Platform events: topic `EventSource`s, `emit_event`, and
  `CURATED_TOPICS` (`api/src/services/events/registry.py`).
- Hashed bearer secrets: `user_invites` (unique `token_hash`, `expires_at`,
  `revoked_at`) and workflow keys (`last_used_at`) are the templates. SHA-256
  of `secrets.token_urlsafe(32)`, looked up by hash.

## Decisions

| # | Decision |
|---|---|
| 1 | Connections exist per organization. The provider org is just another org. Customer orgs and technicians use the same model. |
| 2 | v1 is provisioning plus group mapping per org. Sign-in stays instance-global. Consolidating sign-in into the connection goes into the RFC. |
| 3 | A connection may only create or change users whose email domain is a **verified** claim of the connection's org. It never touches a user whose home org is a different one. Claims are verified by DNS TXT. An org can claim several domains, and a domain can be verified by only one org. |
| 4 | An existing user in the same org (for example, someone invited earlier) is **adopted** by email, then matched by `externalId` from then on. A user who belongs to another org gets `409 uniqueness`. |
| 5 | `active=false` disables the user and revokes sessions. `DELETE` disables and unlinks; it does not hard-delete. One exception: a user the connection created who has **never signed in** has no history to keep, so `DELETE` removes them permanently. This handles Google's create, update and delete connection test. Losing the last mapped group revokes access (decision 7). |
| 6 | New users get the organization's default base role (`shared.base_role_defaults.resolve_base_role`). Groups can optionally map to roles (roles are global). Provisioning removes only roles that provisioning itself granted. Mapping can never grant Platform Admin or any other role the account guards refuse. |
| 7 | Once a connection has at least one group mapping, a user needs at least one mapped group to stay active. A mapping with no roles means "access only". Sources that push no groups (the Google SCIM beta) have no mappings, so this rule does not apply to them. |
| 8 | One active bearer token per connection, stored hashed, with an expiry. Rotation creates a new token and shortens the previous token's expiry to **one hour** from the rotation, so the old and new tokens overlap briefly. |
| 9 | Keep all the data. The full SCIM payload, enterprise extension included, is stored on the user's link. Odd vendor extensions are normalized or ignored as they come up. |
| 10 | Provision / update / deprovision / group-membership changes emit platform events. |
| 11 | External (portal) users: a per-connection **Provision as External users** setting, off by default. It applies to users the connection creates; an adopted user keeps their current setting. An External user (`User.is_external`) keeps the same org cascade but is not covered by `access_level="authenticated"` sharing; they see only what a role or an explicit grant gives them. |
| 12 | The provisioning core is protocol-independent. Connections have `source_type` = `scim` \| `google_directory`. The UI is the same for both: groups seen, "N/N mapped", last sync, credential expiry. |

## Data model

All new tables are identity entities (see `api/src/repositories/README.md`).
They are looked up by ID within their org and are never resolved by name with
cascade.

- `organization_domains`: `id`, `organization_id` (FK, cascade), `domain`
  (lower-case), `verification_token`, `verified_at`, `last_checked_at`,
  `created_by`, timestamps. Unique `(organization_id, domain)`, plus a partial
  unique index on `domain WHERE verified_at IS NOT NULL`.
- `identity_providers`: `id`, `organization_id` (FK, not null), `name`,
  `source_type`, `vendor` (`entra` \| `okta` \| `jumpcloud` \| `google` \|
  `other`, for display only), `is_enabled`, `provision_as_external`,
  `settings` JSONB, `last_request_at`, `last_sync_at`, `created_by`,
  timestamps.
- `identity_provider_tokens`: `id`, `identity_provider_id` (FK, cascade),
  `token_hash` (unique), `token_hint` (last 4 characters, for display),
  `expires_at`, `revoked_at`, `last_used_at`, `created_by`, `created_at`.
- `identity_provider_users`: the link. `id` is the SCIM `id`, kept stable for
  the IdP. Other columns: `identity_provider_id` (FK, cascade), `user_id` (FK,
  unique, so a user has one identity source), `external_id`, `user_name`,
  `active`, `attributes` JSONB (the last full payload), `created_user`
  (whether this connection created the user), timestamps. Unique
  `(identity_provider_id, external_id)`.
- `identity_provider_groups`: `id` is the SCIM `id`. Other columns:
  `identity_provider_id`, `external_id`, `display_name`, `attributes` JSONB,
  timestamps.
- `identity_provider_group_members`: `(group_id, identity_provider_user_id)`.
- `identity_provider_group_roles`: `(group_id, role_id)`. A "mapping" is a row
  in `identity_provider_group_mappings` (`group_id` PK, `created_by`,
  `created_at`), and its roles hang off it, so an access-only mapping is a
  mapping with no roles.
- `user_roles.identity_provider_id`: nullable FK, `ON DELETE SET NULL`. Set
  when provisioning created the assignment. If the user already held the
  role, nothing is recorded, so a manual grant always survives. Clearing it is
  how a manual edit or a deleted connection takes ownership.

A user's **Identity Source** is the linked connection's name, or "Native" if
the user has no link.

## Provisioning core (`api/src/services/identity_provisioning/`)

Inputs are protocol-neutral commands: upsert user, set active, delete user,
upsert group, set or add or remove group members, delete group. Every command
runs inside the request transaction and:

1. Checks the domain claim (decision 3) before any create or email change.
2. Resolves the user by link, then by `externalId`, then adopts by email in
   the same org (decision 4).
3. Writes user fields through narrow helpers. The IdP owns `name`, `email`
   and `is_active` for linked users; the UI shows them as managed.
4. Recomputes access and roles for the affected users: desired roles = the
   union of mapped-group roles. It inserts missing roles, tagged with the
   connection, and deletes tagged roles that are no longer wanted. Active =
   `link.active` and (no mappings, or at least one mapped group).
   **Revoke now, grant later** (see below).
5. Emits audit and events after the commit.

### Batched grants (the access pass)

Every role or activation change replaces the single global authority epoch,
which retires every access token on the instance. Each SCIM request is its
own transaction, so a 2,000-user initial sync applied request by request
would do that about 2,000 times in 40 minutes — far past the authority
runbook's 30-an-hour threshold, and fast enough that a running workflow's
three refresh-and-replay attempts could all lose the race.

- **Revocations apply in the request:** deactivation (`active=false`,
  DELETE, losing the last mapped group, with sessions revoked) and removing
  roles the connection granted.
- **Grants wait:** activation, new role assignments and display-name changes
  are recorded on the link (`access_pending_since`, with the provider's
  `display_name`). The SCIM resource the provider reads back comes from the
  link, so it sees no difference.
- **The access pass** (`identity_provider_access_pass`, every 2 minutes)
  applies up to 500 waiting links, oldest first, across all connections in
  one transaction, skipping users a request holds (`SKIP LOCKED`). Each link
  runs in its own savepoint; one that fails rolls back alone and goes to the
  back of the queue. Caches are invalidated and events sent after the
  commit. Grants cost at most one epoch change per run (30 an hour);
  revocations, email changes and deletions still change it per request, so
  they count separately against the runbook's threshold.
- **Immediate where an admin acts:** a mapping change in the UI applies
  grants in its own single transaction.
- New users are inserted in their final state. An insert doesn't change the
  epoch, so on a connection without mappings, creating a user costs nothing.
- Email changes stay in the request: they need the synchronous uniqueness
  and domain checks, and they are rare.
- A grant refused by policy (e.g. a privileged role mapped for an External
  user) is skipped and audited (`identity_provider.role_grant_refused`); the
  next mapping or directory change for that user tries again.
- **Locking.** Every provisioning write takes a per-connection policy lock
  (a transaction-scoped advisory lock, shared); a mapping change takes it
  exclusively. A user created while the first mapping is added therefore
  can't be missed. After that lock, users are locked in id order, and links,
  memberships and assignments are read fresh. Membership changes to one
  group also lock the group row. Deleting the last mapped group reconciles
  every linked user, since the mapped-group requirement lifts for all of
  them.
- **Events.** `user.groups_changed` reports the membership change only.
  `user.access_changed` reports applied access (`activated`, `roles_added`,
  `roles_removed`) when the request revokes and when the pass grants.

The lasting fix is per-subject epochs (the `authz_versions` table is already
keyed by subject); that belongs to the upstream RFC.

The Google Directory sync (phase 4) calls the same commands with a full
snapshot. It adds snapshot-only guards: never deprovision after a failed or
partial fetch, and refuse a sync that would remove an unusually large share of
users at once.

## SCIM 2.0 endpoint

The base URL (the "Tenant URL" in Entra) is
`/api/scim/v2/{identity_provider_id}`. Requests carry
`Authorization: Bearer <token>`, which must belong to that connection, must
not be expired or revoked, and the connection must be enabled. Responses use
`application/scim+json`, with SCIM `Error` bodies.

- Discovery: `ServiceProviderConfig`, `ResourceTypes`, `Schemas`.
- `Users`: list with filters, `POST` (returns `201`), `GET`, `PUT`, `PATCH`,
  `DELETE`.
- `Groups`: the same set, plus `excludedAttributes=members`.
- Filters: only what the IdPs send, `userName|externalId|id eq` on Users and
  `displayName|externalId|id eq` on Groups. Anything else gets
  `400 invalidFilter`. Paging uses `startIndex` / `count`.
- Not supported (advertised in `ServiceProviderConfig`): bulk, sort, etag,
  changePassword.
- PATCH dialects: Entra's capitalized ops, string booleans (`"False"`),
  pathless value objects and `members[value eq "…"]` removals, plus Okta's
  pathed and unpathed forms.
- Hardening: CSRF exemption for the prefix, a per-connection rate limit
  (`src/core/rate_limit.py`) and a body-size cap.

Libraries:
- `scim2-models` (Apache-2.0, Pydantic v2, pinned to `0.12.x`) validates
  resources, applies `PatchOp` requests (`PatchOp.patch`) and parses filters
  (`ScimFilter`, which brings in `lark`). Requests are normalized first for
  quirks the library rejects: Entra's bare-string `manager`, and
  Okta's `remove members` with a value list. Vendor extensions with no
  schema are kept verbatim and patched directly.
- `dnspython`, now a direct dependency (it was transitive), for TXT lookups.
- `scim2-tester` (dev only) runs the RFC discovery checks and the Group CRUD
  checks in the e2e suite. User CRUD is covered by an Entra/Okta-shaped
  lifecycle test instead, because the tester invents random email domains,
  which the domain-claim rule correctly refuses.

## Management API and permissions

- New permission domain `identityproviders`, under "Identity & Access".
  `read` and `readwrite` are checked at the connection's organization.
- Group-to-role mappings also require that the caller could assign those
  roles at that org (the manual role-assignment privilege check). This stops
  a mapping from being used to escalate privilege.
- Routes:
  - `/api/identity-providers`: list, create, get, update, delete.
  - `.../{id}/tokens`: rotate; the token is returned once.
  - `.../{id}/groups`: list, with mapping status.
  - `.../{id}/groups/{group_id}/mapping`: upsert or delete.
  - `/api/organizations/{org_id}/domains`: list, add, verify, delete.
- Every route is registered in the operation catalog and the access list.
- `UserPublic` gains `identity_source` (`{ identity_provider_id, name,
  vendor }` or null).

## Events

Topics `user.provisioned`, `user.updated`, `user.deprovisioned`,
`user.groups_changed` and `user.access_changed`, registered in
`CURATED_TOPICS` with the standard `_base_body` envelope. The actor is the
connection.

## UI

- Users view gains an **Identity Providers** tab. The list columns are Name,
  Organization, Vendor, Verified Domains, Provisioning Status, Credential
  Expiry, Users and Groups Mapped.
- The detail page has:
  - an expiry banner (expired or within 14 days);
  - a provisioning card (status, Tenant URL with copy, token hint and expiry,
    Regenerate with a one-time reveal, last request);
  - a domains card (claims with TXT instructions and Verify);
  - a group-mapping table (group → mapping status → roles; edit).
- The edit dialog uses a left-hand section nav: General, Provisioning, Group
  Mapping.
- The users list gains an Identity Source column in both the wide and narrow
  layouts. Managed fields are locked on user detail.

## Phases

1. **Backend foundation (done):** migration and models, domain claims and
   DNS verification, connection and token management, the provisioning core,
   the SCIM endpoint, events, audit, the permission domain, and tests (unit,
   e2e, scim2-tester).
   - **Sign-in check (no code):** test Cloudflare Access for SaaS end to end
     as Bifrost's generic OIDC provider, with Prefer SSO on. If it works,
     that removes the double sign-in behind Access without trusting proxy
     headers, and the result goes in the docs. Proxy header trust stays out
     of scope: it isn't a standard, and every proxy does it differently.
2. **UI:** list, detail and edit dialog, the domains card, the token reveal,
   the mapping editor, and the Identity Source column; vitest plus one
   Playwright happy path.
3. **Credential expiry warnings (done):** an hourly scheduled task checks each
   enabled connection's current token. Superseded tokens are ignored, so the
   one-hour rotation overlap never raises a warning.
   - While a token is within 14 days of expiry or past it, the task keeps a
     platform-admin notification up with a Regenerate Token action.
     Notifications are Redis-backed and live an hour, which is why the check
     runs hourly.
   - It emits `identity_provider.token_expiry` once per stage (`expiring`,
     then `expired`), recorded on the token. A workflow can use it to reach the
     organization's own admins, who don't see platform notifications.
   - Rotating the token dismisses the outstanding warnings.
   - The list and detail pages flag the same states.
4. **Google Directory sync:** connections with `source_type="google_directory"`.
   The customer supplies a service account with domain-wide delegation and
   read-only Admin SDK scopes (`admin.directory.group.readonly`, plus
   `admin.directory.user.readonly` for a full user pull). A recurring
   scheduled **PlatformJob** per connection (see
   `docs/architecture/platform-jobs.md`) reads `groups.list` /
   `members.list`, plus an optional user pull for tenants without the SCIM
   beta (this also covers secondary domains), and feeds the same core with the
   snapshot guards. This is the only way to get Google group membership,
   because Google's OIDC ID tokens carry no groups claim.
5. **Sign-in group claims:** once the platform emits `user.signed_in` with
   the verified sign-in claims, groups in those claims feed the same core and
   the same group mappings. A connection can then cover a tenant without
   SCIM, and the mappings stay the one source of truth for group-to-role
   rules. On its own this only acts when someone signs in. Revoking access
   from someone who never signs in again still needs SCIM or a Directory sync.
6. **Upstream RFC**, per-organization sign-in first. Sign-in is
   instance-wide today: one provider of each kind, one Prefer SSO default and
   one callback URL. That blocks the common split of staff on one IdP and
   client organizations on plain Microsoft SSO. The RFC also covers retiring
   or keeping the legacy `Organization.domain` login auto-join in favour of
   verified claims, and whether customer organizations may manage their own
   connections.

## IdP notes

- **Entra ID** (non-gallery app):
  - Provisioning cycles run about every 40 minutes; use "Provision on demand"
    for testing.
  - It sends the PATCH quirks above.
  - It doesn't provision nested group membership: a user in a group nested
    inside an assigned group isn't sent. Map the groups people are directly
    in.
  - Validate with Microsoft's SCIM Validator.
- **Okta:** validate with Okta's SCIM test suite.
- **JumpCloud:** custom SCIM app.
- **Google Workspace custom SCIM** (limited beta; it works only where
  "Autoprovisioning" appears):
  - Users only: create, update, suspend (`active:false`) and `DELETE`, with
    configurable delays.
  - Bearer token only, and primary domain only.
  - No groups; group selection only limits which users get provisioned.
  - "Test connection" creates, updates and deletes a test user and expects
    `201`. The test user must pass the domain claim. The never-signed-in
    `DELETE` rule in decision 5 keeps it from leaving disabled users behind.

## Multiple connections per organization

An organization can have several connections. Each one has its own token,
groups and mappings, and revokes only the roles it granted. A user belongs to
exactly one connection: the first connection to provision or adopt them owns
them, and a second connection that sends the same person gets
`409 uniqueness`. Domain claims belong to the organization, so any connection
in the organization can provision any of its verified domains.

This works for disjoint populations, such as an organization migrating
between tenants or a merger where each tenant covers different people. It does
not merge identities that appear in more than one directory.

## Future direction (noted, not prioritized)

Design goal: stay usable for large organizations whose internal IT looks like
an MSP, such as an enterprise managing many Microsoft 365 tenants or business
units. Nothing below is scheduled. Each item is recorded so v1 choices do not
close it off.

1. **Scope a connection to domains.** Give each connection an optional
   allowlist drawn from its organization's verified domains, so two IdPs in
   one organization cannot compete for the same users.
2. **Hand a user off between connections.** Add an explicit "move to
   connection B" action that transfers the link and the roles the connection
   granted. Today a move depends on a `DELETE` from A followed by adoption in
   B, and that path is not specified or tested.
3. **Test two connections in one organization.** Cover the `409` for an
   overlapping user, and confirm that revocation by A leaves B's roles alone.
4. **One directory serving several organizations.** An enterprise may run one
   Entra tenant while modelling business units as separate Bifrost
   organizations. v1 binds a connection to one organization and lets only one
   organization verify a domain, so this would need routing from one
   connection to several organizations (by group or attribute) and a domain
   model that allows it. Revisit together with per-IdP sign-in in the RFC.

To keep these options open:

- Keep connection ownership explicit on every row that provisioning writes
  (links, groups, `user_roles.identity_provider_id`). Do not infer it from the
  organization.
- Keep the provisioning core protocol-neutral and per connection, so routing
  or merging can be layered over it later.
- Treat the unique `user_id` on `identity_provider_users` (one identity source
  per user) as a deliberate v1 constraint, not a permanent one. Multi-source
  identity would relax it.

## Open questions (for the RFC)

- Per-subject authority epochs. Provisioning is the workload most likely to
  cross the runbook's 30-bumps-an-hour threshold; the access pass caps it in
  v1, at the cost of grants waiting up to two minutes. A per-user version
  (or skipping the bump for users who hold no credentials) would let grants
  apply in the request again.

- Deleting a connection: the proposal is to unlink its users (they become
  Native) and convert the roles it granted to manual. The alternative is to
  require disabling or removing the users first.
- How often to re-check verified domains, and what happens when a TXT record
  disappears.
- Whether admins may override IdP-managed fields locally.
