# Mixed synchronous/asynchronous refresh

Two async SDK callers and one synchronous caller now converge on the same
replacement credential in a fresh local Python Worker, with exactly one actual
`POST /auth/refresh` request. A start gate releases all three callers using the
same observed access token. The workflow checks non-None/equal replacements
before its document write. Actual table readback is 70; canonical single-launch,
lost-ACK reconciliation, redaction, terminal access/refresh 401s and cleanup pass.

The changed adapter also passes a separate cold expired-token module challenge:
real API 401 → refresh 200 → same pinned module 200 → workflow/SDK effects.
These are distinct canonical fixture executions, not a replay of an uncertain
attempt. Both use genuine scoped run identity and the real isolated API.

## Failure retained, one bounded workaround

The prior sync-only bridge stalled in the mixed-call fixture. The runner later
exited with no usable provider result. `canonical-refresh-concurrent.json`
records that failure, while `concurrent-refresh-initial-adapter.txt`, driver,
observer and manifest snapshots preserve the exact candidate. No refresh request
was observed in the sampled API logs. Those samples do not prove zero effects
over the entire failure window. Runner termination did not prove guest termination;
the exact owned local supervisor was stopped separately, and port closure checked.
No unchanged retry or timeout increase was used.

The bounded fix replaces physical thread-based serialization with one
`asyncio.Lock` per connection in the fresh isolate. It calls upstream's existing
`_refresh_coordinator` and **unmodified `_refresh_locked`** core. Credential
generation/adoption, revoked-generation handling, storage, redaction and API
authorization stay upstream. The sync bridge uses native `run_sync` to await
that same cooperative path. No global asyncio/threading API is patched and no
Linux/SDK platform source is changed.

The adapter retains and shields an in-flight core task when its waiter is
cancelled, preserving the intent of upstream's independently completing refresh
thread. This task set is per-isolate transport state, not a durable queue or
execution authority. A component test confirms continued completion after waiter
cancellation. **Actual Worker/API interruption during refresh is still unqualified**;
no physical-termination claim follows from `shield` or the component test.

## Reproduction and evidence

Package the current companion into the existing local tools container:

```sh
python backend/pack-engine.py --current-runtime --manifest concurrent-runtime-manifest.json
node backend/canonical-refresh.mjs --run canonical-refresh-concurrent-cooperative concurrent concurrent-runtime-manifest.json
node backend/canonical-refresh.mjs --run canonical-refresh-challenge-cooperative challenge concurrent-runtime-manifest.json
node backend/verify-concurrent-refresh.mjs
python -m unittest discover -s backend -p 'test_*.py' -v
```

Use the healthy isolated test API and authorized-local Wrangler supervisor as in
SDK-REFRESH.md. Observers refuse existing receipts before invocation. Verify is
offline and checks source, package, one exchange, convergence, cold replay,
canonical result/reconciliation and revocation. Eight companion tests pass,
including lock serialization and shielding; focused Ruff passes. Broad API/client
suites and cloud invocation were not run for this companion-only increment.

Manifest: 479 modules, payload
`21b82a201061b86a025265b371b057a3516bb8f549c5a701634390f0a8ca9dc9`.
The runtime fingerprint and existing native-wheel constraints remain unchanged;
no dependency was compiled or retagged, and memory overhead remains unmeasured.
The failure concerns the old physical scheduling path, not missing wheel data.

## Remaining boundaries

This relies on private upstream refresh-core interfaces and a fresh single-loop
runtime. Source/ABI changes require requalification. Caller cancellation has a
component test, not actual provider/API cancellation proof. Whole-isolate/host
loss, arbitrary user code and CPU physical termination remain material gates.
No production registration, shared placement change, public cloud endpoint,
spending or credential grant occurred. Local supervisor/network/API state is
restored after qualification.
