# Updated SDK refresh and interrupted close

The exact SDK139596497/pure-wheel candidate now passes genuine run refresh and
redaction with the awaited normal-close wrapper. Command on CT128:

```sh
ct128-canonical-failure.py --sdk-refresh --revision 1
```

Exit0. The workflow verifies changed access identity and stable run refresh,
emits original/replacement access plus refresh through stdout/logging/exception,
and canonical error/logs redact all three. The actual API refresh returns200.
The peer succeeds with78, original/ended credentials401, live peer200, lost-ACK
replay preserves canonical state/log IDs, stale-owner and duplicate checks pass,
reservations2,2,1,0, canonical accounting and cleanup pass. Both attempt responses
record SDK close returned. Supervisor72.759s includes setup/work/cleanup, not
startup/CPU. Source/runtime/image pins remain explicit; no cloud call occurred.

## Cancellation negative control

The credential-free `ct128-sdk-probe.py --revision 6` uses the real updated SDK and
real HTTPX clients in local workerd. It injects a bounded Event wait around one
HTTPX `aclose`, preserving and later calling the actual method. It cancels the
coroutine awaiting SDK close, releases the wait, then observes the owner task.
This is not a real stalled network request, whole-execution abort or cloud stop.

Observed: the waiter is canceled; the SDK-owned async cleanup survives, closes
the async client and finishes. The sync client is still open without a follow-up
close. Explicitly awaiting SDK close again closes it. The ordinary close control
still shows both clients closed, settled owner and empty loop registry.

This is a remaining abnormal-cleanup gap, not a green cancellation result. The
current response diagnostic is emitted only after a normal close returns; no
terminal ACK or retirement is inferred from the canceled waiter. No unbounded
background owner, immortal waitUntil loop, fabricated ACK, provider termination
claim or upstream SDK rewrite was added to hide the negative control.

Follow-on OWNED-SDK-CLOSE.md now retains/shields the full SDK close task and
repairs this cooperative waiter case. The original partial-cleanup receipt stays
unchanged; real platform interruption and physical-stop guarantees remain open.

Ruff/Pyright pass for the changed Python harness; Node syntax and actual workerd
execution validate the probe. Providers were stopped with not-running readback;
owned test-stack cleanup left no running containers, volumes retained.
Raw evidence: `../results/sdk-refresh/receipt.json` and `interrupted-close.json`.
Verify with `node backend/verify-sdk-refresh-close.mjs`.

Next independent implementation: reject unsupported fetch transport options
before effects, preserving actual upstream SDK defaults. Interrupted close still
needs bounded completion ownership and deployed interruption proof; holding a
Python task or receiving a close exception cannot prove isolate/CPU stop.
Canonical pre-launch retirement, shared startup and deployed memory/isolation/
cancellation also remain open. Public admission stays disabled.
