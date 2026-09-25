# SDK video PlatformJob local enqueue and status

Executor: OpenCode. Reviewer: Codex. Work only in this worktree; no commit,
push, main merge, or primary checkout edit. Read AGENTS.md,
`docs/architecture/platform-jobs.md`, and the aggregate SDK transport plan.
The reviewer will merge the tested shared video service before execution.

Migrate the fixed `bifrost.artifacts.create_video` path to two named local
operations: enqueue video generation and poll that SDK video PlatformJob.
`ai.create_video` delegates to artifacts and inherits this path. The child
must keep its current timeout, polling interval, terminal result/error,
`requires_action`, and `ArtifactRef` behavior; external callers keep HTTP.
Use `api/shared/sdk_video.py` for both enqueue and status, the same service
used by HTTP. The local parent derives requester, org, workspace and
execution context, validates the same `VideoArtifactSpec` DTO, calls the
service's notification/commit/refresh/publish sequence, and never accepts
child actor or visibility claims. Poll only SDK video job IDs; other job
types must be 404. Keep the canonical PlatformJob scheduler and its shared
status contract, with no new queue or status endpoint.

Each poll must use a short parent DB session. Child polling must not hold a
DB connection or pin an API container. A failed local enqueue/poll never
falls back to HTTP or re-enqueues. Test HTTP/local accepted/status parity,
visibility, state transitions and error messages, timeout, external HTTP,
and one real fork with fixed video HTTP disabled and a fake job completion.
Check committed job/notification metadata and count fixed API requests (0
for local). Run focused `./test.sh` and `./test.sh quality api`; diagnose
failures without retries, skips, or timeout inflation.
