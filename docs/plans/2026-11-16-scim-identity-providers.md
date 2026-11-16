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
| 11 | External (portal) users: a per-connection **Provision as External users** setting, off by default. An External user (`User.is_external`) keeps the same org cascade but is not covered by `access_level="authenticated"` sharing; they see only what a role or an explicit grant gives them. |
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
5. Emits audit and events after the commit.

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
- `scim2-models` (Apache-2.0, Pydantic v2, pinned) for resource and message
  validation and serialization.
- `dnspython`, now a direct dependency (it was transitive), for TXT lookups.
- `scim2-tester` runs RFC compliance checks in the e2e suite.
- No filter-grammar dependency; the supported subset is small and explicit.

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

Topics `user.provisioned`, `user.updated`, `user.deprovisioned` and
`user.groups_changed`, registered in `CURATED_TOPICS` with the standard
`_base_body` envelope. The actor is the connection.

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

1. **Backend foundation:** migration and models, domain claims and DNS
   verification, connection and token management, the provisioning core, the
   SCIM endpoint, events, audit, the permission domain, and tests (unit, e2e,
   scim2-tester).
2. **UI:** list, detail and edit dialog, the domains card, the token reveal,
   the mapping editor, and the Identity Source column; vitest plus one
   Playwright happy path.
3. **Credential expiry warnings:** a daily scheduled task that re-raises admin
   notifications (they are Redis-backed with a TTL) for tokens within 14 days
   of expiry.
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
5. **Upstream RFC:** per-IdP sign-in consolidation, retiring or keeping the
   legacy `Organization.domain` login auto-join in favour of verified claims,
   and whether customer orgs may self-manage connections.

## IdP notes

- **Entra ID** (non-gallery app):
  - Provisioning cycles run about every 40 minutes; use "Provision on demand"
    for testing.
  - It sends the PATCH quirks above.
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

## Open questions (for the RFC)

- Deleting a connection: the proposal is to unlink its users (they become
  Native) and convert the roles it granted to manual. The alternative is to
  require disabling or removing the users first.
- How often to re-check verified domains, and what happens when a TXT record
  disappears.
- Whether admins may override IdP-managed fields locally.
