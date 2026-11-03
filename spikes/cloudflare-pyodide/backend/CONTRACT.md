# Smallest executor contract

The [authenticated dispatch handle boundary](REMOTE-HANDLE-BOUNDARY.md) now
characterizes the actual Linux PID ACK, lost-ACK and callback replay paths.
It explains why an opaque isolate handle needs coordinated negotiation and
uncertainty handling; these are proposal requirements, not an existing API.

See [pre-claim eligibility](PRECLAIM-ELIGIBILITY.md) for the tested current
handler gate and the missing persisted runtime-selection contract. It prevents
unqualified registration from being mistaken for supported provider admission.

This is a proposed physical-runtime boundary, not an existing upstream API.
PostgreSQL and the existing upstream services retain scheduling, authorization,
attempt ownership, fences, recovery and terminal acceptance. A provider executes
one already-authorized attempt; it does not decide which business job runs next.

The minimum submission contains the canonical execution/attempt identity and
session generation, immutable code/workspace/dependency digests, approved runtime
capabilities, a trusted deadline, and scoped runtime credentials. Provider identity
and runtime identity are separate from the authorization principal. The provider
must reject unavailable capabilities before running workflow code.

The default Linux contract retains a positive PID ACK. This contribution adds
an experimental explicit opaque-invocation profile to the same pool, with
public admission still disabled. Its lifecycle must distinguish accepted, started,
invocation settled, stop requested, terminal submitted, and terminal acknowledged.
Normal completion requires awaited workflow work, completed response streams,
released returned RPC stubs and canonical terminal reconciliation; it does not
require destroying the isolate. Physical-stop observations are separate evidence
for abnormal cancellation/resource obligations. A disconnected HTTP caller is
not evidence of physical termination. See [lifecycle acceptance](LIFECYCLE.md).
Provider CPU/memory/deadline failures need explicit classifications; unavailable
resource measurements are null, never invented zeros.

Duplicate submission must resolve to the same canonical attempt and physical
handle. Reconciliation reports existing handles and pending terminal receipts;
it must not recreate an uncertain attempt. Terminal acceptance remains fenced by
the upstream transaction. Exactly-once terminal acceptance does not make external
workflow effects exactly once. No automatic redispatch after uncertain effects.

Each untrusted attempt needs a fresh execution security boundary with only its
own credentials and approved bindings. Logs, return values and exceptions pass
through upstream redaction and result semantics. Normal cleanup follows settled
required work and released capabilities. Abandoned attempts require capability
revocation, uncertain-effect reconciliation and bounded resource/accounting
evidence before reuse or recovery. Isolate reuse is a provider implementation
detail; tenant/attempt separation must still be qualified. Artifact hashes,
provider version, lifecycle observations and terminal acknowledgment form the
reviewable receipt.

## Current frontier

The contribution now includes shared engine stream capture/redaction, uncertain
ACK retention, signed runtime-profile metadata, an experimental opaque workflow
handle in the real pool, and inventory uncertainty guards. Linux defaults remain
the existing process implementation. Actual local pool/engine/SDK execution,
canonical lost-ACK replay and interrupted-provider recovery pass separately from
the deployed engine/SDK samples. The combined deployed contract remains
unaccepted; the public host still rejects the experimental profile. The companion
packages actual upstream source rather than another engine.

The [shared native pool qualification](SHARED-NATIVE-POOL.md) now runs two actual
tenant engine/SDK attempts through one local pool with fresh children. Failure
ACK loss, real ownership-generation rotation, independent credential revocation
and obligation/accounting release pass without relaunching either workflow.
The control connection is fixture-supplied; this is not public profile startup,
combined deployed control authentication or arbitrary untrusted admission.

The [private HTTP adapter](PRIVATE-HTTP-POOL.md) now composes that native pool
without rewriting scheduling, token issuance or terminal semantics. Transport
retention precedes physical dispatch, and duplicate calls preserve the original
attempt. It requires explicit provenance matching the caller's native context;
the [API preparation follow-on](API-WORKSPACE-PREPARE.md) now obtains hashes for
declared imports through the signed run's existing source endpoints. Dynamic
closure, atomic workspace revisions and automatic startup remain real gaps.

### Health, workflow outcome and provider settlement are separate facts

The [automatic recovery experiment](AUTOMATIC-RECOVERY.md) establishes why a
first-class physical boundary must preserve these distinctions:

| Fact | Authority and required behavior | Observed boundary |
| --- | --- | --- |
| Executor/session health | Existing owner lease, heartbeat and API reconciler | Real relay silence automatically ends the run and fences stale commands. A healthy relay does not prove its child invocation is alive. |
| Canonical workflow outcome | Existing authorization, claim, consumer and terminal ACK/recovery | Failed/Success persistence and credential revocation do not certify physical settlement or undo external effects. No replay after uncertain effects. |
| Physical invocation obligation | Supported provider lifecycle evidence tied to the accepted handle | Normal awaited response completion can settle an invocation without isolate destruction. Lost responses and logical failure keep resource uncertainty visible. |

Provider-only loss remained Running while the fixture relay heartbeated. Genuine
relay silence was then recovered by the existing API control replica, without
clock patches, direct recovery calls or a new queue. A per-invocation failure/
deadline policy is required for a relay that manages multiple invocations;
stopping the whole relay on one uncertain child can harm unrelated work. Any
additional abnormal physical settlement signal remains a proposal, not an existing upstream API.
Its authorization/accounting requirements cannot be replaced by a guest log,
caught exception, revoked token or disconnected HTTP caller.

The [companion deadline adapter](ATTEMPT-DEADLINE.md) now implements logical
delivery at the immutable cutoff through the actual upstream consumer, without
worker-wide recovery or physical release. Its owned native timer retains the
collection Task and propagates lost delivery receipts for replay. This is a
tested local increment, not a new public API or production admission grant.
The [late finite completion case](LATE-SETTLEMENT.md) now also proves normal
result/EOF settlement and lost-ACK replay after logical Timeout, with the provider
daemon still running. It does not require per-success isolate destruction or
certify abnormal noncooperative CPU cancellation.

### Historical isolation and cancellation probes

Fresh `WorkerLoader.load()` calls pass local sequential and parallel interpreter,
environment, module and temporary-file contamination probes. The parent-binding
positive control is present and absent from the child. Outbound networking is
denied. Started I/O cancellation prevents the later observed effect. These are
bounded local observations, not a complete isolation proof.

The CPU probe fails: it completes and delivers its later effect before the
supervisor's abort timer executes. There is no established physical termination
primitive here. Consequently this implementation cannot yet advertise arbitrary
untrusted Python or cancellation-equivalent first-class executor support.
Subsequent cloud engine/SDK evidence is recorded below; combined canonical
lifecycle adoption remains an acceptance gate. Do not conceal these gaps by
clearing globals, reporting an abort as termination, or adding another queue.

A second test moves the abort timer outside workerd and forwards the caller's
abort signal. It still observes the later CPU effect after the caller disconnects.
The synchronous cold-module HTTP client itself works under local Pyodide: the
real worker requests a module from the real isolated API, which rejects a fixed
invalid credential with 401. The worker preserves its existing WorkflowLoadError
semantics. This is negative authentication evidence, not authorized SDK success.

The bounded authorized cloud experiment subsequently confirmed fresh-isolate
state separation and execution of current upstream engine fixtures. CPU abort
still permits after effects. Cloudflare's CpuLimitExceeded exception can itself
be caught by Python code, which then emits another effect. Neither a request
abort nor this exception alone proves an uncatchable physical stop. See
`results/fresh-cloud-verification.json`; full executor acceptance remains false.

## Current lifecycle assessment

The later [cloud benchmarks](../dev/CLOUD-BENCHMARK.md) execute the real engine
and SDK with genuine run-scoped identity, including integration reads and table
writes. The [canonical failure/replay fixture](CANONICAL-FAILURE-REPAIRED.md)
separately proves upstream persistence/reconciliation locally. These are not yet
one accepted deployed canonical executor path.

The [incoming-disconnect cloud test](../dev/REQUEST-SIGNAL-CLOUD.md) proves the
JavaScript signal control arrived, but the Python CPU case does not establish
physical stop. Earlier local abort tests without that control cannot establish
that a CPU cancellation request reached the parent. Preserve them as historical
observations, not stronger cancellation claims.

The [native tail controls](TAIL-OBSERVER-QUALIFICATION.md) now distinguish normal
settlement (including bounded waitUntil work) from abnormal cancellation. An ok
tail also occurred on an aborted I/O invocation. Local CPU timing fields were
zero and unusable. Neither an ok tail nor silence is a stop certificate.

The deployed custom-limit catch-and-continue observation is specific to that
invocation limit. It does not establish absence of plan-level enforcement or
infinite execution. Remaining abnormal acceptance is no unauthorized
post-deadline effects, fenced/reconciled uncertain outcomes and bounded resource
use, using supported provider mechanisms. An undocumented terminate API is not
a prerequisite. Untrusted admission remains disabled pending that qualification.
