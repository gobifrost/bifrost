# Completion, cancellation and provider lifecycle

Normal success requires settled workflow work and released resources, not a
per-job isolate kill. This corrects earlier physical-termination wording without
discarding failed experiments or admitting untrusted workloads.

## Supported lifecycle contract

| Boundary | Required evidence | Current evidence / gap |
| --- | --- | --- |
| Normal completion | Await required engine/SDK work, finish streams, return result, release returned RPC stubs, reconcile canonical terminal ACK | Real engine/SDK cloud workloads pass separately from local canonical replay. Combined deployed contract remains unaccepted. |
| Bounded background work | Use waitUntil only for bounded ancillary work; do not return success before required effects finish | Local tail v2 observes background completion after response and before native tail. This is a diagnostic, not durable job ownership. |
| Deadline/cancel | Prevent unauthorized new effects, reconcile in-flight uncertain effects, reject stale terminal reports and retain resource/accounting obligations | Local canonical fencing/recovery tests exist. Deployed noncooperative Python cancellation and resource settlement remain unqualified. |
| Isolation | Separate tenant/attempt code, state, credentials and capabilities; reject unsupported workloads before effects | Fresh-child contamination controls pass. Successful completion does not prove isolation of later executions or authorize shared interpreter reuse. |

[RPC lifecycle](https://developers.cloudflare.com/workers/runtime-apis/rpc/lifecycle/)
documents explicit disposal of returned RPC object stubs, including containing
objects and duplicated stubs. Disposal releases references; the server disposer
runs later and is not a synchronous CPU-stop receipt. The current tail fixture
uses fetch responses consumed as JSON and no returned workflow RpcTarget.
The earlier WorkerStub/getEntrypoint inspection did not test returned RPC object
disposal. Any future RPC-result path must dispose its returned stubs explicitly.

[Context](https://developers.cloudflare.com/workers/runtime-apis/context/)
documents a shared HTTP waitUntil allowance of up to 30 seconds after response or
disconnect. Await required work before returning; finish response streams.
Do not use waitUntil to simulate a persistent executor, hold an attempt forever,
or replace PostgreSQL scheduling/recovery. No Queue, Durable Object or D1 is
introduced by this lifecycle assessment.

## Limits are distinct mechanisms

[Dynamic custom limits](https://developers.cloudflare.com/dynamic-workers/usage/limits/)
throw an exception at CPU/subrequest limits. Both supported placements are
described in the [API reference](https://developers.cloudflare.com/dynamic-workers/api-reference/).
The deployed Python catch-and-continue result proves that this exception alone
is insufficient to guarantee no subsequent effect. It does not establish an
infinite execution path or absence of all provider enforcement.

[General Workers limits](https://developers.cloudflare.com/workers/platform/limits/)
separately describe execution termination on consistent CPU overruns, with
isolate flexibility. Plan/configured bounds and their actual deployed behavior
must be qualified independently. Local workerd enforcement observations do not
establish deployed behavior. No limit increase or security-setting change was
made for this assessment.

## Evidence and next bounded qualification

The [streamed invocation qualification](STREAMED-INVOCATION.md) now receives an
opaque accepted frame before workflow collection completes, keeps the response
open through required work, consumes its terminal frame and EOF, and reconciles
the result through the real upstream consumer. It uses no waitUntil. Stream
cancellation closes future fixture capability use; a unit negative control
explicitly observes already-started work continuing. This is normal completion
and transport evidence, not physical-stop acceptance. No returned RpcTarget
object is introduced by this HTTP path.

The [accepted-stream-loss fixture](STREAM-LOSS-RECONCILIATION.md) now preserves
an uncertain native invocation, owner fencing and the original deadline while
recovering an upstream Launcher-retained result. It does not infer failure or
retry from a broken response. The control reconnect is simulated; recovery of
a terminal result never received by the launcher remains unqualified.

The [native inventory repair](INVENTORY-UNCERTAINTY.md) now refuses to interpret
an omitted opaque handle as Linux process exit or to issue Linux termination
for an unowned live invocation. It preserves owner fencing and normal retained
terminal replay. Refusal retains uncertainty; it is not provider settlement.

The [streamed provider-interruption follow-on](STREAM-PROVIDER-INTERRUPTION.md)
now loses the provider before the launcher receives any terminal event. Real
canonical lost-worker recovery records Failed, revokes run credentials and
drains redacted logs without redispatch. The native invocation remains held.
An independently stopped local Linux provider is not deployed isolate-stop proof;
automatic per-invocation provider-loss observation and settlement remain open.
The [existing API control qualification](AUTOMATIC-RECOVERY.md) now recovers real
relay silence without direct fixture recovery calls or clock manipulation. Its
negative control preserves Running while a relay stays healthy after provider
loss, so executor heartbeat must not be treated as invocation liveness.
The [per-attempt deadline adapter](ATTEMPT-DEADLINE.md) now persists Timeout at
the original cutoff while the relay stays Ready and the physical obligation
stays held. It propagates a lost post-commit delivery receipt and replays through
the existing consumer. No CPU-stop or deployed-accounting claim follows.
The [finite late-completion qualification](LATE-SETTLEMENT.md) now receives real
engine Success after logical Timeout and releases its held handle through the
normal result/ACK path, preserving the canonical outcome. The provider is still
running before fixture teardown: normal invocation settlement needs no process
kill. This is finite awaited-work evidence, not arbitrary-code cancellation.

The [per-attempt HTTP component](HTTP-INVOCATION.md) now owns the HTTP session
through terminal EOF independently of scheduling authority. The same real local
late-completion path passes through it; this extraction adds no RPC object or
waitUntil lifetime. Same-relay multi-attempt execution remains unqualified.

[Tail receipts](TAIL-OBSERVER-QUALIFICATION.md) preserve v1's missing background
control and v2's five successful native tails. The combined observer/handle
retention change does not isolate its two causes. The aborted I/O case reported
ok; local CPU/wall fields were zero even during a long loop. Therefore neither
outcome nor those measurements certifies abnormal cancellation or accounting.
No new cloud invocation occurred for these controls.

The [local deadline gate](EGRESS-DEADLINE.md) now refuses three late requests
while actual Python catches failures and continues. Real engine canonical
failure/replay also passes with it installed. A unit counterexample preserves a
write already dispatched before close, committing afterward with a lost response.
The [canonical-cutoff qualification](CANONICAL-DEADLINE.md) now consumes the
existing absolute dispatch deadline and preserves a lost provider response as
uncertain across expiry. A trusted observer's direct API write still succeeds
while the canonical run is live; this is not a child egress escape. The
[retained-result follow-on](DEADLINE-RETAINED.md) now reconciles that uncertainty
locally through canonical ownership without redispatch or a new cutoff.
Retain the provider obligation until settlement is established. Revoking a token
alone cannot establish physical CPU stop.
If network credentials or unrestricted egress escape the gate, post-deadline
effects remain an incompatibility; rejecting a terminal result cannot undo them.

A deployed resource-enforcement qualification remains separate: correlate an
authorized attempt, received cancellation, independent effects and provider
outcome/accounting using the supported limits. Do not infer stop from exceptions,
silence, response completion or killing the Linux harness. Do not require an
undocumented terminate API. Observer receipts must be parent-authenticated: an
untrusted SDK run can forge its own log messages, so a log prefix is insufficient.
This document authorizes no new cloud window, access, spending or admission.

Fresh-child benchmark round trips of 5.2–10.5 seconds include execution and
transport/interop overhead. They are not isolated startup measurements and are
not directly comparable to Containers reservation-to-start measurements.
Readiness remains YELLOW for the demonstrated trusted subset; arbitrary untrusted
execution is not accepted pending the abnormal and isolation gates above.
