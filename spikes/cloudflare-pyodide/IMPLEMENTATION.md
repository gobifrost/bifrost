# Upstream Python Worker backend acceptance

Standing goal received 2026-10-31; sole writer on existing
`t3/cloudflare-pyodide-workflow-spike`. No edits to Containers owner branch/resources.
Baseline spike commits and evidence remain historical and immutable.
Current verified upstream: `e611d31bbba3d7601980271c774cad5e821b1e97`, merged here.

## Reconciled authority

`elastic-execution.md`'s two backends describe PlatformJobs, not an existing
generic workflow backend interface. The thin-runtime report distinguishes the
API's canonical PostgreSQL control/authorization from physical providers.
The six-operation abstraction remains a proposal. No new business queue,
authority database, silent fallback or production migration change is authorized.

The existing Worker `bifrost-pyodide-spike-ddf316c1` is present and cf authentication
is valid. Prior final version/404 readback are historical; no live mutation made
in this increment yet. Local debug project `bifrost-debug-19e8149c` and spike
container are running. No pending PR or cloud operation was found in this thread.
Exact Eddie delivery `3edc293f-7c82-4fbd-93d5-6b4177e513ef` acknowledged through
existing caller for native run ordinal 4; receipt says agent_acknowledged=true,
terminal=false, completed=false. Acknowledgment is not acceptance/completion.

## Required finish line

- [ ] Real engine and SDK execute under genuine authorized run-scoped identity.
- [ ] Pinned engine/SDK/dependency/workspace provenance; actual virtual module loading.
- [ ] Fresh per-attempt interpreter/isolate; hostile state and credential separation.
- [ ] Eligibility rejects unsupported requirements before workflow effects.
- [ ] Duplicate dispatch cannot start a second physical attempt; no automatic retry
      of uncertain side effects. Current upstream claim/fence remains authoritative.
- [ ] Terminal reporting is idempotent, transaction-fenced and acknowledged.
- [ ] Disconnect/reconnect reconciles active attempts and pending terminals without
      recreating attempts or losing terminal ownership.
- [ ] CPU and I/O cancellation physically stop the runtime; stop receipt differs
      from confirmed termination; deadlines use trusted supervisor clocks.
- [ ] Provider CPU/resource failures map through upstream terminal mechanisms.
- [ ] Logs, stdout, results, exceptions and redaction retain upstream meaning.
- [ ] Cleanup covers interrupted, stale, canceled and completed attempts.
- [ ] Linux behavior remains compatible with focused tests and normal CI.
- [ ] Actual deployed supported workflows pass end-to-end acceptance; local/unit/
      cloud primitive evidence is distinguished from authoritative execution.
- [ ] Clean reviewable contribution; maintainer review/acceptance recorded separately.

## Next increment

Test `WorkerLoader.load()` (fresh Worker per call), never cache-by-ID `get()` for
untrusted attempts. First prove isolation and termination without new authority,
network access or credentials. Dynamic Workers have a distinct creation charge;
cloud usage needs an explicit bounded allowance before exceeding existing scope.
Implement locally and prepare a concrete limited cloud test for review.

Shared interface note for Eddie: a physical runtime handle must not assume a PID,
but attempt identity, session generation and canonical DB fence must remain
separate. Do not independently change the Containers owner's shared seam.

## Tested increment (2026-10-31)

Added trusted URL metadata and per-attempt resource observation to the real
engine/worker. Default Linux accounting is preserved; absent provider metrics
are null. Current worker closure imports in a fresh local Python Dynamic Worker
with Redis's pure-Python wheel and the actual virtual loader. This import is
not proof of authorized module retrieval. The older exact engine closure also
executes JSON in a fresh local Worker with synthetic identity.

`./test.sh tests/unit/test_execution_runtime_metadata.py tests/unit/test_solution_module_isolation.py -v`
passed 7 tests. Credential/redaction consumers passed 14 tests:
`./test.sh tests/unit/execution/test_worker_credentials.py tests/unit/engine/test_engine_token_redaction.py tests/unit/test_engine_token_lifetime.py -v`.
`./test.sh quality api` passed pyright and Ruff. Commands ran through the isolated
Docker CLI tools container, with the test runner's Docker-daemon-visible log
mount corrected after an initial permission failure. JUnit is retained in results.

`node backend/isolation.test.mjs` against local Wrangler port 8795 deliberately
fails its CPU-cancellation assertion. Revision 5 evidence records clean sequential
and parallel builtins/environment/module/tmp-file canaries, parent-binding
separation, denied egress, and started I/O cancellation with no later observed
effect. CPU work completes with both effects before abort is requested. It is a
material blocker, not a passing cancellation test. Actual cloud behavior remains
unverified for this fresh-isolate design.

Reproduction after preparing the historical package and syncing locked dependencies:
run `backend/prepare-runtime.py` from the repository, copy backend into `/spike`
in the tools container, then run `python backend/pack-engine.py --current-runtime`.
Start `npx wrangler dev --config backend/wrangler.runtime-local.jsonc --port 8797`
there; POST `{"action":"inspect"}` to inspect the current closure. The runtime
entry intentionally rejects execution until canonical authorization is connected.
For isolation, start the equivalent `wrangler.local.jsonc` on port 8795 and run
`node backend/isolation.test.mjs`. Generated bundles and source copies are ignored;
raw manifests retain source and module hashes.

No new deployment, credential rotation or Dynamic Worker charge was authorized.
The concrete pending request is at most 20 fresh Workers, at most $0.10 incremental
cost, and a temporary guarded test secret for at most 30 minutes on the existing
isolated Worker. Eddie confirmed the allowance is not approved. No remote_pool.py
changes were made; positive-PID/cgroup assumptions await shared seam coordination.
