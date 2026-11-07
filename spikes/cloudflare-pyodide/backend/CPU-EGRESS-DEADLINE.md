# CPU continuation and effect refusal are different guarantees

Actual local Python completed a finite 20,000,000-iteration loop and then attempted
an HTTP effect 1,813 ms after diagnostic activation. The existing trusted parent
`createAttemptEgress` returned 410 at its fixed 500 ms cutoff. The independent Node
observer received started but no after request. The zero-loop control attempted
its effect after 3 ms, received 200, and the observer recorded started/after.
These are parent-clock wall intervals, not measured CPU milliseconds.

No timer callback interrupts the child. The gate checks time when the child asks
to send another request. Python continues and returns its checksum
199999990000000 and effect_status410. This directly demonstrates effect refusal
while computation has continued; it does not demonstrate physical termination.

The cutoff here is deliberately a **synthetic diagnostic activation**, fixed once
at the first started callback. It is not a canonical workflow timeout and must not
reset a dispatched deadline. The actual canonical adapter separately uses upstream's
absolute workflow_deadline and owns timeout/result reconciliation. No platform
run credentials, database state or production dispatch were involved in this test.

## Smallest execution contract: the remaining distinction

This is an assessment, not a new upstream API. Existing source remains authoritative:

* The canonical platform owns authorization, claim/attempt identity, session fencing,
  immutable source/dependency provenance, deadline, terminal persistence and recovery.
* A physical backend accepts one identified attempt and reports a real invocation
  handle, observed outcome and outstanding resource obligations. A logical terminal
  result is distinct from physical settlement and safe capacity release.
* Normal completion awaits work and consumes/closes response/RPC resources. It needs
  neither a Linux process kill nor an isolate-destruction receipt after every job.
* Abnormal timeout/cancellation must fence results and prevent newly authorized
  effects. Already dispatched writes remain potentially committed and need upstream
  reconciliation; capability closure cannot undo them.
* Resource enforcement remains a separate backend guarantee. The checked-out Linux
  `ProcessPoolManager._check_timeouts` calls `_kill_process` before reporting timeout
  and removing the execution. Its signal escalation is not supplied by a guest
  exception or by returning HTTP410 from an effect capability.

Consequently scoped capability refusal can satisfy part of the abnormal-effects
contract, but cannot alone establish bounded resource use or justify releasing
capacity for arbitrary untrusted code. The existing local CPU-placement experiments
already show both configured placements failing to interrupt Python on this emulator;
they were not repeated. Cloud custom invocation limits document an exception, while
plan enforcement and native termination require their own deployed evidence. Do not
infer infinite execution or absent platform enforcement from these local findings.

## Reproduction and evidence

Actual command: `timeout -k 5 120 python3 ct128-sdk-probe.py --cpu-egress --revision 1`
in the exclusive CT128 packet. It starts cached cf CLI dev, then the existing
container-loopback HTTP observer driver. Two fresh Python children, fixed code,
allowlisted egress only; no child credentials or arbitrary network destination.
The observer server is independent of workerd, within the same constrained container.

CT128 remains 2 CPU/8 GiB/no swap; provider 1 CPU/2 GiB/128 PIDs/network none.
Same pinned cf1.0.0-beta.26, workerd1.20261023.1 and publisher-source bundle
3c3fd5a4.... No ABI remeasurement is claimed. `../results/cpu-egress/v1.json`
records exact tested packet hashes, HTTP200 observation and stopped owned container.
No OOM, cloud invocation, deployment, new package, credential, security setting,
resource expansion or volume removal. The outer container is stopped after results
are collected, never used as a physical-termination signal.

`node backend/verify-cpu-egress.mjs` checks hashes and the observed distinction.
Five attempt-egress regressions pass, including commit-after-close uncertainty.
The Python harness passes bounded guest Ruff/Pyright; Node syntax and existing
HTTP-context verifiers pass. No broad platform suite was rerun for fixture changes.

Next genuine blocker: independently qualify deployed abnormal CPU enforcement,
resource settlement and isolation under an authorized bounded cloud window. Keep
admission disabled. This local capability result does not reopen that window.
