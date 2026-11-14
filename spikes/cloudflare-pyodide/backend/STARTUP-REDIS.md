# Real Redis qualification of the isolated startup candidate

Six cases pass with actual upstream registration/unregistration, a real Redis
7.4.12 server, and independent readback through a second client. The candidate
remains a read-only file overlay; neither writer's shared source was changed.

| Case | Independent Redis result |
| --- | --- |
| Healthy start/repeated start/stop | Online row and packages/runtime tags observed; stop removes row and closes owned client |
| Final-tagging failure | Real partial online registration observed before failure; compensation removes it |
| Redis deletion failure | Online row remains, cleanup raises as a grouped failure, another start is refused; later stop deletes it |
| Readback failure | Deletion succeeds but confirmation raises; pool still refuses another start until later stop confirms absence |
| Cancellation during final tagging | Partial row observed; cancellation compensation removes it |
| Superseded owner predicate | Replacement marker remains untouched by old-owner compensation; explicit fixture cleanup subsequently removes its test key |

All six synthetic keys are independently confirmed absent at fixture completion.
The superseded-owner predicate, transport configure/package inventory and idle
lifecycle loop bodies are simulated. PostgreSQL ownership, signed control startup,
real listener subscriptions and Pyodide public admission are **not** proved. The
pubsub event publisher is mocked to avoid touching any global Redis connection.
Redis HSET/EXPIRE/DELETE/EXISTS/HGETALL operations themselves are real. The delete
and readback failure injections wrap only the selected owned client's methods;
the independent observer remains able to inspect the actual server state.

## Experiment lineage

v1 exits before qualification because an immediate ping races Redis startup.
Its server log shows ready immediately afterward; the owned container is stopped.
v2 adds a bounded Docker health gate before testing. Six runtime cases pass, but
Pyright reports two Redis sync/awaitable typing unions. v3 uses explicit casts
for the known async calls, passes Ruff/Pyright, then passes all six cases with that
exact source. Earlier supervisor/probe snapshots, failed readiness receipt,
typing diagnostic summary and all runtime outputs are retained. No failed test
was retried unchanged or hidden behind a larger execution timeout.

Actual source base readback: 1d33150eb376b71f8a81b427920eda1b0447f829.
Base remote_pool SHA256 dd14eb91761041a0f6ea6ea974295c2c610ad43c9e50e46dbb14028ef9f81789;
process_pool SHA256 598593cb02afd9efab6c9c6db6935a6bca4e799d47792e63b9f139b5c7b92417.
Loaded candidate SHA256 90bf84fb5213856473d04a770bae6ac1d3dfd71eadd0a897b0ac291a6c3cddde,
unchanged from the 12-case source/mock candidate. Packet hashes and exact runner
command are in `../results/startup-redis/v3-receipt.json`.

## Bounds and reproduction

Existing CT128 remains 2 CPU/8 GiB/no swap, with more than 10 GiB free at preflight.
Cached Redis image98c6ca8f... runs with network none, no host ports, 0.25 CPU,
128 MiB/no additional swap/64 PIDs and a task-owned 16 MiB tmpfs at /data.
Persistence is disabled; no persistent volume is created. API image33cd92be...
runs with 0.5 CPU/1 GiB/no additional swap/64 PIDs, joining only the Redis
container's otherwise network-disabled loopback namespace. Source/candidate/test
mounts are read-only; only the exclusive evidence directory is writable.

No host sysctl was changed in response to Redis's overcommit warning. No package
was downloaded, no cloud/API account was accessed, no production secret was used.
Both test and Redis containers are absent/stopped in the cleanup receipts. Existing
volumes, locks, dev stack and shared source are preserved.

Stage startup-redis-probe.py, ct128-startup-redis.py and the generated candidate
remote_pool.py.txt into the exclusive guest inbox startup-redis-v3. Then:

```sh
timeout -k 5 90 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/startup-redis-v3/ct128-startup-redis.py
node spikes/cloudflare-pyodide/backend/verify-startup-redis.mjs
```

The supervisor refuses existing run directories/containers and verifies cached
image IDs, candidate hash, guest identity and free disk. It bounds readiness and
the actual qualification separately; cleanup verifies exact owner labels before
stopping only its containers. Reconcile an existing run instead of deleting it to
repeat the command. Broader platform suites were not run for these fixture files.

Next: coordinated application of the shared lifecycle patch and normal upstream
review/CI, with actual signed Linux startup/stop regression coverage before adoption.
This Redis result advances the candidate; it does not enable the Python profile,
resolve deployed CPU/isolation enforcement, or renew cloud invocation authority.
