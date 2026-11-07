# Signed Linux regression with the startup candidate

The actual signed control path passes one native Linux workflow and retirement
with the transactional-startup candidate mounted into the API. PostgreSQL and
Redis are real disposable services; no transport/session authority is mocked in
this increment. The shared source checkout is still unchanged.

Observed: one signed generation, one configure command, one workflow command,
one positive native PID ACK and one retained result event. The real upstream
engine/SDK reads configuration and integration data, imports a workspace helper,
writes a table document and returns Success. Independent SQL document readback
is 70. The fixture asserts result redaction, absence of the parent's signing
secret, and runtime credential rejection401 after terminal completion.
Log redaction is not independently remeasured by this non-benchmark fixture.

After terminal ACK, the fixture requests the existing signed physical drain and
waits for the launcher's retired event. Because that event precedes final pool
cleanup in the API worker's lifecycle, it separately observes the pool's Redis
registration disappear within a bounded 5-second settlement window. Native
templates stop, fixture worker data is removed, and every owned project container
is stopped. This is normal Linux lifecycle evidence, not Python CPU cessation.

## Exact source and isolation

Guest source base 1d33150eb376b71f8a81b427920eda1b0447f829 in the exclusive
detached worktree /opt/bifrost-tests/pyodide-ddf316c1/startup-linux-candidate-v1.
It copies only the already-qualified protocol/host source overlays and resource
bounded Compose from the existing test checkout; exact hashes are in the receipt.
The candidate is a read-only file mount, SHA256
90bf84fb5213856473d04a770bae6ac1d3dfd71eadd0a897b0ac291a6c3cddde.
An API-container readback confirms that file before the signed connection starts.
Neither the original guest checkout nor either writer's shared remote_pool source
was edited. The public profile guard remains part of the pinned host overlay.

The independent project is bifrost-test-9b326eab, with a fresh disposable database,
private Compose network, no published host ports and the cached dependency images.
Post-cleanup container image IDs, stopped state and resource limits are preserved
in ../results/startup-linux/container-readback.txt. The API image is33cd92be...;
Postgres, Redis, PgBouncer and SeaweedFS pins are in that readback.

CT128 remains2CPU/8GiB/no swap. The single launcher runner uses1CPU/2GiB/no
additional swap/128PIDs, the previously qualified Linux reference bound. Other
services retain the existing bounded Compose allocation. This is not a claim
that2GiB is the minimum. The supervisor enforces240seconds and8GiB free disk;
the actual whole-stack run took75.173seconds, including boot and cleanup. That
is not workflow latency or a cold-start benchmark. No volumes were deleted.

## Reproduction and acceptance limits

Stage ct128-linux-startup-candidate.py, linux-startup-candidate.py, task_cleanup.py,
pyright-guest-fixtures.json, the generated remote_pool.py.txt candidate and
spike/backend/authorized-local.py into the exclusive guest packet. Run:

```sh
timeout -k 10 290 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/startup-linux-candidate-v1/ct128-linux-startup-candidate.py
node spikes/cloudflare-pyodide/backend/verify-startup-linux.mjs
```

The existing script uses ./test.sh stack up, then stops only its native worker
and scheduler before the single signed fixture. The supervisor refuses an
existing source/run/project instead of resetting evidence. Reconcile the existing
completed run rather than invoking it again. Raw logs stay in the owned guest
run; only the fixture's secret-checked report and allowlisted readbacks are copied.

Ruff and Pyright pass before the actual run. Two initial static errors (the
fixture import path and a Redis awaitable union) were corrected before any stack
qualification started. No test retries, altered acceptance window, downloaded
package, laptop Docker, cloud invocation/deployment, new credential, grant, guest
resource expansion or runtime/security setting. Broader suites were not run.

Together with the12 source/mock and6 real-Redis cases, this supports review of
the **candidate** common startup repair. It remains unapplied to shared source
pending ownership coordination and normal review/CI. It does not negotiate
portable configure semantics, enable the Python runtime profile, or resolve
deployed CPU/resource enforcement and tenant isolation. Those remain required
before claiming a first-class untrusted Pyodide executor.
