# Engine SDK synchronous import local channel

Owner/reviewer: Codex. Executor: OpenCode
`opencode-go/muse-spark-1.3-contributor`. Worktree:
`/home/jack/GitHub/bifrost/.claude/worktrees/sdk-engine-local`.
Baseline: `edeb4e5aa`. This is a blocking stage for the complete engine SDK
fast path. No PR or main merge until all fixed operations and tests pass.

## Behavior

Cold Python module-name resolution and source fetch in a forked engine child
must call the existing `shared.sdk_modules` service in the worker parent,
without an API request or a child DB/S3 connection. Keep Redis fast paths.
External/non-engine callers retain existing HTTP/S3 behavior. A failed local
request never falls back to HTTP/S3. Preserve Solution-first namespace and
module resolution, sealed Solution restrictions, cache keys/TTLs, candidate
progression on 404, and successful source recaching.

Synchronous imports must use a **second dedicated pipe pair**, separate from
the existing async SDK channel. An import may block the child's event loop
while an async SDK request is awaiting a response; sharing the async channel
can deadlock on its lock. Create/close both pairs in template fork, process
handle, pool task tracking, child startup and teardown. Install a stdlib-only
synchronous child import transport before user code. Use the existing bounded
JSON frames and chunk ordering (64 KiB per frame), request IDs, deadlines,
size checks, EOF/protocol failure behavior, and sequential backpressure. The
parent may run both serving tasks concurrently. Import parent timeout 25 s,
child 30 s. Do not require a new HTTP endpoint or a DB connection in child.

Two explicit parent allowlisted operations:

- `modules.resolve`: child supplies only logical `name`; parent returns the
  shared resolver dict (module/package/namespace/not_found).
- `modules.fetch`: child supplies only candidate storage `path`; parent
  returns the shared source dict or a 404 error frame.

The parent builds `ModuleSourceScope` only from trusted dispatch context:
`principal.solution_id` plus a new `solution_global_repo_access` bool on
`LocalDispatchPrincipal`. Derive that bool from parent-owned
`context_data["solution_global_repo_access"]`, type-check it and fail closed
for malformed values. Workflow and service producers already set it. Frames
must not accept `solution_id`, `global_repo_access`, actor or caller identity.

`resolve_module_sync` and `get_module_sync` in `module_cache_sync.py` use the
new sync import transport after Redis misses only when it is installed. A
`modules.fetch` 404 advances to the next candidate; other errors raise a
clear import failure without HTTP/S3 fallback. Preserve current behavior
outside engine children. User-created threads may import: make the sync
transport request/response channel safe for concurrent callers.

`get_requirements_sync()` runs in a standalone setup helper before a forked
execution and before these channels exist. Leave its existing setup transport
unchanged in this stage; document it separately in the coverage audit.

## Blocking regression

`api/tests/e2e/platform/test_sdk_tables_local.py::TestSdkTablesLocalLiveService`
currently fails before table calls because a cold service source fetch gets
403 from the existing superuser-only HTTP module endpoint. This stage must
make that live service test pass with its module path fetched locally. Do not
skip, xfail, retry, or extend its timeout. The live workflow table test and
40 table unit tests already pass; preserve them.

## Ownership and checks

Allowed production files: `api/bifrost/_local_transport.py` or a focused new
`api/bifrost/_import_transport.py`, `api/src/core/module_cache_sync.py`,
`api/src/services/execution/template_process.py`, `process_pool.py`,
`simple_worker.py`, `worker.py`, and `sdk_local_dispatch.py` under that same
directory. Only minimal trusted context plumbing elsewhere if necessary;
report before editing a different production file. Tests under `api/tests/`.
Do not commit, push, merge, install packages, alter config/credentials, or
touch the primary checkout.

Tests: Redis hits avoid channel; resolve/fetch use shared service; 404
candidate progression; failure closes/fails locally; non-engine HTTP/S3 path;
malformed/oversized frames; large chunked source; trusted parent Solution
scope; missing/malformed global flag; child exit/parent shutdown; real-fork
cold entry/dynamic import with fixed-operation HTTP disabled; concurrent
async SDK request held while a synchronous import finishes; live service test
above; relevant Solution import regressions; `./test.sh quality api`.
Use `./test.sh` only. Report exact commands/results and broader suites not run.

Progress log: `/tmp/bifrost-sdk-import-local-opencode.jsonl`. Owner reviews
diff and independently verifies. Resume only by explicit session ID.
