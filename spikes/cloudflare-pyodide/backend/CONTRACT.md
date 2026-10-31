# Smallest executor contract

This is a proposed physical-runtime boundary, not an existing upstream API.
PostgreSQL and the existing upstream services retain scheduling, authorization,
attempt ownership, fences, recovery and terminal acceptance. A provider executes
one already-authorized attempt; it does not decide which business job runs next.

The minimum submission contains the canonical execution/attempt identity and
session generation, immutable code/workspace/dependency digests, approved runtime
capabilities, a trusted deadline, and scoped runtime credentials. Provider identity
and runtime identity are separate from the authorization principal. The provider
must reject unavailable capabilities before running workflow code.

The physical handle is opaque: a Linux PID is one implementation, not the wire
contract. Its lifecycle must distinguish accepted, started, stop requested,
physically terminated, terminal submitted, and terminal acknowledged. An abort
request or disconnected HTTP caller is not evidence of physical termination.
Provider CPU/memory/deadline failures need explicit classifications; unavailable
resource measurements are null, never invented zeros.

Duplicate submission must resolve to the same canonical attempt and physical
handle. Reconciliation reports existing handles and pending terminal receipts;
it must not recreate an uncertain attempt. Terminal acceptance remains fenced by
the upstream transaction. Exactly-once terminal acceptance does not make external
workflow effects exactly once. No automatic redispatch after uncertain effects.

Each untrusted attempt needs a fresh execution security boundary with only its
own credentials and approved bindings. Logs, return values and exceptions pass
through upstream redaction and result semantics. Provider cleanup follows
confirmed physical termination, including abandoned attempts. Artifact hashes,
provider version, start/stop observations and terminal acknowledgment form the
reviewable receipt.

## Current frontier

The contribution currently introduces only trusted public URL metadata and
pluggable resource observation in the real engine/worker. Linux defaults remain
the existing process implementation. It does not implement the lifecycle above.
The companion packages actual upstream source rather than another engine.

Fresh `WorkerLoader.load()` calls pass local sequential and parallel interpreter,
environment, module and temporary-file contamination probes. The parent-binding
positive control is present and absent from the child. Outbound networking is
denied. Started I/O cancellation prevents the later observed effect. These are
bounded local observations, not a complete isolation proof.

The CPU probe fails: it completes and delivers its later effect before the
supervisor's abort timer executes. There is no established physical termination
primitive here. Consequently this implementation cannot yet advertise arbitrary
untrusted Python or cancellation-equivalent first-class executor support.
Cloud testing, genuine run-scoped SDK execution and canonical lifecycle adoption
remain acceptance gates. Do not conceal these gaps by clearing globals, reporting
an abort as termination, or adding a second execution queue.

A second test moves the abort timer outside workerd and forwards the caller's
abort signal. It still observes the later CPU effect after the caller disconnects.
The synchronous cold-module HTTP client itself works under local Pyodide: the
real worker requests a module from the real isolated API, which rejects a fixed
invalid credential with 401. The worker preserves its existing WorkflowLoadError
semantics. This is negative authentication evidence, not authorized SDK success.
