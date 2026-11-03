# Isolated transactional-startup repair candidate

This patch is tested as a read-only guest file overlay, **not applied** to either
writer's shared remote_pool.py. It is not an accepted upstream fix or Pyodide
startup/admission implementation. The signed/public profile guard stays intact.

The candidate serializes start/stop through an owned lifecycle lock. Startup
failure or cancellation awaits normal upstream stop compensation. If compensation
also fails, both exceptions are retained in a BaseExceptionGroup and the pool
refuses another start until explicit stop has settled cleanup. Normal healthy
start, repeated start and stop remain covered.

An additional negative test exposed that upstream _unregister_worker swallows
Redis delete errors. The first candidate could consequently report a clean
rollback with registration still present. The revision requires absence readback
during startup rollback and subsequent quarantined stop, while the session is
still owned. An unconfirmed registry state raises and retains the pool's resource
obligations. Normal non-quarantined stop keeps its existing behavior.

This retains upstream's existing owner check; it does not create an atomic
Redis/PostgreSQL ownership transaction or claim the underlying session race is
solved. Superseded-owner behavior is tested with a mocked authority response only.

## Evidence and boundaries

| Experiment | Actual outcome |
| --- | --- |
| Nine regressions against unchanged upstream | 6 failures, 1 error, exit1 |
| First candidate overlay | 9 passed |
| Add concurrent stop and second cancellation | 11 passed |
| New swallowed-unregister regression against first candidate | 1 error, exit1; original failure escaped without cleanup quarantine |
| Revised candidate | 12 passed, exit0 |
| Revised candidate and fixture Ruff/Pyright | Passed |

The tests use the actual upstream pool with mocked transport, registration/cache,
session authority and idle lifecycle loop bodies. They prove source control flow,
not live Redis cleanup, signed worker startup, cross-process ownership, platform
compatibility or physical termination. No DB/Redis stack or cloud call was made.
The baseline diagnostic remains unchanged and still documents the upstream defect.

All runs took place in existing CT128 (2 CPU/8 GiB/no swap) with the cached image
sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b,
network none, 0.5 CPU/1 GiB/no additional swap/64 PIDs, 40-second outer bounds.
The image imports API source from the qualified 1d33150 checkout; only the candidate
module is overlaid read-only in the test container. --rm removes the owned test
containers; no existing volumes, locks, source checkout or dev stack were changed.

Exact source hashes, command output (including original PowerShell stderr wrappers),
earlier preparer/test snapshots and the patch are in `../results/startup-candidate/`.
The receipt is an author artifact binding observed command output, not a signed
provider or independent service receipt. The revised source SHA256 is
90bf84fb5213856473d04a770bae6ac1d3dfd71eadd0a897b0ac291a6c3cddde.

## Reproduction and next gate

From the repository root:

```sh
node spikes/cloudflare-pyodide/backend/prepare-startup-candidate.mjs /task-owned/remote_pool.py
git apply --check spikes/cloudflare-pyodide/results/startup-candidate/remote-startup.patch
node spikes/cloudflare-pyodide/backend/verify-startup-candidate.mjs
```

The preparer refuses a different source-base hash. In the bounded guest image,
mount the existing API at /app read-only, test packet at /qualification read-only,
and generated candidate at /app/src/worker/remote/remote_pool.py read-only. Set
PYTHONPATH=/app:/qualification and the testing/synthetic fixture environment,
then run `python -m unittest -v test_startup_candidate` from /qualification.
Packets startup-candidate-v1/v2/v3/v4 remain in the exclusive owned guest inbox.

Next qualify the same candidate against real disposable Redis: registration
write followed by failure, deletion/readback failure, interrupted compensation,
successful retry and superseded-owner retention. Then coordinate the shared-file
repair with the Containers owner and ordinary upstream review. Neither step is
replaced by these 12 mocks. No new cloud window, deployment, credentials, security
settings, spending, resource expansion or untrusted admission is authorized here.
