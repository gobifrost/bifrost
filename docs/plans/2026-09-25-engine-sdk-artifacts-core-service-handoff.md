# Artifact core shared service

Executor: OpenCode. Reviewer: Codex. Work only in this worktree. Do not
commit, push, merge, or touch the primary checkout.

Extract the business behavior of four fixed Python SDK artifact operations
from `api/src/routers/cli.py` into a shared application service used by the
HTTP handlers and later by the engine parent dispatcher:

- `artifacts.write` (`sdk_store_artifact`)
- `artifacts.read` (`sdk_read_artifact`)
- `artifacts.list` (`sdk_list_artifacts`)
- `artifacts.get_download_url` (`sdk_artifact_download_url`)

Use the existing `ArtifactService` and content validation, authorization,
workspace versioning, content disposition and inert download headers. Keep
the router thin; preserve exact response models, bytes, statuses, error
precedence, actor ownership and org scope for external SDK users. The HTTP
engine token represents a global superuser; supervised service tokens carry
their service org. Make the service take an explicit trusted actor/DB/session
and request data, never a FastAPI Request or child-provided actor claim.
Return transport-neutral results, with the HTTP adapter constructing
Response/StreamingResponse as needed. Do not add local dispatch yet and do
not touch generation routes (`document`, `spreadsheet`, `text`, `image`,
`video`) or AI routes. Do not create a parallel artifact repository/service
that duplicates `ArtifactService` storage behavior.

Add focused service tests (content validation, auth/scope, bytes, list
versions, signed URL disposition) and preserve existing HTTP tests. Run
relevant `./test.sh` unit/E2E tests and `./test.sh quality api`. Report
exact commands/results and failures; diagnose failures rather than adding
retries/skips/xfail. Do not edit `api/src/services/execution/*`,
`api/bifrost/_local_transport.py`, forms/context, roles, or tables.
