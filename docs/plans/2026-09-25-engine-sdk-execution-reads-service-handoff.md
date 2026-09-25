# Shared workflow and execution SDK reads

Executor: OpenCode. Reviewer: Codex. Work only in this worktree. Do not commit,
push, merge, edit the primary checkout, or touch the engine dispatcher.

Extract the business logic used by fixed Python SDK reads into shared service
functions called by their existing HTTP routes:

- `workflows.list()` → `GET /api/workflows` in `api/src/routers/workflows.py`.
- `executions.list()` → `GET /api/executions` in `api/src/routers/executions.py`.
- `executions.get()` and `workflows.get()` → `GET /api/executions/{id}`.

Put transport-neutral logic in `api/shared/` (for example
`sdk_execution_reads.py`). Keep the router as authentication, HTTP query
parsing/validation, and response/error translation. The later local-dispatch
stage must be able to call the same functions with a parent-built principal,
database session, and explicit arguments, with no FastAPI Request or router
import in the shared service. Existing repositories may be reused. Move route
helpers into shared only when required to avoid a reverse dependency.

Preserve all current scope and permission rules, workflow filters, used-by
counts, execution history keyset and legacy cursor behavior, pending execution
fallback, status mapping, and response shape. Inspect the SDK's actual query
names in `api/bifrost/executions.py`; preserve current external HTTP behavior.
Do not add compatibility fallbacks or broaden access. No new DTO unless
necessary; repository instructions place Pydantic models in
`api/shared/models.py`.

Add focused service unit tests and relevant HTTP E2E tests using `./test.sh`.
Test the same result and denial via the shared service and HTTP route where
practical. Run `./test.sh quality api`. Report exact commands/results and any
failure. The repo testing protocol forbids blind reruns, retry/skip/xfail, and
host pytest. Do not change file, artifact, knowledge, agent, AI, or dispatcher
code. No main merge; this stage is one part of the complete SDK product.
