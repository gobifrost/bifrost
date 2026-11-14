# Own cooperative SDK close completion

SDK-REFRESH-CLOSE.md preserves the negative control: canceling the coroutine
awaiting upstream SDK close lets its async pool cleanup finish but leaves sync
close unexecuted. The companion now owns the complete public close call in a
retained asyncio Task and shields it from cancellation of its waiter. Upstream
client code is unchanged. Completed task references are removed; exceptions remain
the normal caller's exceptions and are observed without credential-bearing logs.

`sdk-cleanup-code.mjs` provides the small Python completion module injected by
runtime-code.mjs. It is loaded before the virtual workspace hook, not resolved
from user workspace source. No new queue, scheduler, thread, waitUntil loop,
retry or durable result authority. It cannot extend Cloudflare invocation lifetime.

## Evidence

Three Dockerized ownership tests pass: normal close awaited once, canceled waiter
leaves its owned close running to completion, and close errors propagate. The
tests use synthetic close coroutines; they are not SDK compatibility evidence.
Ruff/Pyright pass for the extracted Python module and tests.

Actual updated SDK probe (`ct128-sdk-probe.py --revision 7`) repeats the preserved
bounded HTTPX-close delay/canceled-waiter control in local workerd. One owned Task
remains at cancellation; after releasing the injected wait, both real async and
sync clients are closed without a follow-up close and the pending set is empty.
The previous partial failure remains in the earlier raw receipt. This is cooperative
Python waiter cancellation, not real network loss, provider interruption or CPU stop.

Real engine/SDK controls also pass:

* Updated SDK with actual refresh/redaction:
  `ct128-canonical-failure.py --sdk-refresh --revision 2`, exit0,92.572s supervisor.
* Prior SDK bundle:
  `ct128-canonical-failure.py --api-prepare --revision 92`, exit0,74.406s supervisor.

Both perform real config/table operations and retain expected canonical errors/
Success78, redaction, peer independence, lost-ACK/current-owner replay, stale
report rejection, duplicate reuse,401 revocation, accounting2,2,1,0 and cleanup.
Both responses record close returned. Owned containers stopped; volumes remain.
Durations include setup/work/cleanup and guest scheduling; no startup/CPU or speed
comparison is inferred.

Raw source/runtime/image-bound receipts: `../results/owned-sdk-close/`.
Verify with `node backend/verify-owned-sdk-close.mjs`. Native tests were run in
the existing cached image, network disabled, .5CPU/1GiB/64PIDs. Actual cf dev
providers retain their existing1CPU/2GiB bounds; no laptop Docker or cloud calls.

## Remaining guarantees

Strong Python references and a caught CancelledError do not prove survival after
actual platform request cancellation or physical CPU termination. No terminal ACK
is fabricated when the caller is canceled; the close-returned header is only sent
on normal completion. Canonical uncertainty/resource obligations remain held by
the existing attempt/result machinery. A close failure after waiter cancellation
is not converted into success. Deployed interruption/limits and recovery still
need proof; admission remains disabled. Custom transport options and provider-owned
module-name collisions also need explicit pre-effect qualification.
