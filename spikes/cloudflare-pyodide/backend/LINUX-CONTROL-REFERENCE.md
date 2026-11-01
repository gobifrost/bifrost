# Linux control-path reference

The existing CT128 guest executed one real upstream Linux workflow through the
signed WebSocket control path, native template fork, engine, SDK and isolated API.
This supplies a reference for the proposed physical-handle boundary. It does not
implement or qualify Pyodide adoption of that control path.

API source: `f417d03f5c8d5e3a182e68f69e2bf601a3b60125`. The source checkout was
clean; task-owned fixture hashes and exact commands are in the retained receipts.
No shared remote-pool, protocol, launcher or host source was changed.

The bootstrap first proved signed ownership and startup/cleanup of both actual
Linux templates, without inventory, admission or workflow execution. The reference
then observed one workflow command, one positive native PID acknowledgement and
one result event. A workspace helper, configuration read, integration read with
encrypted synthetic secret and table upsert ran through the real SDK. Independent
SQL document readback was **70**; canonical execution status was `Success`.
Result/log redaction assertions passed, the parent signing secret was absent,
the retained terminal event was acknowledged, and the run credential returned
401 after completion. Both fixtures stopped their templates and removed their
owned worker rows. The supervisor observed no running containers after cleanup;
volumes were retained.

The existing guest remained 2 CPU / 4 GiB. Each test runner was capped at one CPU,
2 GiB, 128 PIDs, with a 240-second supervisor wall guard and 24-GiB used-disk
guard. Bootstrap and reference supervisors completed in 59.19 and 62.21 seconds.
These include stack work and are not workflow latency or CPU benchmarks. Bootstrap
cgroup peak was 339,484,672 bytes; this is not a comparable full-workflow or Cloud
isolate peak. There were zero Cloudflare invocations.

Evidence: `../results/linux-control-reference/`, including exact Compose invocation,
exit statuses, fixture provenance, output and cleanup. Host-spool and local receipt
SHA-256 values matched. Verify retained evidence without starting containers:

```sh
node spikes/cloudflare-pyodide/backend/verify-linux-control-reference.mjs
```

For reproduction, stage the corresponding `linux-control-{bootstrap,reference}.py`
in the supervisor's exclusive packet path and execute the corresponding
`ct128-linux-control-{bootstrap,reference}.py` inside the already authorized CT128.
The supervisors reject an occupied guest or dirty/wrong checkout and refuse to
reuse an existing run directory. This is a single correctness reference, not the
five-workload benchmark suite.

The next boundary remains a reviewed, versioned physical runtime handle preserving
the current Linux PID contract and canonical attempt/session fencing. Shared-file
ownership has not been handed over. Separately, the actual local workerd abort
counterexample remains material: noncooperative work emitted an effect after abort.
Neither this Linux success nor logical result rejection proves Pyodide physical
termination. Pyodide admission remains disabled pending isolation and deployed
acceptance; no cloud invocation is authorized by this fixture.
