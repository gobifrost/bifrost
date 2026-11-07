# Retain Uncertain Invocation Capacity Before ACK

A pre-ACK HTTP transport error now reaches the native pool as its existing
`ConnectionError` uncertainty contract. The original collection and delivery
receipts still raise the original `httpx.TransportError` subtype. The adapter
does not retry the POST, fabricate acceptance or a terminal result, or report
physical settlement. Native route ownership, Redis context and capacity remain
held until trusted reconciliation can prove otherwise.

Previously a `httpx.ReadError` escaped through the native pool's generic failure
branch. That branch removed the native handle and attempted Redis cleanup even
though an HTTP request might already have reached the provider. This fix lives
only in the companion HTTP adapter; Linux/upstream pool code is unchanged.
Protocol parse errors already use `ConnectionError`. Admission/input errors are
not included in the new `httpx.TransportError` mapping.

## Evidence

CT128, unchanged 2 CPU/8 GiB allocation and native checkout
`1d33150eb376b71f8a81b427920eda1b0447f829`. Pinned image
`sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b`.
Each unit/quality container uses network none, 0.5 CPU/1 GiB/64 PIDs, 40/50-second
wall timeout with five-second kill grace, read-only source mounts and no real
credentials. No laptop Docker, cloud calls, package or runtime-setting changes.

Raw outputs, exact guest scripts and before/after source hashes are in
`../results/preack-loss/`:

- V1: fail-first regression exits 1 with `0 != 1: uncertain started invocation
  lost native capacity`, using the previous published companion adapter source.
- V2: the same regression plus related cases pass: 40 tests, 2.326 seconds.
  Ruff and Pyright pass, zero diagnostics.
- The regression injects `httpx.ReadError` before an ACK using MockTransport,
  exercises the real native pool, and checks original error receipts, one POST
  across duplicate dispatch, native busy count one, no Redis cleanup, original
  logical deadline callback and refusal to retire an unacknowledged route.

Reproduce by staging the named packet over the previously pinned guest snapshot
and running the checked-in v2 script. Core container invocation:

```sh
python -m unittest -v test_private_http_pool test_attempt_delivery test_workflow_request_mux test_http_invocation test_workspace_prepare test_attempt_deadline
ruff check --no-cache private_http_pool.py test_private_http_pool.py
pyright --project pyright-guest-fixtures.json private_http_pool.py test_private_http_pool.py
```

This is native unit evidence, not real SDK/Worker execution during pre-ACK stream
loss. Prior real engine/SDK/deadline receipts remain historical and are not
substituted for that missing fault test. Lost collection is still uncertain:
logical timeout does not permit retirement or capacity release, and this adapter
does not yet expose authenticated retained-terminal adoption after that loss.
No fake ACK or rerun is introduced to make cleanup pass. Public admission remains
disabled; deployed abnormal CPU enforcement remains unproven.

Next: bounded real local HTTP/engine fault injection before ACK, then authenticated
retained-result adoption and explicit unsettled cleanup obligations. Also inspect
oversized context preflight: rejection should occur before native bookkeeping,
rather than only when constructing the physical HTTP envelope. Supported
startup/registration remains a later contract boundary.
