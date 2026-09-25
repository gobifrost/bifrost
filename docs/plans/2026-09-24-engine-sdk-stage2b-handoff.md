# Engine SDK local transport: integration reads

Executor: OpenCode `opencode-go/muse-spark-1.3-contributor`. Owner and final
reviewer: Codex. Worktree:
`/home/jack/GitHub/bifrost/.claude/worktrees/sdk-engine-local` on
`codex/sdk-engine-local`. Baseline: `88744be92`; preserve all committed work.
The complete plan is `docs/plans/2026-09-24-engine-sdk-local-transport.md`.

## Bounded task

Implement engine-local paths for `integrations.get`, `list_mappings`, and
`get_mapping`, while external SDK callers continue to use the current HTTP
routes. Extract each route's business behavior into a shared application
service that both the HTTP router and parent local dispatcher call. Preserve
response models, errors, secret registration, scope and Solution declared
connection behavior, OAuth token cascade and optional scope-based fetch, and
the external caller restrictions. The parent must derive the execution
identity and Solution ID from its trusted context, not child claims. Keep
child DB-free. Use the existing dedicated local channel and bounded frames.

Expected ownership: `api/bifrost/integrations.py`,
`api/bifrost/_local_transport.py`,
`api/src/services/execution/sdk_local_dispatch.py`,
`api/src/routers/cli.py`, a new `api/shared/sdk_integrations.py`, and focused
tests under `api/tests/unit/sdk/`, `api/tests/unit/execution/`, and
`api/tests/e2e/platform/`. Other files need owner review first. Do not touch
the primary checkout, configuration, credentials, dependencies, or unrelated
code. Do not commit or push.

## Acceptance

- Exactly these three facade methods select the local channel when installed;
  all external calls retain HTTP behavior. No local failure retries via HTTP.
- Parent dispatch validates the same request DTOs as HTTP and calls the same
  service; the service owns resolution and response construction.
- Scope, provider bypass, entity-ID lookup boundaries, declared Solution 424,
  missing integration/mapping, merged configs, external restrictions, OAuth
  decryption and token selection match the existing HTTP path.
- Parent payloads cannot forge actor, organization, or Solution ID. If a
  necessary trusted context field is unavailable, report the gap instead of
  taking it from the child or silently downgrading scope.
- Focused unit tests cover HTTP/local parity and denial; one live worker E2E
  proves these calls succeed with their Bifrost HTTP client disabled, and
  compares an external HTTP result. Keep tests deterministic and scoped.
- Run relevant focused tests via `./test.sh`, `./test.sh quality api`, and
  report exact commands, changed files, and broader suites not run. Stop and
  report a genuine blocker rather than broadening the stage.

No concurrent writer will modify the owned files during this stage. Progress
and results belong in the OpenCode session output. Resume by the recorded
session ID with an explicit scoped follow-up; do not use `--continue`.
