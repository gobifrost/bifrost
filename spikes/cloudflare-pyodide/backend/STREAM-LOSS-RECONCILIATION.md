# Accepted stream loss and retained terminal reconciliation

The real upstream opaque pool retains its invocation obligation when an accepted
HTTP stream loses its terminal result. An actual TCP proxy forwards the validated
accepted frame, collects the real local Pyodide result, retains it using the
upstream Launcher, then closes the caller connection without the result frame
or chunked EOF. The caller observes RemoteProtocolError, not a fabricated result.

This qualifies a specific local failure boundary. The control link/reconnect is
simulated. It does not establish deployed recovery, persistence across launcher
death, recovery of a result never received by the launcher, or physical stop.
No new provider result queue, database schema or authoritative scheduler exists.

## Observed contract

- Native routing returns after the opaque ACK, before result collection.
- Collection fails; the journal retains its failed, unacknowledged receipt.
  The real pool retains the matching non-PID invocation and busy count 1.
- The canonical execution stays Running without a result or error. A real SDK
  table write already committed; an independent API read returns 70. Failure of
  the result stream neither rolls back that write nor licenses a retry.
- Owner rotation preserves the receipt. Duplicate dispatch does not launch code
  again. The original native start/timeout/deadline remain unchanged.
- Stale-owner retained-result submission is rejected. The current owner delivers
  the retained event through the real pool's handle_event/on_result and canonical
  WorkflowExecutionConsumer. Success/70 persists once; the opaque route clears.
- Loss of the first terminal ACK preserves the Launcher event. Replay creates no
  duplicate log row; the upstream ACK receiver drains the event. Access and
  refresh credentials return 401 after canonical completion.
- Fixture execution/worker cleanup passes. Owned test containers stop, volumes
  and the separate dev stack remain. No cloud invocation or physical-stop claim.

This does not close an arbitrary unknown outcome as Success. If no authenticated
retained terminal event exists, normal upstream lost-worker/deadline recovery is
still needed. Neither guessed results nor silent workflow redispatch is added.

## Reproduction and evidence

Assigned CT128, 2 CPU/8 GiB/no swap, same isolated native test project and pinned
runtime as [streamed invocation](STREAMED-INVOCATION.md). The provider is capped
at 1 CPU/2 GiB/128 PIDs; the run has a 430-second supervisor bound. Public
admission stays disabled. Native protocol/pool overlay hashes were read back as
91db4293...b3956d5 and abb9a48e...ba429a, matching the prior 67-case qualification.

```sh
timeout 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/stream-loss-v2/ct128-canonical-failure.py --stream-loss --revision 2
node spikes/cloudflare-pyodide/backend/verify-stream-loss.mjs
node spikes/cloudflare-pyodide/backend/verify-stream-pool.mjs
node spikes/cloudflare-pyodide/backend/verify-deadline-retained.mjs
```

V1 runtime qualification passed with all four phases exit 0; supervisor 66.429 s.
Its [receipt](../results/stream-loss/v1.json) and exact
[original probe](../results/stream-loss/v1-provider.txt) remain intact.

The first standalone Pyright invocation used no upstream source/config and
reported 43 errors, including missing imports and conditional bindings. Checking
with the upstream API config resolved the native import context but still lacked
the companion import path (one missing import). The task-local
pyright-guest-fixtures.json now extends that unchanged config and adds only
/app and /qualification import paths. Conditional deadline bindings were made
explicit; no diagnostic suppression was added. V2 is a separate runtime check
of this corrected exact source, not a retry of the old artifact until green.
The [V2 receipt](../results/stream-loss/v2.json) passes all four phases; supervisor
101.290 s includes stack/setup/cleanup, not pure execution or startup latency.

The type/lint containers use network none, 0.5 CPU/512 MiB/64 PIDs and the cached
native image. Commands are `pyright --project
/qualification/pyright-guest-fixtures.json /qualification/provider-response-loss.py
/qualification/ct128-canonical-failure.py` with the native API mounted read-only
at /app, and `ruff check --no-cache provider-response-loss.py
ct128-canonical-failure.py`. Both pass on V2 source.
The initial failed checks, their disposition, passing commands and native overlay
readback are preserved in [quality-checks.json](../results/stream-loss/quality-checks.json).

The [provider-interruption follow-on](STREAM-PROVIDER-INTERRUPTION.md) now
qualifies explicit canonical failure and retained resource obligations when no
terminal event reaches the launcher locally. Automatic provider-loss observation,
deployed lifecycle/enforcement/accounting and untrusted isolation
remain separate gates; these local tests authorize no cloud reopening or rollout.
