# Role SDK engine-local transport

Executor: OpenCode. Reviewer: Codex. Work only in this worktree. Do not
commit, push, merge, or touch the primary checkout. Read AGENTS.md and the
aggregate transport plan first.

Migrate the entire fixed `bifrost.roles` facade to named engine-local
operations: `create`, `get`, `list`, `update`, `delete`, `list_users`,
`list_forms`, `assign_users`, and `assign_forms`. Preserve external HTTP
behavior. Both transports must invoke the existing shared implementation in
`api/shared/sdk_roles.py`; the local dispatcher only validates the same
request DTOs, derives a trusted parent principal, opens a short pooled
session, calls the service, and maps results/errors to the existing SDK
contract. Inspect transaction ownership for each mutation and explicitly
commit where the HTTP route commits; `_run_short` itself never commits.

Match exact list envelopes, 404/403/422 precedence, actor attribution,
organization scope, role/form/user assignment side effects, and cache
invalidation. Child frame actor, org, and Solution claims cannot grant
additional access. Preserve `roles.get` missing behavior and all public
model types. No failed-local-call fallback to HTTP.

Add focused unit parity plus a real fork/supervised-service test with fixed
role HTTP disabled; cover one mutation and assignment, denied cross-org
operation, and external HTTP path. Run focused `./test.sh` tests and
`./test.sh quality api`; diagnose failures rather than retrying or masking.
