# Engine SDK table document reads

Executor: OpenCode `opencode-go/muse-spark-1.3-contributor`. Owner/reviewer:
Codex. Worktree: `/home/jack/GitHub/bifrost/.claude/worktrees/sdk-engine-local`.
Start only after Stage 2c is reviewed and committed. Do not commit, push,
merge, or edit the primary checkout.

## Scope

Move `tables.get`, `tables.query`, and unfiltered `tables.count` to the
parent-local transport. The HTTP router and parent dispatcher must call the
same table resolution service and the existing `shared.table_documents` read
service. Move `get_table_or_404` from `src.routers.tables` into a focused
`shared.table_resolution` module; the router imports it. Preserve the exact
org, Solution, app, inbound-access, policy, audit, pagination, DTO, and status
behavior. Do not import the table router in local dispatch.

The resolver context must carry `db`, `user`, `org_id`, `app_id`, `solution_id`,
and `caller_solution_id`. HTTP supplies its existing context. The parent
constructs the local context from trusted execution/service metadata.
Never accept `caller_solution`, `app_id`, user, actor, or principal claims from
the child frame. A child-supplied `solution` for get/query is only a target;
the caller's install identity remains parent-owned. Preserve UUID lookup,
org gate, own-install/name fallback, and the inbound gate in order.

Build token-equivalent `UserPrincipal` objects in the parent: workflow engine
tokens are system-user superusers with parent execution and Solution IDs;
service tokens are system-user non-superusers with parent service, attempt,
and Solution IDs. The current config-oriented `is_platform_admin` field
tracks the initiating user and cannot stand in for the engine token when
evaluating table access. Add focused tests of both identities.

Use three named allowlisted operations (`tables.get`, `tables.query`,
`tables.count`) with bounded child frames. Validate query with `DocumentQuery`
and return JSON-mode DTOs. Keep the service's actual 404/403 status in local
frames; the SDK facade applies its existing method-specific 404 result
(`None`, empty `DocumentList`, or zero). A local error never retries HTTP.
Filtered `tables.count(where=...)` must continue composing through public
`tables.query(limit=1)` so its existing behavior is retained; do not add a
filtered-count operation. Return unfiltered count as `{"count": n}` because
the current transport result contract does not carry bare integers.

## Verification

Unit tests: HTTP/local SDK parsing parity, no-HTTP local calls, query DTO
defaults and pagination, missing table/row, policies and deny audit, parent
Solution forgery, cross-install targeting, service identity, malformed frames,
large chunked result, and one short pooled session per request. Add a live
worker E2E with fixed-operation HTTP disabled and compare with REST, plus a
live service path because its token identity differs. Run existing Solution
table gate and policy regressions and `./test.sh quality api`; report exact
commands and broader suites not run.

Allowed production files: `api/shared/table_resolution.py`,
`api/shared/table_documents.py` only if necessary,
`api/src/routers/tables.py`,
`api/src/services/execution/sdk_local_dispatch.py`,
`api/bifrost/_local_transport.py`, `api/bifrost/tables.py`, and the minimal
trusted parent context plumbing if required. Tests under `api/tests/`.
Report before modifying any other production file.

## Reviewer status, 2026-09-25

The 40 new table-read unit tests, 85 existing unit regressions, API quality,
and the live workflow test passed. The live supervised-service test is a
blocking integration dependency: the service child attempts a cold source
fetch over `/api/sdk/modules/{path}` before any table call and receives 403
because that HTTP endpoint requires a superuser while service tokens are
non-superuser. The service then crash-loops. This test remains in place and
must pass after the dedicated parent-local import channel is implemented.
The aggregate branch is not ready for a PR or main merge until then.
