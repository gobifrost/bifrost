# Request Budget Before Native Bookkeeping

The private adapter now uses the physical HTTP encoder's exact identity and byte
validation before creating an attempt, journal route, mux collection or native
Redis context. It checks the original context and a sizing-only copy reserving
the longest native UTC deadline string (32 characters). The real native pool
still supplies the actual cutoff; no placeholder is sent or persisted.

The existing companion request limit remains 262,144 bytes. This is the spike's
framing budget, not a general Cloudflare request limit. No separate smaller
parameter/context/manifest limit was introduced. Context and manifest must fit
together, including JSON escaping and envelope overhead. The physical constructor
continues to enforce the same shared encoder after native context preparation.
Changed duplicate dispatch still follows the original conflict/refusal path.

## Evidence

V1 fails before the change: oversized input invokes the Redis context-write
method before the physical constructor rejects it. The regression doubles Redis
and HTTP, so it proves the native write path was called, not an observed real
Redis write. The raw test exits 1 with `Awaited 1 times`.

V2 passes 54 targeted native tests (3.355 seconds), Ruff and Pyright with zero
diagnostics. Added cases cover:

- Oversized ASCII context: no native Redis write, route, collection or POST.
- Escaped Unicode: encoded bytes, not character count, determine admission.
- Manifest and context that fit individually but exceed the combined envelope.
- Positive request at the original budget: sizing copy reaches 262,144 bytes;
  actual native deadline formatting yields 262,137-262,144 bytes at the transport.
  The actual deadline differs from the sizing placeholder, normal reporting and
  retirement pass. This uses MockTransport, not actual maximum-size Pyodide input.

With the new shared encoder, actual CT128 engine/SDK retained-stream recovery
also passes using ordinary-sized requests: two genuine run identities, both SDK
effects independently read, original collector pre-ACK error retained, real EOF
evidence adopted, post-commit ACK loss/reconnect/stale-owner rejection, one launch
each, unchanged canonical state/log IDs, final credentials 401, accounting 2,2,1,0
and cleanup. No owned running containers remain; provider alive/no OOM before
explicit cleanup is separate from physical settlement proof.

## Reproduction and Boundaries

Existing CT128: 2 CPU/8 GiB. Native API source
`1d33150eb376b71f8a81b427920eda1b0447f829`, engine
`f49d4ba99cebd24aa497f1244833b940e640885f`; image
`sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b`.
Unit/quality containers: network none, 0.5 CPU/1 GiB/64 PIDs. Provider:
1 CPU/2 GiB/128 PIDs, cached cf CLI and unchanged pinned runtime. Stage the three
changed Python files over `deadline-recovery-v2` with the checked-in scripts in
`../results/request-preflight/`. For the actual valid-request control, copy that
packet to the guarded fresh `retained-http-v2` path and run:

```sh
timeout -k 5 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/retained-http-v2/ct128-canonical-failure.py --retained-http --revision 2
node spikes/cloudflare-pyodide/backend/verify-request-preflight.mjs
```

Raw failed/passing unit output, source hashes, scripts and actual receipt are
preserved. Real supervisor total 73.446 seconds includes setup, prewarm, waits and
cleanup; it is not startup/CPU time. No cloud invocation, deployment, new account
credentials/grants, packages, resource or runtime/security-setting changes.

This fixes request admission, not all payload/memory acceptance. Maximum-size
execution under Pyodide remains untested. Results have a separate stream-frame
bound; upstream result metadata may contain parameters/locals, so fitting input
does not guarantee fitting output. It does not classify arbitrary workflow code
or establish safe peak isolate memory, preclaim placement, deployed cancellation
or startup readiness. Public admission remains disabled. The updated acceptance
ledger separates those gates and normal completion from abnormal cancellation.
