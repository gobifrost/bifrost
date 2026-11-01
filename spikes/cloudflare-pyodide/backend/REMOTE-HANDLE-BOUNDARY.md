# Authenticated dispatch: remaining physical-handle boundary

This is an upstream extension proposal and tested characterization of the
current Linux contract, **not an implemented provider API**. Actual workerd
engine/SDK success is documented separately in WORKERD-SDK-QUALIFICATION.md.
There is no combined signed-control/isolate-dispatch acceptance result yet.

## Current source, not an assumed generic API

At tested platform source `45bcb38dd6412d64aa1d308479178904cd2ceab6`:

- `api/src/worker/remote/launcher.py:Launcher.command` handles workflow/service
  commands by `LinuxTemplate.fork`, creates a Linux `Child` and acknowledges a
  PID. Its termination/sampling facilities also own real OS processes.
- `remote_pool.py:ThinRemoteProcessPool._remote_pid_from_ack` requires a positive
  integer PID and explicitly rejects booleans. Workflow routing validates that
  ACK after sending the command.
- A `ConnectionError`/`TimeoutError` while awaiting ACK preserves the route for
  inventory reconciliation. An invalid-PID ACK instead reaches generic dispatch
  failure cleanup: the route/context is removed and its later result is ignored.
- The wire already has route IDs, generation fences, retained terminal events
  and ACKs. It does not currently negotiate a generic isolate-handle contract.
  An arbitrary `runtime_handle` string used by the unit fixture is deliberately
  not an upstream field definition.

Therefore replacing the PID with a made-up integer, or simply sending a new
opaque field, is unsafe. The latter would be rejected **after dispatch**, not
before effects. The current Linux producer emits real PIDs; these tests are not
evidence that a healthy Linux launcher produces malformed ACKs or that an
actual provider effect occurred in the mocked cases.

The process supervision dependency is an incompatible OS contract for Pyodide,
not a missing pure-Python package or PyEmscripten wheel. Compiling `os.fork`,
process signals or Linux cgroup supervision cannot make them available inside
a Cloudflare isolate. Keep the canonical API owner on upstream infrastructure;
generalize only the physical-provider boundary after coordinated review.

## Smallest proposed contract

1. **Authority remains upstream.** Admission, canonical attempt/claim, owner
   session, runtime credential issuance, terminal acceptance and recovery stay
   in the existing PostgreSQL/services contract. Bind approved runtime and
   immutable artifact requirements before claim; workflow-supplied flags and
   import scans cannot authorize an executor. No second business queue.
2. **Physical identity is separate.** Negotiate a versioned provider profile
   before sending work, retaining the current positive-PID Linux ACK unchanged
   for legacy peers. A future tagged opaque handle is bound to the existing
   route/attempt/session, not a new authority ID. Missing/invalid ACK data after
   a possibly accepted launch is an uncertainty requiring reconciliation,
   rather than permission to relaunch or fabricate terminal failure.
3. **Lifecycle evidence has explicit meaning.** Accepted, started, stop
   requested, physically stopped, terminal submitted and terminal acknowledged
   are distinct. Reconnect inventory identifies the same physical run and
   retained terminal event without starting it again. Preserve replay fencing,
   error/result/redaction semantics and unknown resource metrics as null.
4. **Qualification is enforced.** Fresh untrusted isolation, trusted deadline
   enforcement, independently verified physical stop and cleanup must be
   demonstrated for the advertised profile. The measured workerd post-abort
   CPU effect currently fails this requirement. A tagged handle, revoked token,
   caught CPU exception or killed Linux harness cannot repair that failure.

This requires a coordinated change to launcher/remote-pool/inventory and
canonical placement/recovery interfaces, not merely a new transport callback.
Signed hello and runtime credential protocol 2 already work; they do not by
themselves negotiate a different physical-execution guarantee. Unsupported
requirements must remain unclaimed before credentials/effects. Linux behavior
and existing wire peers must keep their current meaning.

## Executed characterization

`api/tests/unit/dev/test_pyodide_remote_ack_boundary.py` uses the **actual**
ThinRemoteProcessPool with explicit transport/Redis doubles. It covers legacy
positive PID, lost ACK, non-PID ACK and boolean PID. A selected existing callback
failure/replay regression runs alongside it. These are unit/source-contract
tests; no SDK, provider or physical process is executed by them.

In CT128's unchanged 2 CPU / 4 GiB allocation, five cases passed with no skips,
errors or failures in 0.37 seconds. Targeted Docker Ruff passed. The supervisor
completed in 97.200 seconds, used an exclusive checkout/project and stopped
owned containers; existing volumes and unrelated projects were preserved.
`./test.sh` performed its normal reset of this new task-owned test project.
No cloud, laptop Docker, new credential or shared production source change.

Exact commands/JUnit/source hashes: `results/remote-ack-boundary/`. Base source
is the commit above plus the single uncommitted test fixture SHA256
`07154cf159a1d57e8abe5e0bfe5af4d82e7235d7924720e1a2dd20d53e57c87a`.
The receipt also hashes all four unchanged shared control files. Command:

```sh
./test.sh tests/unit/dev/test_pyodide_remote_ack_boundary.py tests/unit/dev/test_thin_remote_pool.py::test_callback_failure_retains_route_until_durable_result_replay -q
```

The sole Pyodide owner requested coordination through the existing caller and
acknowledged its reply. No verified per-file handoff exists, so shared control
files were not edited. A completed neighboring run is not a release of ownership;
the proposed handle is not maintainer acceptance or permission to change those
interfaces. Independent fixture work can continue, including a bounded Linux
control reference where feasible; deployment and untrusted admission stay held.
