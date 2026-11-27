# Step 3 — Design: is the shape right?

Three checks against the same proposed design. Each must pass; a fail in any stops the
path. Skip a check only when it truly does not apply, and say why in one line.

## 3a. Standards

- Prefer an open, widely supported standard over a provider-specific design: OIDC/OAuth
  2.x, workload identity federation, SCIM, SAML, OpenTelemetry, S3 API, webhooks with
  signed payloads, OpenAPI, cron, etc. If a provider-specific design is proposed, name the
  standard that covers it or explain why none does.
- Prefer a standard over gluing it together from Bifrost primitives when the standard is
  what the outside world speaks (e.g. SCIM for user/role provisioning vs a workflow that
  syncs users). Primitives are fine for Bifrost-internal composition.
- Exceptions need a stated reason (no standard exists, standard can't express the need,
  measured cost).

**Credential scope.** A credential keeps the purpose it was issued for. Do not widen a
key, token or secret to new operations (e.g. a workflow endpoint key that only invokes
one workflow must not also register or delete workflows). Machine and CI access goes
through Bifrost identities and their permissions (client credentials, workload identity
federation), not by stretching another credential.

Precedent: #1167 keyless outbound auth. A provider-specific credential design was
redirected: give concrete use cases, before/after workflow code, and compare against
existing mapping configuration plus a shared SDK helper before adding a core credential
type. A Bifrost OIDC issuer is a separate design question.

## 3b. Tenancy

Every design states how it behaves across scopes before it is accepted:

- Which scope owns it: global, provider org, customer org, user? Does it cascade
  (org value overrides global) the way Config/Tables/OAuth do?
- Who can configure it, and for whom? Check the resource’s existing scope resolution,
  permissions, and any explicit provider or admin bypass.
- How does an MSP set this up for 50 customer orgs — once, or 50 times? If it is
  tedious per org, the design is wrong.
- Data must never cross tenant boundaries; name the query/repository that enforces it.
  Reference: `api/src/repositories/README.md` (OrgScopedRepository, scope resolver).
- Exception: when each tenant must grant something themselves (OAuth admin consent,
  a customer's own app registration), per-org setup is correct. Make each org's step
  as small as possible and say why it cannot be done once.

## 3c. Compatibility and surfaces

- Who consumes this today: apps, workflows, Solutions, SDK, CLI, MCP, API clients?
  Name them and what changes for each.
- Upgrades: does any supported install need a manual step, migration, or new config?
  Claims of "no behavior change" need evidence from the contracts and consumers.
- Never assume installs pass through every release. Schema removals use
  expand/contract: stop using it in code now, drop it in a later release, and state
  what happens to an install that skips releases.
- Data repair on upgrade fails loudly on rows it cannot fix; it never silently skips.
- No pre-release runtimes or dependencies (alpha/beta/rc Python, Node, libraries).
- Permissions/defaults changes are breaking regardless of label; state them.
- Surface parity: CLI, MCP and manifest export share DTOs; a new field or entity must be
  reachable from all intended surfaces (`api/bifrost/dto_flags.py`).
- Durable background work uses PlatformJob, not a new job system
  (`docs/architecture/platform-jobs.md`).

Precedent: preserve supported installs without customer changes (#1235 — compatibility is
a demonstrated condition, not a promise).
