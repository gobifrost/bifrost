# Cooperative cancellation qualification

This candidate is not wired into the engine or public executor admission.
Its receipt describes only the tracked asyncio task. It deliberately contains
no BiFrost terminal status, and reports execution_settled=false and
physical_stopped=false. Upstream engine CancelledError cleanup propagates the
exception; this helper must not fabricate an engine-produced result.

On 2026-11-03, CT128 (bifrost-pyodide-test-01) ran eight native unittest cases
using cached image sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b.
The final packet is /opt/bifrost-tests/pyodide-ddf316c1/inbox/cooperative-native-v2.
The container used network=none, 0.5 CPU, 256 MiB memory and swap ceiling,
64 PIDs, a read-only packet mount, and a 30-second external bound:

```sh
timeout 30 docker run --rm --name pyodide-ddf316c1-cooperative-native-v2 \
  --network none --cpus 0.5 --memory 256m --memory-swap 256m --pids-limit 64 \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -v /opt/bifrost-tests/pyodide-ddf316c1/inbox/cooperative-native-v2:/qualification:ro \
  -w /qualification bifrost-test-api-dev:bifrost-test-7ddfac36 \
  python -m unittest -v test_cooperative_execution
```

Exit 0: eight cases passed. The negative control actually permits a spawned task
to perform an effect after the main task was canceled. That evidence motivated
removing the initial candidate's synthetic Cancelled envelope. The v1 packet is
preserved; it passed its original eight cases but its envelope was too strong.
The v2 tests require the weaker receipt and absence of a terminal status.
Other cases preserve suppressed cancellation success, cleanup exceptions,
user-raised cancellation, wrong-run refusal, duplicate request behavior,
single-run admission and finally-block completion.

Local `node --test spikes/cloudflare-pyodide/backend/stage-cooperative-code.test.mjs`
passes: packaging preserves exact source and refuses overwrite. This is packaging
evidence only. No host Python or laptop Docker test was run.

## Actual local Pyodide RPC

The next experiment executed, using existing cf CLI dev, workerd 1.20261023.1,
Pyodide 314.0.6 / Python 3.14.2, ABI cpython-314-wasm32-emscripten. The pinned
runtime bundle and wrapper guards are enforced by unchanged ct128-sdk-probe.py.
Its historical --approved-https mode is only a reusable launcher here; the
observation's scope is raw cooperative RPC, with no HTTP egress or SDK claim.
The synthetic coroutine waits on an asyncio.Event, not a real vendor request.

First attempt v9 returned HTTP500: after concurrent RPC methods, reading the
child HTTP response body failed with "Cannot perform I/O on behalf of a different
request" (ReadableStreamSource). The exact entry is preserved in
cooperative-http-failed-entry.mjs; the guest retains the full failed observation.
cooperative-rpc-v9.json records source hashes, 10.381-second launcher lifecycle,
no OOM and stopped provider. This is a real transport-integration finding.

The bounded workaround v10 uses run_execution(payload) returning a JSON string
via RPC, avoiding the response stream. Three sequential fresh Dynamic Workers
pass normal completion, requested cancellation and suppressed cancellation.
Each finally block runs and tracked slots return to zero. Wrong-run and late
requests return false; suppressed cancellation stays Success. The cancel case
returns only the coroutine receipt, with physical_stopped/execution_settled false.
cooperative-rpc-v10.json preserves actual outcomes, ABI, source hashes, lifecycle
duration and provider cleanup. Both tests used network-disabled 1 CPU / 2 GiB /
128-PID containers. No runtime flags, security settings or bundle pins changed.

```sh
python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/cooperative-rpc-v9/ct128-sdk-probe.py --approved-https --revision 10
```

Packet preparation copies the existing approved-https-v3 project into a new
approved-https-v10 packet, overlays cooperative-probe-entry.mjs under the launcher's
approved-https-entry.mjs name and cooperative-probe.mjs under its probe name,
and stages cooperative-code.mjs with stage-cooperative-code.mjs. V9/v10 packets
and stopped providers remain preserved; do not overwrite/relaunch these names.

Next: qualify actual I/O ownership and engine cleanup using the RPC-string route
before considering integration. Event cancellation does not prove vendor I/O
settlement, spawned-task cessation, CPU stop or canonical terminal reporting.
No Cloudflare deployment/invocation was performed. Public admission remains off.
