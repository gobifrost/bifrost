# SCIM Lite (showcase)

Global install, org-overridden. One install serves the provider org and every
tenant org: per-org `scim-idp` integration mappings (tenant anchor + secrets)
and per-org `SCIM_GROUP_ROLES` values cascade over global defaults. Shared
tables key rows by `{org_id}:{external_id}` so one registry serves all orgs.

Two sync paths, one shared provision core (`modules/scim_provision.py`):

- **Trickle:** webhook `scim_user_event` — one IdP event, one user, org from `context.org_id`.
- **Bulk:** scheduled/manual `scim_pull` — walks every org mapping (or one
  with `org_id=`), pages each IdP, reconciles through the same core;
  `full=true` deactivates leavers. Per-org cursors, per-org failure
  containment: one org's bad page never stops the others.

Inactive never-provisioned users are ignored (nothing to deactivate); rehire
works via an org-guarded email fallback that never hijacks another org's user.

## Wire-up (per-org installs; code ships once)

```bash
bifrost solution deploy --org "Customer Org"   # install + tables + workflows, org-scoped
bifrost config set SCIM_GROUP_ROLES '{"support-tier1":"Support"}' --type json --org "Customer Org"

# IdP credential lives in an integration mapping (per-org tenant anchor):
bifrost integrations create --name scim-idp --description "Customer IdP for SCIM pull"
bifrost integrations add-mapping scim-idp --organization "Customer Org" \
  --entity-id "<tenant-or-realm>" \
  --config '{"base_url":"http://idp:8471","dialect":"scim-lite","api_token":"<secret>"}'

# Trickle: org webhook source (generic adapter) + subscribe the ORG install's
# scim_user_event UUID (portable refs can resolve to the wrong install).
# Bulk: org schedule source (cron) + subscribe the ORG install's scim_pull UUID.
```

Dialects: `scim-lite` speaks the fake-IdP `GET /users?cursor&limit`
(`local_idp/serve.py`). `keycloak` speaks the Admin API with
`base_url=http(s)://<host>:<port>/admin/realms/<realm>` and an admin
access token as `api_token`.

## What it proves

- Webhook → `scim_user_event` → `users.create/update` + `scim_identities` mapping table (idempotent retries via upsert on IdP `external_id`).
- Groups resolve to roles through the `SCIM_GROUP_ROLES` config, grants and revocations enforced (revocation uses the platform `DELETE /api/roles/{id}/users/{id}` endpoint via the generic `bifrost.api` client, since the typed `roles` SDK has no unassign).
- Deactivate (`active: false`) disables the user but keeps the mapping row for rehire.

## What it does not do (by design)

- No per-org IdP connections, no SAML, no full SCIM server (filter queries, pagination). Those need platform work; this shows how far a workflow gets.
- Unattended runs need per-org installs: global-install webhook/scheduled runs execute as an org-less identity with no reach into org scopes, so trickle sources, schedules, and installs are per org. Code still ships once.

## Group → role mapping

`SCIM_GROUP_ROLES` is per-org (`{"idp-group": "Bifrost Role"}`): each tenant
maps its own IdP group names onto the global role catalog, so the same group
name can mean different roles in different orgs and one role can collect
members from many groups. Unmapped groups are dropped; mapped names that match
no role are reported in `unknown_roles` instead of silently skipping. One
email lives in exactly one org (platform constraint) — a same-email create
in a second org fails loudly per item (`409`) rather than hijacking.

Multi-org grants (`{"role": "Leads", "orgs": ["A", "B"]}`) are implemented
(read-modify-write via `PUT role-assignments`, boundary-only revokes) but
capped by caller reach: an org-scoped unattended run cannot resolve or grant
into orgs it can't see, so cross-org boundaries surface as
`unknown_roles: ["Leads@<org>"]` instead of granting. Cross-org boundary sync
needs a privileged caller (provider-admin-executed pull); home-org grants
work fully unattended. Do not assume multi-org boundaries in scheduled runs
until that caller exists.

## Skim — the admin face

`apps/skim` ("skim the IdP, sync the org"): one screen per org for the two
things that change per tenant — the `scim-idp` mapping (tenant anchor,
endpoint, dialect, domain, token-as-secret) and `SCIM_GROUP_ROLES`. Reads via
`scim_onboarding_status`, writes via `scim_onboarding_save` (validates first,
loud on bad input). App + both workflows are `role_based` on builtin
Platform Admin; the UI additionally gates on `RequireRole`. Secrets never
reach the browser (presence flag only).

## Local exercise (no IdP account)

```bash
python3 -m pytest scim-lite/tests/ -q
python3 local_idp/send.py hire           # trickle payload
python3 local_idp/serve.py 8471         # pull delta endpoint
```
