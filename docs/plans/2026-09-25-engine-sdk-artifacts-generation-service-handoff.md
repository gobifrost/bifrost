# Artifact generation shared service

Executor: OpenCode. Reviewer: Codex. Work only in this worktree. Do not
commit, push, merge, or touch the primary checkout.

Extract business behavior for the Python SDK artifact generation operations
`create_document`, `create_spreadsheet`, `create_text`, and `create_image`
from `api/src/routers/cli.py` into one or more shared application services
under `api/shared/`. The HTTP routes must call those same services. Preserve
the exact DTOs, response fields/statuses, actor and org scope, templates,
rendering, binary validation, provider calls and usage, error precedence,
storage, and metadata. Reuse the existing artifact/generation services and
the new `shared.sdk_artifacts` core service where appropriate; do not
duplicate storage logic. Services take explicit trusted actor, session, and
validated requests, not a FastAPI Request or child frame. Keep HTTP handlers
thin with transport-specific response construction only.

Do not add local dispatch yet. Do not touch `artifacts.write/read/list/
get_download_url`, video generation/PlatformJob, AI routes, the public SDK,
or execution dispatcher. Document which operations hold a DB session across
an external provider call and propose a safe split if needed; do not silently
change transaction semantics.

Add focused service tests and preserve HTTP coverage. Run relevant
`./test.sh` unit/E2E tests plus `./test.sh quality api`. Report exact commands
and results; diagnose failures without blind reruns, retries, skips, or
timeout increases.
