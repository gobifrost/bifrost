# Forms and context shared service extraction

Executor: OpenCode. Reviewer: Codex. Work only in this worktree. Do not commit,
push, merge, or touch the primary checkout.

Goal: extract the existing business behavior of the SDK-consumed form list/get
endpoints and `GET /api/sdk/context` from routers into shared callable services.
The HTTP handlers must call those services with unchanged responses, access
rules, error precedence, logo enrichment, Solution scope, and query semantics.
This is a service-extraction stage; do not add the engine transport here.

Read `api/src/routers/forms.py` list/get routes, `api/src/routers/cli.py`
context route, `api/bifrost/forms.py`, and `api/bifrost/client.py` context
property before editing. Prefer `api/shared/sdk_forms.py` and
`api/shared/sdk_context.py` for behavior. Services may receive a DB session
and explicit trusted principal/context parameters. Do not import HTTP request
objects into shared services or relax the existing access checks. Keep routers
thin and preserve their public DTOs.

Write focused unit tests for the shared service and update router tests where
their mock boundary moves. Run the relevant existing form/context unit and
E2E tests through `./test.sh`; run `./test.sh quality api`. Report exact
commands/results and any failures. Do not mask failures with retries/skips.
Do not edit `api/src/services/execution/*`, `api/bifrost/_import_transport.py`,
or other SDK local transport code.
