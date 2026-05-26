# Org-Scoping Consolidation

**Date:** 2026-05-26
**Status:** Draft — pending approval
**Companion audits:**
- `docs/audits/2026-05-26-org-scoping-cascade-audit.md`
- `docs/audits/2026-05-26-caller-identity-audit.md`

---

## Problem

Organization scoping in Bifrost is scattered across five distinct cascade implementations, nine effective-scope resolvers, and eighteen ORM models with `organization_id` columns. The documented canonical pattern (`OrgScopedRepository` in `api/src/repositories/`) is bypassed by inline cascades in routers, parallel implementations in services, and hand-rolled queries in SDK endpoints. This has been "fixed" multiple times and continues to drift. The drift has shipped real bugs (the cross-tenant token leak in `IntegrationsRepository.get_provider_org_token`, the partial-hash cache invalidation bug, the unguarded `_get_cli_org_id` accepting any UUID).

**The goal of this plan is not to fix the next bug. The goal is to make it impossible for the pattern to drift again.** The OAuth and config bugs we know about today get fixed as a natural consequence of consolidation, not as the destination.

## Success criteria

The overhaul is complete when all of the following are true:

1. There is exactly one effective-scope resolver function, used by every entry point that needs one.
2. There is exactly one cascade implementation (the `OrgScopedRepository` base class), used by every execution-resolution entity.
3. Every ORM model with `organization_id` is classified in code as either execution-resolution (has a repository) or identity (explicitly exempt).
4. Mechanical tests fail CI if a new router introduces an inline cascade, or a new model with `organization_id` is added without classification.
5. The documented contract appears at every code surface a developer or agent would touch when working in this area.
6. The OAuth cross-tenant leak, the config cache invalidation bug, and the unguarded `_get_cli_org_id` are gone — as observable evidence the consolidation worked, not as standalone fixes.
7. All tests pass (unit, E2E, client) — `./test.sh all && ./test.sh client unit && ./test.sh client e2e`.
8. Manual end-to-end verification in the browser and via the SDK across three caller types: a regular org user, a platform org user, and a platform admin. For each, confirm:
   - UI listing pages (applications, forms, agents) show the expected entities — own-org + globals for org users; cross-org as expected for platform admins.
   - SDK reads from within a workflow resolve correctly — org users get their own org's data, platform admins can target any org by passing scope.
   - Cross-tenant isolation holds — an org user cannot surface another org's data by any means.
9. A neutral agent (fresh session, no prior context from this work) invoked with a prompt like "confirm your understanding of org scoping in this codebase" produces a sound answer derived from the documentation. If the agent gets it wrong, the docs failed and need iteration before the overhaul is considered complete.

---

## Trust model (anchoring assumption)

The engine sentinel is the security boundary. Specifically:

- The engine authenticates to the API as a single fixed superuser identity (`SYSTEM_USER_ID = "0000...0001"`).
- Each execution carries a `caller` field on its `ExecutionContext`, populated at execution start from the originating user / scheduled job / webhook event.
- The SDK (running inside the workflow process in the engine) reads `context.caller` and resolves the effective scope locally before making API calls.
- The API receives a resolved scope from the engine and trusts it, because the authenticated principal is the sentinel.
- **If the sentinel credential leaks, the entire org-isolation model collapses.** This is a known and accepted cost of the architecture, and must be stated plainly in the canonical doc.

Direct user-facing API calls (REST endpoints hit by the UI) do not pass through the engine. Those endpoints authenticate the user directly and apply the same resolver against the authenticated principal, with no caller indirection.

MCP authenticates as the user directly and does not follow the engine-sentinel pattern. MCP endpoints are out of scope for this overhaul.

---

## Architecture

### Single scope resolver

```python
def resolve_effective_scope(
    caller: Caller,
    requested_scope: Scope,
) -> UUID | None:
    """Resolve the org scope for this operation.

    Caller is the originating identity (user, scheduled job, webhook event).
    Returns the org UUID to use, or None for global.
    """
```

`Scope` distinguishes three states: unspecified (use caller default), explicit None (global), explicit UUID. In FastAPI/Pydantic this requires an `UNSET` sentinel or a tagged union — the resolver must not collapse unspecified and explicit-None into the same branch.

Rules:

| `requested_scope` | Allowed if... | Result |
|---|---|---|
| unspecified | always | caller's default org |
| explicit `None` (global) | caller is platform admin | global (no org filter) |
| `caller.org_id` | always | caller's org |
| any other UUID | caller is platform admin | that org |

"Platform admin" means `caller.is_platform_admin` is true. (Whether the caller's organization is the `is_provider` org is a related concept — the resolver uses the flag, not the org membership; the flag is set at caller construction.)

The function lives in `api/shared/` so both the SDK (engine-side) and the API (for direct user calls) can import it. One source of truth.

### Two methods on the repository base class

`OrgScopedRepository` already exposes both. The contracts need to be made explicit and the implementations consolidated:

**`repo.get(name=...)`** — resolve one entity by identifier. Cascade with override: org-specific wins on collision, falls back to global. Returns at most one row. Used by SDK execution-time reads (`sdk.tables.get("users")`, `sdk.configs.get("api_key")`).

**`repo.list()`** — enumerate everything visible in this scope. Cascade union (org-specific + global, both visible). No override logic — collisions don't matter, both rows are returned. Role-based access filter applied automatically when the repository was constructed with a non-superuser `user_id`. Used by UI listing pages and by anyone who wants the full set of visible entities.

User-ness is encoded in the repository instance (`user_id`, `is_superuser`), not in method names. Construct the repo with the right identity and the right filters fire.

Both methods share the same cascade primitive (`or_(organization_id == X, organization_id.is_(None))`). The base class is the only place that primitive appears in the codebase. No subclass reimplements cascade. No router writes inline cascade queries.

### Entity classification

Eighteen ORM models carry `organization_id`. They split cleanly:

**Execution-resolution (13):** Config, Table, Knowledge, IntegrationMapping, OAuthProvider, OAuthToken, DataProvider, Workflow, Form, Application, Agent, MCPServer, MCPConnection. All accessed via `OrgScopedRepository` subclasses. All subject to cascade.

**Identity (5):** Organization, User, UserRole, OAuthAccount, AuditLog. Belong to an org but are not resolved during execution. No cascade. No `OrgScopedRepository`.

The classification is encoded in code (see "Mechanical enforcement" below), not just in docs.

### Engine-facing endpoints

Rename `/api/cli/*` → `/api/sdk/*`. File rename `routers/cli.py` → `routers/sdk.py` (or split by domain: `sdk_tables.py`, `sdk_configs.py`, `sdk_integrations.py` — decision deferred to implementation).

These endpoints are thin: receive scope from request, pass to repository, return result. They do not call `resolve_effective_scope` — the engine has already resolved. They pass `is_superuser=True` to the repository because the principal is the sentinel.

Endpoints that don't touch org-scoped data (auth, health, version, cross-org platform admin operations, execution telemetry scoped by execution_id) are exempt. The exemption is explicit (allow-list in the lint test) and documented in each endpoint's docstring.

### User-facing endpoints

REST endpoints hit by the UI use `resolve_effective_scope` against the authenticated user (principal IS caller, no indirection). They pass the user's actual privileges (`is_superuser=user.is_superuser`) to the repository so role-based access checks fire.

### Caching is per-repository, cascade is centralized

Cascade is the cross-cutting concern (every execution-resolution entity has the same org-then-global pattern). It lives in `OrgScopedRepository` as the single primitive used by both `get` and `list`.

Cache is the per-entity concern (only Config caches today; maybe Knowledge later; most entities don't). When an entity needs caching, it's a thin transparent layer **inside that repository** wrapping the standard methods — callers don't know whether the result came from cache. The cache layer calls the cascade primitive for misses; it does NOT reimplement cascade.

`ConfigResolver` goes away entirely. Its `load_config_for_scope` becomes the standard `ConfigRepository.list()`, with cache concerns moved onto the repository. Callers that want a `{key: value}` dict do the shape transform at the call site — one line, no abstraction needed. `Config` is not special; it is one row in the entity classification table like every other execution-resolution entity.

The PR-308 cache invalidation bug gets fixed as part of this consolidation:

- Org-scoped config write: `delete` the org cache key (no partial `hset`). Forces re-fetch via cascade on next read.
- Global config write: `INCR bifrost:config:global_version`. Org cache keys embed the version, so a global write naturally invalidates every org cache without enumeration. No `scan_iter`. Old keys age out via TTL.

Cache invalidation hooks live on `ConfigRepository`, not in `core/cache/invalidation.py`. The `core/cache/` module retains shared Redis primitives (keys, TTL constants, client) but not entity-aware invalidation logic.

---

## Documentation tripwires

Documentation alone has not stopped drift. The plan combines docs with mechanical enforcement, but the docs still need to appear at every surface a developer or agent might touch.

**Root `CLAUDE.md`** — Add a top-level "Org Scoping" section with the four resolver rules and a pointer to the canonical doc. Every Claude session loads this; non-negotiable for stopping agent drift on routine tasks.

**`api/src/repositories/README.md`** — Single source of truth. Expand to include:
- The four-rule scope resolution table (with the trust model explained).
- The two repository methods (`get` for single-entity resolution, `list` for enumeration) and how user-ness is encoded in the repository instance, not in method names.
- The cascade-centralized / cache-per-repository split, with an example.
- The full classification table of all 18 ORM models.
- The trust assumption: "the engine sentinel credential is the security boundary; if it leaks, isolation collapses."
- "When NOT to use this pattern" section listing exempt endpoint categories.
- "Common Mistakes" with concrete anti-examples (inline cascades, raw queries in routers, trusting `requested_scope` directly).
- Pointer to the mechanical enforcement tests.

**Module docstring on `api/src/routers/sdk.py`** (post-rename) — "This is the SDK execution surface. The engine has already resolved scope; trust it. Every endpoint touching org-scoped data MUST use an `OrgScopedRepository` subclass. See `api/src/repositories/README.md`."

**Class docstring on `api/src/repositories/org_scoped.py::OrgScopedRepository`** — Expand to document the `get` vs `list_for_user` distinction with intent ("SDK resolution" vs "UI enumeration").

**One-line classification comment on every ORM model with `organization_id`** — `# Execution-resolution entity — access via <RepoClass>. See repositories/README.md` or `# Identity entity — not org-resolved. See repositories/README.md`. This is the tripwire when someone adds a column or migrates a model.

**Function docstring on `resolve_effective_scope`** — The four rules table inline. "If you find yourself writing a different version of this, stop."

`bifrost-build` skill is **not** updated. It targets workspace users building workflows/apps/forms on Bifrost; they don't touch this code.

---

## Mechanical enforcement

The pattern stops drifting when drift is detected automatically. Three new tests:

1. **`test_no_inline_org_scoping_in_routers`** — AST-walks `api/src/routers/`. Fails if any file contains a raw `organization_id == ...` comparison or `organization_id.is_(None)` outside whitelisted lines. New routers cannot introduce drift without explicitly editing the whitelist, which forces code review.

2. **`test_org_scoped_models_have_repository`** — For every ORM model with an `organization_id` column, assert there is a corresponding `OrgScopedRepository` subclass *or* the model is on the explicit identity-entity allow-list. Adding a new org-scoped model without a repository fails CI.

3. **`test_sdk_endpoints_use_resolver`** — For every endpoint in `routers/sdk.py` that declares a `scope` parameter, assert the handler calls `resolve_effective_scope`. Allow-list for exempt endpoints with one-line justification per entry.

Pattern precedent: `test_dto_flags.py` already enforces CLI/MCP/manifest parity the same way. Same shape works here.

---

## Sequence

Eight phases. Each phase ends in a green test run and is committable on its own.

### Phase 0: Plan + audit references

- This plan doc lands.
- Both audit docs are linked from the plan.
- No code changes.

### Phase 1: Centralize the resolver

- Define `Caller`, `Scope` types in `api/shared/`.
- Implement `resolve_effective_scope` in `api/shared/`.
- Unit tests for the four rules, including:
  - Cross-tenant: non-admin caller cannot resolve to other org's scope.
  - Explicit-None: only platform admin can request global.
  - Unspecified vs explicit-None distinction.
- No callers migrated yet. Function exists in isolation.

### Phase 2: Documentation surfaces land

- `api/src/repositories/README.md` expanded to canonical doc.
- Root `CLAUDE.md` "Org Scoping" section added.
- Module docstring on `routers/cli.py` (pre-rename — still useful in current location).
- Class docstring on `OrgScopedRepository`.
- One-line classification comment on all 18 ORM models.
- No behavior changes. The docs describe the target state. Subsequent phases bring code into compliance.

### Phase 3: Mechanical enforcement scaffolding

- All three lint tests written but with **complete current-state allow-lists**. They pass on day one.
- Subsequent phases shrink the allow-lists as code is migrated. The allow-list shrinking IS the migration's progress indicator.

### Phase 4: OAuth migration (the highest-impact security fix)

- Create `OAuthProviderRepository(OrgScopedRepository[OAuthProvider])` and `OAuthTokenRepository(OrgScopedRepository[OAuthToken])`.
- Migrate `sdk_integrations_get` and `sdk_integrations_refresh_token` call sites to use the new repositories.
- Delete `IntegrationsRepository.get_provider_org_token`.
- Delete `OAuthConnectionRepository`. The new `OAuthProviderRepository` absorbs both its cascade reads (via the base class) and its OAuth-specific write methods. Callers using `get_by_name` migrate to `repo.get(name=...)`. The `OAuthConnection` Pydantic return type goes away.
- E2E test: two orgs, each with their own OAuth mapping. Assert org A's SDK call cannot surface org B's token.
- Allow-list entries for the migrated sites are removed.

### Phase 5: Config migration

- Delete `ConfigResolver`. Callers move to `ConfigRepository.get(key=...)` for single values and `ConfigRepository.list()` for full enumeration. The merge IS the cascade — no "merge" method needed.
- Move cache concerns onto `ConfigRepository` as a thin transparent layer wrapping the standard methods.
- Versioned global cache key. Org writes do single `delete` (no `hset`). Global writes do `INCR` on version. Cache invalidation hooks live on the repository, not in `core/cache/invalidation.py`.
- Callers that wanted a `{key: value}` dict do the shape transform at the call site.
- Allow-list shrinks.

### Phase 6: Tables and Application repository relocation + inline cascades

- Move `ApplicationRepository` from `routers/applications.py` to `repositories/applications.py`. Same for `TableRepository`.
- Migrate `cli_list_tables` and any other inline cascade in `routers/cli.py` to use the repository.
- Allow-list shrinks.

### Phase 7: Engine-facing endpoint rename + final cleanup

- `/api/cli/*` → `/api/sdk/*`. **No backwards-compatible aliases.** The CLI enforces strict version equality with the server (`sys.exit(1)` on mismatch — verified in `api/tests/unit/cli/test_cli_version_check.py`), so any deployed CLI is already forced to upgrade against the matching server build. Single coordinated CLI version bump in the same release as the rename.
- `/api/cli/download` (the endpoint that serves the matching CLI build) **stays at its current path** as a documented exception. Moving it adds churn without benefit — the version check is the load-bearing safety, not the URL.
- File rename `routers/cli.py` → `routers/sdk.py`.
- CLI binary's API client updated to use the new paths.
- `_get_cli_org_id` deleted; replaced by direct use of `resolve_effective_scope` (the engine has already resolved by this point, but the function provides the validation path for any remaining direct callers).
- Final allow-list shrink. The three lint tests now have minimal, justified allow-lists.

### Phase 8: Follow-up items filed as separate issues

- Cross-org traversal hardening for any user-facing endpoints discovered during the migration (allow-list entries that survived phase 7 review).
- Redis-spoofing hardening for the `ExecutionContext` payload (sealing/signing).
- Manifest sync `_resolve_*` audit — verify whether import paths bypass repositories and migrate if so.
- Worker containers running as root (pre-existing memory entry, unrelated but adjacent).

Each follow-up gets its own GitHub issue. They are NOT in this overhaul's scope.

---

## Test discipline (during migration)

When tests fail during this migration, **stop and consider why the test expected what it did before adjusting it.** Default assumption: the test was right and the code change is wrong. Tests are claims about behavior; adjusting them silently to match new output erodes the contract they were protecting.

This rule is saved as feedback memory (`feedback_test_failure_discipline.md`) and applies to every phase. If a test fails:

1. Read the test name and the assertion. What was it claiming?
2. Check git history for the commit that introduced it. What bug was it preventing?
3. Decide: (a) my change broke a valid contract, fix the code; (b) the contract is intentionally obsolete, document why and update; (c) the test was wrong from the start (rare, needs justification).
4. Never silently update assertions to match new output.

If a test only covered happy path and now fails because the new code raises on a previously-accepted scope, the failure may be telling you the test was incomplete. Add the missing coverage rather than weakening the assertion.

---

## Risks and mitigations

**Risk: phase 4 (OAuth) is a behavior change that could affect production OAuth flows.**
Mitigation: E2E test pinned at phase 4 entry. Stage the change behind feature flag if the test surface isn't strong enough. Rollback plan: revert the repository wiring, fall back to current behavior.

**Risk: cache invalidation versioning (phase 5) could cause a stampede on the first global config write after deploy.**
Mitigation: pre-compute the global version key on deploy if absent. Document the warm-up.

**Risk: `/api/cli/*` rename (phase 7) breaks deployed CLI binaries.**
Not actually a risk. The CLI does `sys.exit(1)` on any version mismatch with the server (verified in `test_cli_version_check.py`), so deployed CLIs are already forced to upgrade against the matching API build. Single coordinated CLI version bump in the same release. The `/api/download/cli` endpoint (renamed from `/api/cli/download`) is reachable by source/dev installs which skip the version check.

**Risk: the mechanical lint tests have allow-lists that grow over time as people exempt themselves.**
Mitigation: the allow-list requires a one-line comment per entry. Code review catches additions. The audit can be re-run periodically to ensure shrinkage.

**Risk: identity-classification of the 18 models is wrong in subtle cases.**
Mitigation: phase 2 docs include the table. Phase 3 lint test exercises every model. If a classification is wrong, the test fails or the missing repository surfaces in phase 4-6.

**Risk: SDK-side `resolve_effective_scope` is bypassed by some sneaky path.**
Mitigation: SDK has cross-tenant tests at the SDK boundary, not just the API boundary. Phase 4 sets the test pattern; subsequent phases follow it.

---

## What this overhaul does NOT do

Explicit non-goals, so the scope stays bounded:

- Does not change MCP authentication or scope handling. MCP is user-direct and stays that way.
- Does not redesign the engine sentinel credential or rotate it.
- Does not seal/sign the `ExecutionContext` payload in Redis. (Follow-up.)
- Does not migrate identity models (Organization, User, etc.) to `OrgScopedRepository`. They are not cascade entities by design.
- Does not change `bifrost-build` or any workspace-facing skill. This is platform-development work.
- Does not address worker root-user hardening.

---

## Open questions

1. ~~Should the engine-facing endpoint rename land in this overhaul?~~ **Resolved: yes, in phase 7.** CLI enforces strict version equality so backwards-compatibility is not needed. `/api/cli/download` moves to `/api/download/cli`.

2. ~~Does `ConfigResolver` go away entirely?~~ **Resolved: yes, deleted.** Cascade is centralized in `OrgScopedRepository`; cache lives on `ConfigRepository` as a transparent layer. No separate resolver class. Callers use `repo.get()` and `repo.list()` like every other entity. The CLI download endpoint stays at `/api/cli/download` as a documented exception to the `/api/cli/*` rename.

3. ~~The five identity models — are any edge cases?~~ **Resolved: no.** Verified `UserOAuthAccount` is only touched by `services/oauth_sso.py` (login flows) and the user/MFA models. Never resolved by SDK or execution paths. The five-model identity classification stands as-is: Organization, User, UserRole, OAuthAccount, AuditLog.

4. ~~`OAuthConnectionRepository` — migrate or leave as sibling?~~ **Resolved: migrate.** The "exactly one cascade implementation" success criterion isn't met if a sibling class with parallel-but-correct cascade semantics survives. `OAuthProviderRepository(OrgScopedRepository[OAuthProvider])` becomes the canonical class. OAuth-specific operations (`update_connection`, `delete_connection`, `store_tokens`, encryption/decryption around client secrets) move onto the new repository as additional methods rather than a sibling service. The current `OAuthConnection` Pydantic return type disappears — callers work with the `OAuthProvider` ORM row directly.

5. ~~Feature flag on phase 4?~~ **Resolved: no flag.** Feature flags on security fixes are an anti-pattern — the "off" state preserves the vulnerability. Phase 4 lands as one cohesive PR with two-org cross-tenant tests at the SDK boundary. Rollback path is a normal revert.

All open questions resolved. Phases 0-3 can begin once the plan is approved.
