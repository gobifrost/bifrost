# Shared SDK video PlatformJob enqueue and observation

Executor: OpenCode. Reviewer: Codex. Work only in this worktree. Do not
commit, push, merge, or touch the primary checkout. Read AGENTS.md,
`docs/architecture/platform-jobs.md`, and the aggregate transport plan.

Extract the existing `POST /api/sdk/artifacts/video` orchestration in
`api/src/routers/cli.py` to a shared parent-side SDK service. The route
must be a thin adapter. Retain the canonical
`SDK_VIDEO_GENERATION_DEFINITION` PlatformJob, requester/org/resource
metadata, notification creation, commit/refresh/update ordering, accepted
response, 202 status and Location header. Do not create another job/status
system. The service will later be called by the engine-local dispatcher.

Extract the visibility/serialization logic for `GET /api/platform-jobs/{id}`
from its router into a shared service usable for local SDK video polling.
HTTP and local must share the same requester/org visibility rule and
`PlatformJobPublic` shape. Do not expand visibility or add a generic local
job API beyond this fixed SDK video status call. Preserve cancellation and
other platform-job routes' behavior.

Add focused unit tests for enqueue metadata, notification failure, commit
ordering, status visibility and serialization, plus one HTTP E2E happy path.
Run targeted `./test.sh` and `./test.sh quality api`. Diagnose failures;
no retries, skips, or timeout inflation. Keep this stage free of local
dispatcher, child SDK, and transport edits.
