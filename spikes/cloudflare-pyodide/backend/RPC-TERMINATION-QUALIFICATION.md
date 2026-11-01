# Python RPC cancellation counterexample

Hypothesis: cancellation of a Python RPC execution context might stop CPU work
where forwarding a fetch abort signal did not. The bounded guest experiment
falsified this workaround for the tested local runtime.

`guest-rpc-termination-entry.mjs` creates a fresh Python Dynamic Worker and calls
its fixed `run` method through actual RPC. The child reports `before`, computes a
finite noncooperative loop and reports `after`. The independent Node HTTP observer
aborts the parent request ten milliseconds after receiving `before`. It retains
cross-case events and records its own monotonic timestamps. No credentials,
arbitrary workflow code or platform execution is supplied.

Observed actual local workerd result:

| Case | Before (ms) | Abort (ms) | After (ms) | Meaning |
| --- | ---: | ---: | ---: | --- |
| Completion control | 3691.396 | — | 5398.922 | Callback route and finite work function |
| Catch-capable finite loop | 2209.678 | — | 19116.693 | No local quota exception observed |
| External request abort | 2152.830 | 2163.138 | 3829.473 | Effect 1666.335 ms after abort |

The last result is a physical-stop counterexample, not merely an absent response.
The RPC response serialized the Python mapping as `{}` in this synthetic fixture;
it is not workflow-result or Python-to-JavaScript mapping compatibility evidence.
The termination result uses independently timestamped callbacks, not that body.
Both normal calls returned HTTP 200, and the observer exited zero because recording
negative evidence succeeded. No canonical late-result acceptance was exercised.

The existing CT128 allocation remained 2 CPU / 4 GiB, with 19 GiB free at preflight.
One container used one CPU, 1 GiB, 256 PIDs and `--network=none`. No OOM was recorded.
The existing supervisor bounds readiness to 45 seconds and the observer to 120
seconds, and rejects used disk above 24 GiB. It finished in 44.105 seconds, stopped
only its owned container and deleted no volumes. Cleanup of the harness does not
prove cancellation of the child. Wrangler's attempted `Request.cf` metadata lookup
failed under the disabled network; this did not prevent local execution.

Runtime provenance is the already verified workerd `1.20261023.1`, Python/Pyodide
`314.0.6`, exact source-built bundle SHA-256
`3c3fd5a4179230e21e018e28b0e735e7c4abd8fe8e4ff39d2fd6bb2ea3a2e260`.
No vendor-denied download was retried. Container command, packet hashes, observer
events and cleanup are retained in `../results/rpc-termination/receipt.json`;
host and local receipt hashes matched. The supervisor is
`ct128-rpc-termination.py`, executed with Python inside CT128, not on the laptop.
Its packet is the existing guest termination packet with this RPC entrypoint and
the config's `main` changed to `guest-rpc-termination-entry.mjs`. Paths and exact
invocation are in the receipt; existing run directories must not be reused.

```sh
node spikes/cloudflare-pyodide/backend/verify-rpc-termination.mjs
node --test spikes/cloudflare-pyodide/backend/termination-observer.test.mjs spikes/cloudflare-pyodide/backend/timed-termination.test.mjs spikes/cloudflare-pyodide/backend/termination-evidence.test.mjs
```

Current official [loader API](https://developers.cloudflare.com/dynamic-workers/api-reference/)
and pinned [WorkerStub source](https://github.com/cloudflare/workerd/blob/7caf6dee5bfe9af1b7710ddf66222900007af080/src/workerd/api/worker-loader.h)
expose entrypoint access but no explicit terminate method. The documented
[RPC disposal lifecycle](https://developers.cloudflare.com/workers/runtime-apis/rpc/lifecycle/)
describes remote-object resource management, not a physical-stop receipt. This
experiment tests execution-context cancellation, not an undocumented disposer.
Documentation/source inspection cannot establish deployed behavior.

The proposed executor profile still needs an independently verifiable stop/deadline
mechanism preserving upstream guarantees. Fetch abort and RPC context cancellation
have both failed locally. Deployed Cloudflare physical-stop and late-result fencing
proof remain required; cloud tests are held. Untrusted admission stays disabled.
