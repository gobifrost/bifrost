# Partial startup can leave the upstream pool marked started

The actual checked-out ThinRemoteProcessPool.start sets _started=True after
configure but before awaiting registration and the final Redis runtime tags.
With a failure in either awaited operation, a second start returns immediately,
although heartbeat, lease-refresh, cancellation and command tasks are all absent.
This is a common startup path, not a Pyodide-only configure failure.

The executable `startup-failure-probe.py` runs the real upstream class while
mocking transport, registration, memory sampling, Redis and the four loop bodies.
It never registers a live worker, starts a workflow, reaches PostgreSQL or makes
a cloud call. Its four observed cases are preserved in
`../results/startup-failure-probe/v1.json` (exact stdout transcribed from the guest
command, not an independently persisted service receipt):

| Injected condition | First / second start | _started | Lifecycle tasks |
| --- | --- | --- | --- |
| configure fails | raises / raises, two configure calls | false | 0 |
| registration fails | raises / returns, one configure call | true | 0 |
| runtime tagging fails | raises / returns, one configure call | true | 0 |
| healthy | returns / returns, one configure call | true | 4 |

Source SHA256 dd14eb91761041a0f6ea6ea974295c2c610ad43c9e50e46dbb14028ef9f81789
matches the actual loaded guest module and this contribution's remote_pool.py.
Guest probe SHA256 81414c82b34f2a173a7d8d20708112cf4a234eb3eea321d07f492982783e057e
matches the checked-in fixture. Run `node backend/verify-startup-failure.mjs` to
check source and retained observations without repeating the guest experiment.
The qualified guest source checkout remains 1d33150eb376b71f8a81b427920eda1b0447f829.
These are source/mock observations, not successful signed/public startup evidence.

## Ownership and smallest corrective contribution

The shared remote_pool/host lifecycle remains coordinated with the separate
Containers writer. Read-only inspection found those two paths clean in that
checkout, but clean status does not transfer ownership. No shared file was edited,
no profile guard was removed, and configure was not falsely acknowledged.

The coordinated fix needs transactional startup semantics: failure or cancellation
must not advertise readiness; partial registration/cache/client ownership must be
cleaned up under the current session fence; created listeners must be settled; the
original failure must remain observable. Simply moving _started to the last line
is insufficient because stop currently returns immediately when _started is false,
which can strand partial registration/client resources. Calling stop blindly can
also fail and obscure the original startup error or affect replacement ownership.

Required regressions for that change: configure refusal, inventory/registration
failure before and after cache write, final-tagging failure, cancellation while
starting, cleanup failure, superseded ownership, healthy Linux start/stop and repeat
start. Use disposable Redis readback for partial-state cleanup; this mocked probe
does not establish those guarantees. Keep Pyodide admission disabled independently.

## Actual bounded guest invocation

Exclusive packet: /opt/bifrost-tests/pyodide-ddf316c1/inbox/startup-failure-probe-v1.
`timeout -k 5 40 docker run --rm --network none --cpus=0.5 --memory=1g
--memory-swap=1g --pids-limit=64`, pinned image
bifrost-test-api-dev:bifrost-test-7ddfac36, source /app and fixture /qualification
read-only, PYTHONPATH=/app:/qualification, testing environment and synthetic fixture
secret, `python startup-failure-probe.py`. Exit0, four explicit observations checked.
The healthy mock listeners are canceled and awaited by the diagnostic finally;
this is fixture cleanup, not production compensation. No persistent volumes created.
Ruff and Pyright on that packet both pass in bounded network-disabled guest runs.
No heavy laptop Docker, cloud invocation, new package, credentials, resource or
runtime/security settings. The overall execution backend remains incomplete.
