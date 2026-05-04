# External MCP Client (RFC v2)

**Version:** v2
**Date:** 2026-05-02
**Status:** Locked, ready for implementation
**Supersedes:** v1 (2026-04-25)

Bifrost serves MCP via `mcp_server/`. This proposes the symmetric **client** capability: register external MCP servers, hold per-org connections to them, and let Bifrost agents invoke their tools. v2 reflects the resolution direction in [jackmusick/bifrost#99](https://github.com/jackmusick/bifrost/pull/99) after Jack's 2026-04-30 reframe (accepted 2026-05-01) plus answers to the three open questions Jack didn't address in his comment.

## What changed from v1

- **Not a `kind` discriminator on `integrations`** — new top-level entity (`mcp_servers`) with a server-template / per-org-connection split. `integrations` is left untouched.
- **Both auth flows supported** (`authorization_code` + `client_credentials`). Per-user delegated tokens for the user-first case (M365 Copilot); shared service tokens with two visibility flags (`available_in_chat`, `available_to_autonomous`) for the autonomous + chat-fallback cases (halopsa-mcp).
- **Per-connection (not per-server) tool catalog**. Connections are per-org only (no global). Streamable HTTP transport only (no SSE, no stdio).

## Locked decisions

| Question | Decision | Rationale |
|---|---|---|
| Server template scope | **Platform-level row** (`organization_id` nullable, NULL = visible to all orgs) | Cleaner manifest round-trip; cascade-pattern available as escape hatch if a per-org override case emerges. |
| Per-user token row shape | **Richer `user_mcp_credentials` row** with `oauth_token_id` FK + `consent_granted_at` + `consent_expires_at` + `granted_scopes[]` | Chat reauth UX needs "expiring soon" semantics; consent provenance shouldn't live on the bare `oauth_tokens` row. |
| URL override location | **Default on template, optional override on connection** | Matches Jack's "per-org URL overrides" wording. Most orgs leave it blank. |
| `client_credentials` v1 support | **Yes — both flows supported** in v1. Model `client_credentials` MCP servers as a shared service token in `oauth_tokens` with no per-user mode. | The forcing example (halopsa-mcp) requires it. `OAuthProvider` ORM already supports both flow types; the cost of supporting both at v1 is trivial vs. blocking halopsa-mcp migration. |

## Architecture overview

Four new ORM tables in `api/src/models/orm/external_mcp.py`:

- `mcp_servers` — server **template**, manifest-shareable, NO secrets.
- `mcp_connections` — per-org instance with secrets via FK; carries the two visibility flags.
- `mcp_connection_tools` — per-connection tool catalog populated from the vendor's `tools/list`.
- `user_mcp_credentials` — per-user delegated tokens, links to a row in the existing `oauth_tokens` table.

A new service package, `api/src/services/mcp_client/`, mirrors the existing `api/src/services/mcp_server/` structure. Reuses `oauth_providers` and `oauth_tokens` unchanged — no new crypto, no new token-refresh job (the existing `oauth_token_refresh` scheduler picks up MCP-related tokens once its WHERE clause is broadened).

Manifest round-trip lives in a single new file, `.bifrost/mcp-servers.yaml`, modelled on the `events.yaml` pattern of "sources with nested subscriptions": servers at the top level, connections nested under each server, tools nested under each connection.

## Schema

All four tables are owned by `api/src/models/orm/external_mcp.py`. Pydantic contracts in `api/src/models/contracts/external_mcp.py`. **All upserts are by UUID, not natural key**, explicitly to avoid the `_resolve_integration` cache bug filed as [jackmusick/bifrost#148](https://github.com/jackmusick/bifrost/issues/148).

```
mcp_servers                       (TEMPLATE — manifest-shareable, NO secrets)
  id (UUID, PK)
  name (UNIQUE)
  server_url
  oauth_provider_id  → oauth_providers.id   -- reuse existing OAuth machinery
  redirect_url                              -- deterministic per Bifrost deployment
  discovery_metadata jsonb                  -- snapshot from /.well-known
  organization_id NULL ALLOWED              -- NULL = platform-level template (default)
  is_active boolean
```

`oauth_provider_id` carries the auth flow type (`authorization_code` or `client_credentials`), authorization URL, token URL, audience, scopes — all already supported on `OAuthProvider`. No changes required to that ORM. `OAuthProvider.integration_id` is left NULL for these rows; the FK to the consuming entity has always been nullable, and the field was originally introduced for `integrations`-scoped providers. Discovery metadata is a snapshot of the `/.well-known` payloads at create time, kept for diffing on subsequent re-discovery (so the admin UI can show "the vendor changed its scopes since you registered" rather than silently drifting).

`name` is `UNIQUE` and is the natural key surfaced in manifest files for human-readability, but the manifest still keys by UUID — the `name` column is for display and search, not upsert. This is the same pattern as `agents.name`: the human reads the name, the system reads the id.

```
mcp_connections                   (per-org, has secrets via FK)
  id (UUID, PK)
  server_id            → mcp_servers.id (CASCADE delete)
  organization_id      NOT NULL                  -- per Jack: per-org only
  client_id, encrypted_client_secret             -- this org's OAuth app on the vendor
  server_url_override  NULL                      -- usually empty
  available_in_chat        boolean DEFAULT false
  available_to_autonomous  boolean DEFAULT false
  service_oauth_token_id  → oauth_tokens.id NULL -- shared delegated fallback
  UNIQUE(server_id, organization_id)
```

The two boolean flags are the heart of Jack's reframe. Both default to `false` — meaning a freshly-created connection is personal-use only and does not surface tools to autonomous agents or to chat users without a personal connection. `service_oauth_token_id` points at the shared delegated token that backs both flags when enabled. `client_credentials` connections (halopsa-mcp) populate this slot with a service token that has no `user_id` set on `oauth_tokens`.

`encrypted_client_secret` reuses the existing `decrypt_secret()` helper in `api/src/core/security.py` — the same envelope encryption used by `oauth_providers.encrypted_client_secret` today. There is no new crypto path. Decryption happens at dispatch time only; the secret never leaves the API process and is never logged.

`UNIQUE(server_id, organization_id)` enforces "one connection per (server, org)". A second connection to the same vendor from the same org is not allowed in v1 — if an org wants to model multiple OAuth apps against the same vendor, that's a future feature.

```
mcp_connection_tools              (per-CONNECTION catalog from tools/list)
  id (UUID, PK)
  connection_id  → mcp_connections.id (CASCADE delete)
  tool_name
  tool_schema jsonb
  enabled         boolean DEFAULT true
  disabled_reason text
  last_seen_at
  UNIQUE(connection_id, tool_name)
```

Per-connection because a vendor's `tools/list` may legitimately return different tools for different OAuth apps — feature gates, region-specific tools, beta tools, etc. We don't try to deduplicate across connections.

`enabled = false` with a `disabled_reason` is how an admin opts a tool out without losing the schema for later re-enable. `last_seen_at` updates on each `tools/list` refresh; tools that disappear from the vendor get `enabled = false` and `disabled_reason = "no longer published by vendor"` rather than being deleted, so existing agent tool bindings don't silently break.

Tool-name namespacing in the agent's planning toolset uses `<connection-id>:<tool_name>` rather than the v1-proposed `<integration.name>.<tool>` form. UUID-prefixed names are uglier but rename-stable and unambiguous when an admin renames a server template.

```
user_mcp_credentials              (per-user delegated tokens)
  id (UUID, PK)
  user_id         → users.id (CASCADE delete)
  connection_id   → mcp_connections.id (CASCADE delete)
  oauth_token_id  → oauth_tokens.id (CASCADE delete)
  consent_granted_at
  consent_expires_at
  granted_scopes  text[]
  UNIQUE(user_id, connection_id)
```

`oauth_token_id` is a separate FK rather than reusing `oauth_tokens.user_id`. The latter is reserved for SSO. Per-user delegated MCP tokens get their own first-class link with consent metadata alongside.

`consent_expires_at` is the vendor-stated expiration of the user's consent (typically much longer than the access token's lifetime — Microsoft Graph's `offline_access` consent lasts 90 days from the last refresh). The chat surface watches this field to show "Connection expiring soon — reconnect to avoid interruption" warnings ahead of the actual failure. `consent_granted_at` + `granted_scopes[]` are written once at the OAuth callback and never updated; if the user reconnects with different scopes, that's a new row creation (the previous row gets soft-deleted via cascade when the new one wins on `UNIQUE(user_id, connection_id)`).

CASCADE on all three FKs (`user_id`, `connection_id`, `oauth_token_id`) is intentional. Deleting a user, deleting a connection, or revoking a raw OAuth token all need to clean up the user-credential row — there's no defensible state in which the row survives any of those parents.

## Manifest format

One file: `.bifrost/mcp-servers.yaml`. Servers at the top level keyed by UUID, connections nested under each server keyed by UUID, tools nested as a list under each connection. Mirrors `events.yaml` having sources with nested subscriptions.

```yaml
mcp_servers:
  <server-uuid>:
    name: "Microsoft 365 Copilot"
    server_url: "https://graph.microsoft.com/.../mcp"
    oauth_provider_id: <fk-uuid>
    redirect_url: "https://bifrost.yourcompany.com/api/mcp/oauth/callback"
    discovery_metadata:
      authorization_url: "..."
      token_url: "..."
      audience: "https://graph.microsoft.com"
      scopes: "Files.Read.All Sites.Read.All offline_access"
    organization_id: null              # platform-level template
    is_active: true
    connections:
      <connection-uuid>:
        organization_id: <org-uuid>
        client_id: "a1b2c3d4-..."
        # encrypted_client_secret NOT in manifest (gitignored, like configs)
        server_url_override: null
        available_in_chat: true
        available_to_autonomous: true
        tools:
          - tool_name: graph_search
            tool_schema: {...}
            enabled: true
          - tool_name: send_email
            tool_schema: {...}
            enabled: false
            disabled_reason: "Disabled by admin"
```

`encrypted_client_secret` is NOT in the manifest — same treatment as `Config` values today, gitignored and stored encrypted at rest in the database. The connect popup is the only path that writes it.

Manifest changes required:

- `api/bifrost/manifest.py` — new `ManifestMCPServer`, `ManifestMCPConnection`, `ManifestMCPConnectionTool` Pydantic models.
- `api/src/services/manifest_generator.py` — DB → manifest serialization.
- `api/src/services/manifest_import.py` — `_resolve_mcp_server`, `_resolve_mcp_connection` (UUID upsert; nested tool catalog under connection); deletion lists in `_resolve_deletions` need new entries for the four tables.
- `api/src/services/manifest_import.py` — add `_index_mcp_servers_from_manifest` analog to `_index_agents_from_manifest`.

## Auth resolution

Single function, `mcp_client/auth_resolution.py::resolve_token`, called by both the chat executor and the autonomous executor. Five paths (matching the wireframe in `/tmp/bifrost-mcp-mockup.html` §10):

| # | Caller | User connected? | Flag state | Outcome |
|---|---|---|---|---|
| 1 | `caller_user_id` present (chat or signed-claim webhook) | Yes — `user_mcp_credentials` row found and refresh works | — | Use the user's `access_token`. Vendor sees the user. |
| 2 | `caller_user_id` present | No — not connected, or refresh failed | `available_in_chat = true` AND service token healthy | Use the shared service `access_token`. Vendor sees the service account. |
| 3 | `caller_user_id` present | No, no fallback path | `available_in_chat = false` OR no healthy service token | Raise `NeedsReauthError(reauth_url=…, connection_id=…)`. Chat surface renders an inline Connect button. |
| 4 | `caller_user_id is None` (autonomous: schedule, webhook without user claim) | N/A | `available_to_autonomous = true` AND service token healthy | Use the shared service `access_token`. Vendor sees the service account. |
| 5 | `caller_user_id is None` | N/A | `available_to_autonomous = false` | Should never reach dispatch — `resolve_agent_tools()` filters tools out at planning. If we get here, log + assert (misconfig). |

Path 5 is enforced at planning rather than dispatch so autonomous agents never see a tool they cannot invoke. Path 3 is enforced at dispatch because the same agent in chat is allowed to surface a tool the user hasn't connected — they get the inline reconnect prompt instead of a missing tool.

"Healthy service token" means: the row exists, `expires_at > now()` after applying the existing `oauth_token_refresh` margin, and the most recent refresh attempt did not fail. The check is cheap (a single read against `oauth_tokens`) and runs on every dispatch — we deliberately do not cache the health verdict. A vendor revoking a token mid-conversation should fail-closed on the next call rather than serve stale "healthy" verdicts.

`NeedsReauthError` carries `connection_id` and a server-built `reauth_url` (the vendor's authorize URL with state and PKCE primed by the API). The chat surface does not assemble the URL — it just opens what the API hands back. This keeps redirect-state generation in one place.

## Connect popup wording

Triggered when an admin clicks "Connect" on the shared service connection in §5–6 of the mockup. The warning copy is **mandatory** and verbatim from Jack's PR comment:

> Users will read and modify resources visible to **{your name}**'s account — recommended only for dedicated service accounts, not personal accounts.

The popup also displays the scopes about to be granted (from `discovery_metadata.scopes`), a Cancel button, and a Continue button that initiates the OAuth flow against the vendor with the deterministic redirect URL stored on the server template. Same modal copy is reused on the per-user connect path in §7 with the warning suppressed (per-user connect doesn't share the consent broadly, so the warning isn't relevant).

## Discovery-first OAuth setup

When an admin creates a new server template (mockup §3), the form is intentionally minimal: display name + server URL. Clicking "Discover OAuth metadata" calls `mcp_client/discovery.py`, which fetches:

- `<server-base>/.well-known/oauth-authorization-server`
- `<server-base>/.well-known/oauth-protected-resource`

The router populates the form fields (authorization URL, token URL, audience, required scopes) from the response and stores the raw payload in `discovery_metadata` for diff-on-rediscovery later. The redirect URL is fixed per Bifrost deployment (`{deployment-host}/api/mcp/oauth/callback`) and shown read-only — the admin's job is to register that URL in the vendor's OAuth app, not configure it on our side.

Manual fallback: if discovery returns 404 or invalid metadata, the panel toggles to editable inputs and the admin enters values directly. We intentionally do not retry-then-fallback automatically — operators should know whether they're working from discovery or from manual config.

## Auth-context plumbing — sensitive surface

The plumbing required to thread `caller_user_id` from request entry to MCP dispatch crosses several layers and **is the most sensitive piece of this work**. It must surface for manual review per `CONTRIBUTING.md`'s sensitive-paths rule.

The chain:

```
request → (chat handler / agent_run_service.enqueue_agent_run)
        → Redis-stored caller context (for autonomous queueing)
        → worker (api/src/jobs/consumers/agent_run.py)
        → AutonomousAgentExecutor.run()  /  AgentExecutor.chat()
        → resolve_agent_tools(caller_user_id=…)
        → _execute_tool(caller_user_id=…)
        → mcp_client.dispatch.invoke(connection, tool_name, args, caller_user_id)
        → auth_resolution.resolve_token(connection, caller_user_id)
```

**Today the autonomous executor receives `_caller: dict | None` but does not use it** (`autonomous_agent_executor.py` line ~73). The Redis-stored caller context written by `enqueue_agent_run` (`agent_run_service.py:44–48`) needs to flow through `consume_agent_run` deserialization and reach the executor. The executor reads `_caller["user_id"]` if present and threads it down through `resolve_agent_tools` and `_execute_tool`, replacing the hardcoded `SYSTEM_USER_ID` reference at line ~401 of `_execute_tool` for MCP dispatch only (other system tools continue to use `SYSTEM_USER_ID`).

`AgentExecutor.chat()` extracts `caller_user_id = conversation.user_id` upfront. Both executors call into the same `dispatch.invoke()`, which calls into the same `resolve_token()`. There is one place that does auth resolution. There is one place that decides whether the vendor sees the user or the service account.

`ToolResult` gains an `error_type: str | None = None` field. When MCP dispatch raises `NeedsReauthError`, the executor catches it and emits a `ToolResult` with `error_type="needs_reauth"`, the human-readable error in `error`, and `metadata={"reauth_url": …, "connection_id": …}`. The chat surface renders that envelope as the inline Connect button.

The same `error_type` field is reusable for other future structured errors (rate-limit, quota, circuit-breaker) without re-plumbing — each gets its own variant string and matching frontend renderer. v1 only emits `needs_reauth`.

**Per-call audit:** every MCP dispatch logs `(agent_run_id, connection_id, tool_name, status, duration_ms, resolution_path)` through the same path workflow tools use, with `tool_kind="external_mcp"` and the resolution-path enum recorded so a post-hoc review can answer "which token did this run use, the user's or the service account's?" That distinction is critical for an auditor — it's the difference between "the user's permissions" and "the service account's permissions" on the vendor side.

## Webhook-triggered runs

Per Jack's "strict, autonomous = no per-user token lookup" line, webhook-triggered agent runs are autonomous (`caller_user_id=None`), full stop. `events/processor.py::_queue_agent_run_for_delivery` (line ~588) already passes `caller_user_id=None`; this is correct and stays.

`resolve_agent_tools()` filters per-user-only MCP tools out of the agent's planning toolset whenever `caller_user_id is None`. So a webhook-triggered run against an agent whose MCP tools are exclusively per-user delegated will see those tools removed from its plan rather than failing at dispatch.

The "permissive signed-user-claim path" Jack mentioned — where a third-party MCP client (e.g. Cursor calling Bifrost-as-MCP-server) embeds a signed claim asserting "this run is on behalf of user X" and Bifrost honors it for downstream MCP-as-client lookups — is acknowledged and **explicitly deferred**. v1 of v2 does not implement signed user claims on inbound webhooks. The shape of the claim, the signing key trust model, and the audit-trail consequences need their own RFC.

## API surface

New routers under `api/src/routers/`:

- `GET /api/mcp-servers` — list templates (platform admin sees all).
- `POST /api/mcp-servers` — create template.
- `GET /api/mcp-servers/{id}` — fetch single template.
- `PATCH /api/mcp-servers/{id}` — update template.
- `DELETE /api/mcp-servers/{id}` — delete template (cascades to connections, tools, user creds).
- `POST /api/mcp-servers/{id}/discover` — re-run discovery, return diff against `discovery_metadata`.
- `GET /api/mcp-connections/{id}` — fetch connection (org-scoped).
- `POST /api/mcp-connections` — create connection under a server.
- `PATCH /api/mcp-connections/{id}` — update connection (flags, client_id, override URL, secret rotation).
- `DELETE /api/mcp-connections/{id}` — delete connection.
- `POST /api/mcp-connections/{id}/connect` — initiate OAuth flow against the vendor for the shared service token. Returns the authorize URL for the popup.
- `POST /api/mcp-connections/{id}/refresh-tools` — call `tools/list` against the vendor and upsert `mcp_connection_tools`.
- `GET /api/mcp/oauth/callback` — deterministic redirect URL. Completes the auth code exchange. State parameter encodes whether this is a service-connection or per-user connect, and which connection id.
- `GET /api/me/mcp-connections/{connection_id}` — per-user view: is this user connected to this connection?
- `POST /api/me/mcp-connections/{connection_id}` — per-user connect (initiates a separate OAuth flow that lands in `user_mcp_credentials` instead of `mcp_connections.service_oauth_token_id`).
- `DELETE /api/me/mcp-connections/{connection_id}` — disconnect (revokes user's row, optionally calls vendor's revoke endpoint).

The platform-admin endpoints require platform admin. The connection endpoints are org-admin scoped to the connection's `organization_id`. The per-user endpoints are scoped to the calling user.

OAuth callback state encoding: `state = HMAC(secret, json({connection_id, mode: "service"|"user", user_id?, csrf_nonce}))`. Verified server-side on callback. Mode determines whether the resulting `oauth_tokens` row gets linked from `mcp_connections.service_oauth_token_id` or from a new `user_mcp_credentials` row. CSRF nonce is checked against a short-TTL Redis entry written at flow start.

Outbound destination control: respects the existing `MCP_CLIENT_ALLOWED_HOSTS` env-var pattern (proposed in v1, kept). When set, dispatch refuses to call any host not on the allowlist — a defense against a stolen admin credential pointing a connection at a private/internal MCP endpoint. Unset = allow all (dev default).

Result-size and timeout caps stay at the v1 defaults: 256 KB per call, 30 s wall-clock. Overridable per-connection via a future `configs` slot — not built in v1 but the seam is there.

## UI surface

Full wireframe: `/tmp/bifrost-mcp-mockup.html` (linked in the PR comment, not committed).

Screens:

- **Sidebar nav** (§1) — new "MCP Servers" item under Configuration, peer of Integrations, `requiresPlatformAdmin: true`.
- **MCP Servers list** (§2) — table of templates: name, URL, connection count, tool count, discovery status.
- **MCP Server form** (§3) — discovery-first new/edit. URL → "Discover" → fields populate. Manual fallback link.
- **Server detail with Connections tab** (§4) — list of per-org connections, status badge, tool count, the two flags as columns.
- **Connection edit** (§5–6) — OAuth credentials panel, URL override panel, the two flags with help text, shared service connection panel with Connect/Reconnect/Disconnect, tool catalog with enable/disable toggles, the connect popup with mandated wording.
- **Per-user My Connections** (§7) — under user settings, list of connections in the user's org, per-user status, org default, Connect/Reconnect/Disconnect buttons.
- **Agent builder tool picker** (§8) — MCP tools alongside built-in tools with auth-context badges (Service auth / Per-user delegated / Disabled) and a publish-time warning panel for chat-only-on-schedulable-agent mismatches.
- **Chat needs_reauth experience** (§9) — `error_type=needs_reauth` ToolResult renders an inline Connect button.

Component plan:

- `client/src/pages/{MCPServers,MCPServerDetail,MCPConnectionEdit}.tsx` — new pages.
- `client/src/components/mcp/MCPServerForm.tsx` — discovery-first form.
- `client/src/components/oauth/OAuthProviderEditor.tsx` — extracted shared component, used by both Integrations and MCP Connections (per Jack's "OAuth provider editor widget extracted into a shared component").
- `client/src/components/user/UserMCPConnections.tsx` — per-user list.
- `client/src/components/layout/Sidebar.tsx`, `client/src/pages/UserSettings.tsx`, `client/src/components/agents/AgentSettingsTab.tsx`, `client/src/components/chat/{ChatWindow,ToolExecutionBadge}.tsx` — modified.

Reused: existing `OAuthCallback.tsx` + `useOAuth.ts` postMessage popup pattern; shadcn `Form` / `Dialog` / `Tabs` / `MultiCombobox`; react-hook-form + zod patterns from `AgentSettingsTab`.

**Type generation:** after each backend contract change, `cd client && npm run generate:types` lands the new types in `client/src/lib/v1.d.ts`. No manual TypeScript types for any of these surfaces.

## Open questions resolved

The mockup left three follow-up questions for Jack. The plan locks one further question (`client_credentials` v1 support). Answered in prose:

**Q1: Server template scope — platform level or per-org with NULL default?**
A: Platform-level row (`organization_id` nullable, NULL = visible to all orgs).
Rationale: Manifest round-trip is materially cleaner with one canonical row per server. Per-org override is still expressible via the cascade pattern (insert a non-NULL row that shadows the NULL row) if a forcing example shows up — it's not closing the door, just deferring it.

**Q2: Per-user token row shape — thin link or rich row with metadata?**
A: Rich row. `user_mcp_credentials` carries `oauth_token_id` + `consent_granted_at` + `consent_expires_at` + `granted_scopes[]` alongside the FK.
Rationale: The chat reauth UX needs "expiring soon" semantics so we can warn users before their token dies mid-conversation. Consent provenance (when they consented, what scopes they agreed to) is consent-flow state, not OAuth-token state — it doesn't belong on the bare `oauth_tokens` row, which is shared with SSO and machine tokens.

**Q3: URL override location — on the template, on the connection, or both?**
A: Default URL on the template, optional override on the connection. Most connections leave the override blank.
Rationale: Matches Jack's "per-org URL overrides" wording. The template URL is the right answer for 95% of cases. The override is for regional / sovereign cloud deployments where one org's vendor lives at a different host.

**Q4: `client_credentials` flow support in v1?**
A: Yes. Both `authorization_code` and `client_credentials` flows are supported in v1. `client_credentials` connections are modelled as a shared service token in `oauth_tokens` (no `user_id`) referenced by `mcp_connections.service_oauth_token_id`, with no per-user mode available.
Rationale: The forcing example (halopsa-mcp) is `client_credentials`. `OAuthProvider` ORM already supports both flow types. Blocking halopsa-mcp migration on a "phase 2 will add `client_credentials`" excuse would gate the most concrete validation we have for the whole design.

## Implementation phasing

Eight phases, ~13.5 days total. Phases 1–4 (backend) start immediately; 5–7 (frontend) start once Phase 4 establishes the OpenAPI surface; 8 sequences after upstream merges land in `SpireTech/bifrost:deploy`.

**Phase 0 — Spec v2 in parallel (½ day, non-blocking).**
This document. Pushed to `spec/external-mcp-client-v2`, link posted on PR #99. Does not gate Phase 1.

**Phase 1 — Backend foundation (~3 days).**
Four new ORM models in `api/src/models/orm/external_mcp.py`. Pydantic contracts in `api/src/models/contracts/external_mcp.py`. Repositories in `api/src/repositories/external_mcp.py` (`MCPServerRepository`, `MCPConnectionRepository` — org-scoped, cascade lookup). Single Alembic migration covering all four tables. Manifest models, generator, and import resolver. Deletion lists updated. Round-trip unit test in `api/tests/unit/test_manifest.py`. Reuse `OAuthProvider` and `OAuthToken` unchanged. Extend `oauth_token_refresh.py` WHERE clause to include MCP-related tokens.

**Phase 2 — `mcp_client/` service package (~2 days).**
New package at `api/src/services/mcp_client/` mirroring `mcp_server/`: `client.py` (streamable HTTP wrapper, per-connection), `catalog_sync.py` (`tools/list` refresh), `dispatch.py` (tool call → vendor MCP, normalized envelope), `auth_resolution.py` (the 5-path table), `discovery.py` (well-known probes), `errors.py` (`NeedsReauthError` + structured chat-surface envelope). Streamable HTTP only via `mcp.client.streamable_http.streamablehttp_client`. Reuses `decrypt_secret()` and `oauth_provider.py`. Unit test covering all 5 auth-resolution paths.

**Phase 3 — Agent executor integration (~2 days, sensitive).**
Thread `caller_user_id` from request entry to dispatch through both executors. `resolve_agent_tools()` gains the param and filters per-user-only MCP tools when `caller_user_id is None`. `AgentExecutor.chat()` extracts `caller_user_id = conversation.user_id`. `_execute_tool()` adds the MCP case (between system and workflow). `AutonomousAgentExecutor.run()` reads `_caller["user_id"]`, threads it down. `consume_agent_run` deserialization confirmed to pass caller through. `ToolResult.error_type` field added. `NeedsReauthError` catch path emits the structured envelope. Integration test exercises both executors with a stub MCP server.

**Phase 4 — REST API + OAuth flow (~1 day).**
New routers (`mcp_servers.py`, `mcp_connections.py`, `mcp_oauth_callback.py`). Discovery endpoint wired to `mcp_client/discovery.py`. OAuth callback handles both service and per-user state. Manual smoke test from API container: create server → discover → create connection → connect (popup completes) → tools/list refresh.

**Phase 5 — Frontend admin UI (~3 days).**
Mockup §2–6. New pages, new MCPServerForm, new shared OAuthProviderEditor (extracted per Jack), sidebar entry. shadcn forms + react-hook-form patterns from `AgentSettingsTab`. Playwright e2e: create server → connect → use tool in agent chat.

**Phase 6 — Frontend user UI (~1 day).**
Mockup §7. New "Connections" tab on user settings, `UserMCPConnections.tsx`. Connect uses the same OAuth popup flow as admin but stores `user_mcp_credentials`. Playwright e2e: per-user connect, disconnect, see needs_reauth in chat.

**Phase 7 — Frontend agent builder + chat (~1 day).**
Mockup §8–9. Agent builder shows auth-context badges per tool and publish-time warning panel for chat-only-on-schedulable-agent mismatches. Chat surface renders `error_type=needs_reauth` as inline Connect button (reusing the `AskUserQuestionCard` inline-action pattern).

**Phase 8 — halopsa-mcp-server migration (~1 day, SpireTech-only).**
After Phases 1–7 land in `SpireTech/bifrost:deploy`. Register halopsa-mcp as a platform-level template in `.bifrost/mcp-servers.yaml` (workflows manifest). SpireTech-org connection with rotated client_secret. Wire Tech Support Assistant agent (`561b92e1-23f3-4cb5-8d01-2b104a5bd9e6`) to the MCP tools. Verify the 19 tickets-module tools used by the agent. Deprecate the equivalent native HaloPSA workflows once parity holds for ≥1 week.

## Verification

Per-phase gates plus a cross-cutting checklist before merge.

**Phase 1.** `pyright` and `ruff check` clean. Migration applies on dev and rolls back cleanly. Round-trip test in `api/tests/unit/test_manifest.py`: write `.bifrost/mcp-servers.yaml` → import → re-export → byte-identical. Stale-entity cleanup: changing `confirm_deletes:false` against a manifest with one of the four tables stripped reports the right pending deletions and does not fire.

**Phase 2.** Unit test for `auth_resolution.resolve_token` covering all 5 paths in the auth-resolution table (each path exercised at least once before merge). Live test against halopsa-mcp's `tools/list` from a Python REPL inside the API container, asserting the connection-tool catalog populates correctly.

**Phase 3.** Integration test that exercises both executors with a stub MCP server. Confirm `caller_user_id` lands at dispatch in chat AND autonomous paths. Verify `error_type=needs_reauth` propagates through `_execute_tool` and into the conversation envelope. Verify the resolution-path audit log column is populated correctly for each of paths 1, 2, 4.

**Phase 4.** Manual smoke from the API container: create server → discover → create connection → connect (popup completes) → tools/list refresh populates `mcp_connection_tools`. Verify `service_oauth_token_id` lands and decrypts. OpenAPI spec validates against the new contracts.

**Phases 5–7.** Playwright e2e: admin flow (create server → connect → use tool in agent chat); user flow (per-user connect, disconnect, see needs_reauth in chat); agent builder flow (publish-time warning panel renders for chat-only-on-schedulable mismatch).

**Phase 8.** `bifrost run agent tech-support-assistant` smoke on dev with halopsa-mcp tools instead of native workflows; compare 10 ticket-summary outputs against the native version. Differences flagged for review before deprecating natives.

**Cross-cutting.** `./test.sh stack up && ./test.sh all` green. `cd client && npm run tsc && npm run lint` clean. Manifest sync against dev with `confirm_deletes:false` succeeds with "imported X entities" and zero pending deletions. Manual sensitive-paths review per `CONTRIBUTING.md` — Phase 3 is the trigger; the auth-context plumbing diff gets a second pair of eyes.

## Security highlights

- **Encrypted-at-rest credentials** reuse `oauth_providers.encrypted_client_secret`'s `decrypt_secret()` helper. No new crypto path.
- **Token refresh** via the existing `oauth_token_refresh.py` scheduler. Single change: WHERE clause broadens to include MCP-related tokens. No new job.
- **Outbound destination control** via the existing-pattern `MCP_CLIENT_ALLOWED_HOSTS` env var.
- **Result-size cap** 256 KB per call; **timeout** 30 s wall-clock. Both per-connection-overridable in a future release; not in v1.
- **Audit trail** records the resolution-path enum on every dispatch so an auditor can answer "did this run use the user's permissions or the service account's?" — the question that matters most when an MCP-driven action shows up in a vendor's audit log.
- **Sensitive paths review** required on the Phase 3 diff per `CONTRIBUTING.md`: agent_executor.py, agent_helpers.py, autonomous_agent_executor.py, agent_run consumer, ToolResult contract.
- **Untrusted server bytes.** Vendor responses are validated against the declared `tool_schema` and size-capped. The vendor's tool *definitions* (name, schema) are trusted for catalog purposes; the vendor's tool *responses* are not.

## Files touched

**New backend files:**
- `api/src/models/orm/external_mcp.py`
- `api/src/models/contracts/external_mcp.py`
- `api/src/repositories/external_mcp.py`
- `api/src/services/mcp_client/{__init__,client,catalog_sync,dispatch,auth_resolution,discovery,errors}.py`
- `api/src/routers/{mcp_servers,mcp_connections,mcp_oauth_callback}.py`
- `api/alembic/versions/<timestamp>_external_mcp.py`

**Modified backend files:**
- `api/bifrost/manifest.py` — `ManifestMCPServer`, `ManifestMCPConnection`, `ManifestMCPConnectionTool`
- `api/src/services/manifest_generator.py` — DB → manifest
- `api/src/services/manifest_import.py` — `_resolve_mcp_server`, `_resolve_mcp_connection`, deletion lists, `_index_mcp_servers_from_manifest`
- `api/src/services/agent_executor.py` — chat exec, `_execute_tool` MCP case, `ToolResult.error_type`
- `api/src/services/execution/agent_helpers.py` — `resolve_agent_tools` gains `caller_user_id`
- `api/src/services/execution/autonomous_agent_executor.py` — read `_caller["user_id"]`, thread it
- `api/src/jobs/consumers/agent_run.py` — verify caller deserialization
- `api/src/jobs/schedulers/oauth_token_refresh.py` — broaden WHERE clause

**New frontend files:**
- `client/src/pages/{MCPServers,MCPServerDetail,MCPConnectionEdit}.tsx`
- `client/src/components/mcp/MCPServerForm.tsx`
- `client/src/components/oauth/OAuthProviderEditor.tsx` (extracted shared component)
- `client/src/components/user/UserMCPConnections.tsx`

**Modified frontend files:**
- `client/src/components/layout/Sidebar.tsx`
- `client/src/pages/UserSettings.tsx`
- `client/src/components/agents/AgentSettingsTab.tsx`
- `client/src/components/chat/{ChatWindow,ToolExecutionBadge}.tsx`

## Migration and compatibility

Additive across the board. Four new tables; no changes to existing tables. Existing `oauth_providers` / `oauth_tokens` / `users` / `organizations` rows are untouched — the new tables only reference them via FK. Reversible: dropping the four tables and the alembic revision returns the system to its pre-feature state with no orphans.

API responses for existing endpoints are unchanged. New endpoints under `/api/mcp-servers`, `/api/mcp-connections`, `/api/mcp/oauth/callback`, `/api/me/mcp-connections`. Clients that ignore unknown routes are unaffected.

The single hot-loaded behavior change: `ToolResult` gaining `error_type: str | None = None`. Defaulting to None means existing tool callers (system tools, workflow tools, delegated agents) continue to emit `error_type=None`. Frontend renderers that don't know about `error_type` ignore it and render the generic error string. Frontend renderers that do know about it switch to the structured renderer when `error_type="needs_reauth"`.

`oauth_token_refresh.py` WHERE-clause broadening to include MCP-related tokens is the only existing-job change. The same job continues to refresh integration-scoped tokens; it now also refreshes MCP-scoped tokens. No new job, no new schedule.

## Alternatives rejected

For completeness, alternatives weighed and dropped:

- **`kind` discriminator on `integrations`** (the v1 design). Conflates two different concerns under one entity, complicates the manifest serialization (one row, two shapes), and makes the per-org / template split awkward. Rejected after Jack's reframe.
- **Single dispatcher tool** (`mcp_call(server, tool, args)`). Collapses N tool schemas into one. The LLM can't reason about specific tools at planning time, and per-tool grants become impossible. Rejected at v1 and stays rejected.
- **Delegating to Anthropic's `mcp_servers=` parameter** on `messages.create`. Requires public exposure of every MCP server, locks dispatch into Anthropic's infra, bypasses Bifrost's grant gate. Could be a future per-connection fall-back option for operators who don't want to host the client, but not v1.
- **Mirror each remote tool as a workflow.** Doesn't scale; remote tool changes become workflow PRs. The forcing example is 83 HaloPSA tools — that's 83 mirrored workflows kept manually in sync.

## Out of scope (deferred)

- **Third-party MCP-client connecting to Bifrost-as-MCP-server with signed user claims.** The "permissive signed-user-claim path" Jack alluded to. Needs its own RFC: claim shape, signing key trust model, audit consequences. Until then, `caller_user_id` only flows from Bifrost-owned chat surfaces and webhook handlers.
- **`client_credentials` schema slot beyond the v1 minimum.** v1 models `client_credentials` connections as a shared service token only. If a use case shows up for "per-user `client_credentials`" or for granular per-tool client identities, it's a v2 problem.
- **Curated catalog of templates.** Intentional non-feature. There is no "browse from a curated list of public MCP servers" UI. The manifest round-trip *is* the sharing mechanism — operators check `.bifrost/mcp-servers.yaml` into git, share their git, others import. That stays consistent with how Bifrost's other entities are shared today.

## Migration path for halopsa-mcp

Phase 8 sequences after the upstream PR merges into `jackmusick/bifrost:main` and `SpireTech/bifrost:deploy` picks them up. Steps:

1. Register `halopsa-mcp-server` as an MCP server template in `.bifrost/mcp-servers.yaml` in the SpireTech workflows repo. URL: `https://bifrost.spiretech.com/halopsa-mcp/mcp`. Discovery works (it exposes `/.well-known/oauth-authorization-server` + JWKS). Auth: `client_credentials`.
2. Per-org connection in the SpireTech org. `client_id` from the existing halopsa-mcp integration record. `client_secret` rotated and stored encrypted via the new connect popup.
3. Wire the Tech Support Assistant agent to depend on the MCP tools instead of the equivalent native HaloPSA workflows. The agent currently re-implements 19 ticket-module tools as workflows — these become MCP tool bindings.
4. Smoke test: `bifrost run agent tech-support-assistant` against dev with the MCP tools, compare 10 ticket-summary outputs against the native version. Deprecate the native workflows once parity holds for one week.

This validates the `client_credentials` path end-to-end against a real MCP server with non-trivial scope (83 tools). The M365 Copilot path, validating `authorization_code` + per-user delegated tokens, lands as a separate follow-up once a SpireTech org has the M365 Copilot license.
