# Authentication Consolidation — Unfinished

Status: **in progress; not complete** (2026-10-30).

PR1a #1146 and runtime PR1b #1147 are merged. They establish the global
Postgres authority epoch, shared runtime credentials, refresh and live-run
checks with a legacy bridge. They do not complete authentication consolidation,
and merge does not imply production deployment. Do not redo these changes.

This work owns the remaining authentication inventory and staged implementation:
human password/SSO/MFA/passkey/device/invite issuance, browser sessions and refresh,
CLI/SDK, MCP OAuth, runtime/service/remote launchers, API keys, embeds, and other
credential entry points. Preserve purpose and authorization boundaries while
sharing issuance, verification, refresh and revocation where appropriate.

PR2 (human authentication) remains unfinished. Resolve browser cookie-size limits
and refresh-token JTI registration/revocation, including MCP OAuth. Use the ONE
global random 64-bit authority epoch in Postgres, bumped transactionally; retain
live-run checks. Redis must not cache cheap Postgres row lookups.

PR1c legacy bridge removal is deferred until a later release has shipped PR1b,
compatible launchers are deployed and the documented legacy-status/admission
preconditions are satisfied. This work must not remove the live bridge early.

## Coordination and Evidence

- Source chronology: `.worktrees/pr-sweep-20261029/docs/plans/2026-10-29-pr-sweep-idle-churn-tokens-handoff.md` in the primary repository.
- Runtime plan: `.worktrees/token-pr1b/docs/plans/2026-10-30-token-pr1-implementation-plan.md` in the primary repository.
- Gap notes: `/home/jack/bifrost-bench-evidence/notes/token-spec-gaps-20261029.md`.
- Do not change sibling benchmark scripts/worktrees or run production operations.
- Acquire `/tmp/bifrost-capacity-host.lock` with flock before expensive builds or
  test/debug stacks; hold it until all owned workloads stop. Inspect older
  activity too. Do not pause benchmark children or hold the lock while waiting
  for another owner. Record test gaps in `/tmp/bifrost-capacity-coordination.md`.

## Coverage and Delivery

Inventory, implementation stages, focused verification and independent security
review evidence will be filled in here as source inspection proceeds.
