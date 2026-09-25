# Organization SDK shared service

Executor: OpenCode. Reviewer: Codex. Work only in this worktree. Do not
commit, push, merge, or touch primary checkout.

Extract the existing business logic for the five fixed Python SDK
`organizations` operations (`create`, `get`, `list`, `update`, `delete`) from
`api/src/routers/organizations.py` into `api/shared/sdk_organizations.py`.
The HTTP handlers must call the same shared service and keep their current
DTOs, statuses, error precedence, sorting/filter defaults, audit actions,
cache updates/invalidation, and provider-org protections. Delete is a soft
disable and returns HTTP 204; preserve SDK return behavior. Routers keep
`CurrentSuperuser` authorization. Service accepts explicit trusted actor
and DB/session arguments, never raw HTTP Request/JWT/child claims. Engine
local dispatch comes later; do not add it here. Keep ordinary non-SDK
organization routes untouched unless they call the same helpers.

Write focused unit tests for the service and router delegation and run
relevant existing organization, audit/permission E2E tests via `./test.sh`.
Run `./test.sh quality api`, DTO/contract tripwires if a DTO changes.
Report exact commands/results and any failures. Do not use retries, skips,
or xfail to mask failures. Do not edit roles, users, artifacts, tables,
forms/context, or execution transport files.
