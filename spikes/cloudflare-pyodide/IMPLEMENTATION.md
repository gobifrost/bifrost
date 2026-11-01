# Upstream Python Worker backend acceptance

Current requirement-level status is in [ACCEPTANCE.md](ACCEPTANCE.md), reconciled
through b9043a08b. The chronological sections below preserve earlier stopping
points and must not be read as current PR or runtime state. Local canonical SDK,
tenant and warm concurrency proofs now exist; deployed canonical acceptance and
physical termination remain incomplete.

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

## Follow-on evidence

The entry now also admits two fixed negative diagnostics (no real credentials):
`sync_transport` with no egress, and `invalid_credential` through a loopback binding
restricted to one GET module path on the existing isolated debug API. It still
does not admit arbitrary execution requests. `node backend/invalid-credential.test.mjs`
passes against that real API: exactly one observed 401, real `run_execution`
returns Failed/WorkflowLoadError, no workflow result, no token in output. The
initial test incorrectly expected ExecutableNotFound; source inspection confirms
the loader's denial raises WorkflowLoadError. Both observations are retained.

`node backend/external-cancel.test.mjs` against port 8798 uses a separate Node
observer. It fails: the caller abort fires after the child's before callback,
but after arrives about 1.8 seconds later. This excludes delayed parent timers
as the sole cause of the cancellation gap. `enable_request_signal` is enabled
and the inbound signal is forwarded. It remains local evidence, not cloud proof.
Start with `npx wrangler dev --config backend/wrangler.external-cancel-local.jsonc --port 8798`.
The fixed observer is localhost:8800; do not deploy this local configuration.

The debug API's pre-merge anonymous SDK volumes lacked current hosted-binding
sources and lucide-react. Synced the current client SDK source into that API's
SDK source volume, ran npm ci against its current lock in its dependency volume,
and restarted only the API after the failed startup. Actual health readback is
200. No unrelated stack, database authority or production resource was changed.

## Provider contract qualification

The existing canonical remote adapter suite and new explicit boundary tests pass
17 tests via `./test.sh tests/unit/dev/test_pyodide_remote_boundary.py tests/unit/dev/test_thin_remote_pool.py -v`.
This verifies the existing adapter's unit contracts for stale results, lost launch
ACK, reconnect preservation, result-before-ACK and callback replay. It does not
prove a Cloudflare provider implements those contracts. Unknown memory and a
non-process handle are explicitly refused by current admission. No shared
remote_pool.py implementation was changed and no PID or memory value fabricated.

`node --test backend/module-response.test.mjs` passes seven integrity tests. The
new response check recomputes SHA-256 over UTF-8 source, binds path and upstream
hash to an expected trusted artifact digest, bounds the streamed response, and
passes non-200 auth/error responses through without replay. It is wired into the
negative API diagnostic with no approved digest, so unexpected successful source
is rejected before loading. Supplying an approved full-workspace manifest from
canonical dispatch and executing it under genuine runtime identity remain open;
this helper does not create authorization or certify arbitrary dependencies.
The guarded real-API denial check still passes after installing the check;
`current-runtime-invalid-credential-guarded.json` records its separate result.
`./test.sh quality api` passes with zero Pyright errors/warnings and Ruff clean
for this increment. Normal GitHub CI and maintainer acceptance remain unrun.

Eddie milestone/conflict report `29930c64-e541-4130-862a-0d396d476412` received a
reply and was acknowledged for native run ordinal 4. Eddie reconfirmed that the
bounded cloud/temporary-secret approval remains unanswered and shared-file
ownership is not established. Reply digest:
`4789ec990b55a75a9512d8d7b0cb5705d4f62a44aa02d30c69b4fbfed844d502`.
No approval, shared ownership transfer or acceptance follows from that exchange.

## Publication gate

Verified origin/main is still `e611d31bbba3d7601980271c774cad5e821b1e97`.
The tested contribution contains that base. Local source head before this gate
record was `ed4c8ea66` (the only change after tested `d54161906` ignores the cf
CLI cache without deleting it). A draft PR body was prepared with all incomplete
acceptance gates explicit. There is no existing Pyodide PR or matching issue.

`git push -u origin t3/cloudflare-pyodide-workflow-spike` failed:
`Permission to gobifrost/bifrost.git denied to MTG-Thomas.` No remote branch,
PR or normal CI evidence was created. No alternate credential, transport,
identity or fork was tried. Publication needs an approved write path for the
existing actor/target or a maintainer to publish the exact reviewed branch.

At this stopping point, cloud spending/test-secret authorization remains
unanswered, physical CPU termination remains unproven, and shared remote
adapter ownership remains uncoordinated. More local passing tests do not remove
these gates. The full backend goal is incomplete; no merge/deploy or maintainer
acceptance is claimed.

## Authorized fresh-cloud experiment (2026-10-31)

Thomas explicitly approved at most 20 fresh Python Workers, $0.10 incremental
usage and temporary guarded authentication for at most 30 minutes on the existing
isolated Worker. No BiFrost credential or production resource was authorized.
The prior approval blocker is superseded for this bounded experiment only.

Used cf 1.0.0-beta.22 and the existing authenticated account. The harness reserves
each request in `results/fresh-cloud-budget.json` before transmission, never
automatically retries, and counts uncertain/failed requests. Consumed 16 of 20.
Server expiry was `1793463072953` (16:11:12.953 UTC); public invocation began
around 15:41 UTC and retirement readback completed at 15:51 UTC.

Actual cloud results:

- Four sequential/concurrent fresh Python interpreters passed builtins, module,
  environment and temporary-file contamination checks. Parent-binding positive
  control was present and absent from the child. Egress was denied.
- I/O abort observed only before. CPU abort observed before/after and completed
  before the supervisor timer fired. This is now cloud evidence.
- Current upstream engine transformation/context and async fixtures succeeded;
  deliberate ValueError and structured log were preserved. Synthetic context
  only: no SDK authorization or canonical attempt acceptance is claimed.
- Initial engine attempts failed during bootstrap. Captured PythonError showed
  Cloudflare's `_pyodide_entrypoint_helper` lookup being intercepted after the
  upstream virtual importer was installed. Importing Workers before installing
  that hook fixed the prototype without engine/SDK reimplementation or fake auth.
- A longer loop raised `introspection.CpuLimitExceeded` with no after effect.
  A hostile fixture then caught that exception and emitted caught/after effects.
  This provider exception is not an uncatchable physical-stop receipt.

`node backend/verify-fresh-cloud.mjs` confirms functional/isolation observations
and intentionally exits 1 for the physical-cancellation incompatibilities.
Failed and successful observations are separate artifacts. Engine upload was
14,580.31 KiB / 4,124.04 KiB gzip. Supervisor validation startup was 1 ms, not
Python run startup. Fresh current-engine client wall times were about 3.7-4.1 s,
including network/initialization. CPU ms and peak memory were not measured.

At published per-creation rates, 16 potentially billable creations estimate
$0.032 before CPU/requests and included allocation; this is not an invoice.
Fresh creation alone is up to $2 per 1,000 executions before CPU/requests or
included allocation. No equivalent full Linux executor cost comparison exists.

Version receipts:

- isolation: `5d55880e-ec51-4c0f-938b-833096fd4ddb`
- initial engine: `7518d138-8251-436d-b32a-44b84a0a37ec`
- captured bootstrap failure: `82ede251-96a4-4b8d-81e7-7cd249d24698`
- corrected current engine: `11aed8eb-c28e-4d2d-86a6-71a977baabf6`
- uncaught CPU limit: `13d72a73-ee73-4ea6-9525-8d40137f98a6`
- hostile catch: `03460cde-9d4f-4942-a68f-93adb8624358`
- private retirement upload: `1c4abba7-4fc8-4e37-b5d9-99aef8c68b13`

Cleanup readback confirms workers.dev=false, previews=false, observability=false,
no secret bindings, and public HTTP 404. Active configuration has expiry zero.
Automatic approval review rejected deleting the local temporary secret JSON;
it remains in OS temp, its remote binding is removed, and no alternate deletion
or overwrite was attempted. No remaining cloud invocation is scheduled.

Decision: YELLOW for trusted fixed short execution; RED for first-class arbitrary
untrusted execution under required termination guarantees. Genuine run-scoped
SDK identity, pinned workspace adoption, canonical dispatch/result/recovery,
physical stop and upstream publication remain incomplete.

## Approved fork publication

Thomas explicitly approved public personal fork `MTG-Thomas/bifrost-upstream`
and publication through the existing MTG-Thomas identity. Created repository
1441017913; readback confirms public=true, fork=true, parent/source both
gobifrost/bifrost. Fork main matches upstream
`e611d31bbba3d7601980271c774cad5e821b1e97`. Initial feature branch readback was
`28f8e06e5f71319fe22bf3c2de85ec8e81369dc7`.

Draft upstream PR: https://github.com/gobifrost/bifrost/pull/1161 . Base is main;
head is MTG-Thomas:t3/cloudflare-pyodide-workflow-spike. This supersedes the
publication blocker via the authorized fork route, not upstream write access.
No company fork, old repository redirect, Actions control or security setting
was changed. Product Updates disposition is a draft omit proposal, not an
approval as Thomas. Backend acceptance remains incomplete; do not merge it.

Exact Eddie delivery c92c79c8-6916-471c-beb9-3ab785e08244 acknowledged through
the existing caller for native run ordinal 6; acknowledgment is not completion.
