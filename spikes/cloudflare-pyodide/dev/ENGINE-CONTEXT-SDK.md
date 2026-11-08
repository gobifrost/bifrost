# Refreshed engine/worker/context: real local SDK acceptance

The full canonical two-tenant fixture passed in105.351s under local Python
Workers/Pyodide on CT128. It executed the actual engine, worker.run_execution
and SDK context sources pinned to1796c6fcf68964aa2ff36421ccc7be697d6322f7,
not merely the earlier direct-engine metadata probe. SDK client remains139596497;
the native API remains1d33150. This is a deliberate three-file source overlay,
not whole-current-main compatibility or a deployed refresh.

Observed: real authorized claims and run credentials, parameters, workspace
module hashes/imports, config reads and table writes for both tenants, peer403,
parent-secret absence, fresh-state checks, log/error/result redaction,
persisted logs10/1, expected Failed/Success outcomes, awaited SDK close and ended
token401. Native duplicate dispatch, stale-owner result rejection, lost terminal
ACK/reconnect reconciliation and accounting all passed without relaunch. Launch
counts1/1, busy counts `[2,2,1,0]`; terminal obligations, mux and fixture worker
records cleaned up. Raw assertions and sanitized summary are engine-context-sdk-v1.json.

Both runtime result envelopes contain the newly serialized identity fields with
their correct non-agent defaults. The earlier async direct-engine probe separately
tested nondefault agent/original-user values. No agent identity or authorization
was forged to obtain SDK access in this run.

## Explicit experimental profile

The accepted61d818 bundle and its provenance validation remain unchanged.
`--engine-context` selects a separate packet prefix and validates three exact
upstream module digests before starting the runtime. The local wrapper overlays
only those Python module bodies on every fresh LOADER.load. No engine or SDK
business logic was reimplemented. Default staging/admission and deployed pins
are not promoted by this result. Five preflight tests exercise the actual fixed
overlay plus altered source, changed bytes, extra module and wrong-entrypoint
refusals before runtime creation.

The local callback adapter also needed the local API transport in its exported
WorkerEntrypoint, rather than inheriting the deployed binding environment. It now
uses the same stateless canonicalApiFetch helper with a loopback-only transport;
both callback and normal fetch enforce ISOLATED_TEST_STACK. The full fixture
exercises this repair. The deployed callback source is unchanged.

## Exact run and bounds

```sh
python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/engine-context-sdk-source-v1/spikes/cloudflare-pyodide/dev/prepare-engine-context-sdk.py
python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/engine-context-sdk-source-v1/spikes/cloudflare-pyodide/backend/ct128-canonical-failure.py --engine-context --revision 1
```

Packet: inbox/engine-context-sdk-v1. Run: runs/engine-context-sdk-v1. The existing
pinned checkout's test.sh owns the disposable stack startup and migrations;
normal worker/scheduler stop before the fixture. The cf dev provider uses cached
cf1.0.0-beta.26/Wrangler4.156.0, exact runtime bundle/cache-wrapper hashes,
1CPU/2GiB/128PIDs/no additional swap, existing isolated test network and a360s
outer lifecycle bound. The native runner uses its bounded768MiB Compose service.
CT128 remains2CPU/8GiB; disk preflight requires8GiB free. Heavy laptop Docker was
not used. Existing dev containers, volumes and uncertain locks were preserved.

Runtime inventory admission checks the existing Pyodide314.0.6/Python3.14.2 ABI.
Real package requirements matched httpx0.28.1, pydantic2.14.2 and
pydantic-core2.50.2. The deltas are pure Python; no package install, wheel build or
native dependency replacement. Provider container OOM=false is not an isolate
peak-memory measurement. Elapsed lifecycle time includes stack boot and tests,
not pure startup or CPU. Cost was not measured; cloud invocations were zero.

All four phase exit codes are0. Owned test/provider processes stopped and fixture
cleanup passed. Both Cloudflare endpoints and retired secrets remain untouched.
Raw receipt SHA256307393ce13de65ef2a47f183dcfc29dee2820e6b493470e774f1ba63760b7f31
binds retained private evidence; the exporter emits no raw logs/tokens/result
bodies. An initial exporter used the wrong context key; it was corrected to the
upstream execution_context field without rerunning or altering execution evidence.

Ruff passes changed Python fixtures; JS syntax and receipt verifiers pass.
Full native suites were not rerun. Remaining: normal CI/review, deployed acceptance
of this refreshed candidate, and the separate abnormal cancellation/resource
isolation gate. Physical termination and arbitrary-Python admission remain unqualified.
