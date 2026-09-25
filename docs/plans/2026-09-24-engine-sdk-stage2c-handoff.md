# Engine SDK integration mutations and OAuth refresh

Executor: OpenCode `opencode-go/muse-spark-1.3-contributor`. Owner/reviewer:
Codex. Worktree: `/home/jack/GitHub/bifrost/.claude/worktrees/sdk-engine-local`.
This is Stage 2c of `2026-09-24-engine-sdk-local-transport.md`. Do not commit,
push, merge, or modify the primary checkout.

## Scope

Move the remaining fixed integration calls through the parent-local transport:
`integrations.upsert_mapping`, `integrations.delete_mapping`, and hidden
`OAuthCredentials.refresh()`. Extract their current business behavior from
`api/src/routers/cli.py` into `api/shared/sdk_integrations.py`. HTTP handlers
and the local dispatcher must invoke those same service functions. SDK facades
must select local transport only when installed; external users retain HTTP.
No local failure may retry over HTTP.

Extend the explicit operation allowlist and bounded JSON child methods in
`api/bifrost/_local_transport.py`. Reuse HTTP request DTOs from
`src.models.contracts.cli` in local dispatch. The parent derives actor,
organization, and service provider membership from its own context; frame
claims may not supply them. Use short parent pooled sessions. Match HTTP
status/error, committed state, mapping config, side effects, and secret
registration. Replace the current test that asserts these operations return
404 from local dispatch.

## Critical behavior

- Keep the HTTP mutation scope gate and cross-org 403s. `upsert_mapping` and
  `delete_mapping` must use the parent actor through `_require_actor`.
- Preserve mapping OAuth links, config writes and merged-config response
  behavior, including the external caller's exclusion of global defaults.
- OAuth refresh must retain the locked token lookup
  (`get_org_level_for_provider(..., for_update=True)`), refresh context,
  token rotation, and external caller restrictions. `OAuthCredentials.refresh()`
  passes no scope today; local dispatch resolves the caller's own scope.
- Register the returned fresh access token with the SDK secret scrubber, as
  the existing HTTP path does.
- Preserve a missing-integration/null response and scope-validation order of
  each existing HTTP endpoint. Do not introduce silent compatibility branches.

## Allowed files and tests

Production: `api/shared/sdk_integrations.py`, `api/src/routers/cli.py`,
`api/src/services/execution/sdk_local_dispatch.py`,
`api/bifrost/_local_transport.py`, `api/bifrost/integrations.py`, and
`api/bifrost/models.py`. Focused SDK/dispatch/router tests and one live worker
E2E may be added or changed under `api/tests/`. Update the main plan's stage
record only after verification. If a different production file is necessary,
report the blocker before editing it.

Run focused unit tests for both transports and current refresh behavior,
external isolation E2E, org-scope mutation E2E, one live workflow with HTTP
disabled for the fixed calls, and `./test.sh quality api`. Do not run a broad
suite by default. Report exact commands, failures, and coverage limits.
