# Actual Runtime Inventory and Registration Projection

The credential-free local Worker probe now reports its actual ABI, distribution
metadata and loaded versions of the three core engine/SDK dependencies. The
parent validates the existing runtime pin, bounded unique package identities and
observed versions, then snapshots the report. The private transport supports
only read-only `package_tool` inventory from that bound snapshot. Installation,
uninstallation and requirements mutations remain unsupported before effects.

This is one startup component, not full startup. Binding inventory does not make
`native.start()` succeed: `configure` remains refused before `_started` changes.
No pressure/CPU/cancellation capability is fabricated to satisfy that call.
The package report is diagnostic data, not authorization, signed provider
identity, workload compatibility or scheduler eligibility. Copied BiFrost source
is separately pinned; package presence (including Redis) does not prove its OS or
network contracts work in Pyodide.

## Actual CT128 Result

The real probe observed Python 3.14.2, Pyodide 314.0.6, SOABI
`cpython-314-wasm32-emscripten`, platform `emscripten-5.0.3-wasm32`, and 11 packages:

| Package | Observed Version |
|---|---|
| annotated-types | 0.8.0 |
| httpx | 0.28.1 |
| idna | 3.20 |
| pydantic | 2.14.2 |
| pydantic-core | 2.50.2 |
| python-dotenv | 1.2.4 |
| pyyaml | 6.0.3 |
| redis | 7.4.0 |
| typing-extensions | 4.16.0 |
| typing-inspection | 0.4.4 |
| workers-runtime-sdk | 1.9.5 |

The fixture calls the unchanged upstream `ThinRemoteProcessPool._register_worker`
to exercise its Redis projection and independently reads back the exact package
list. This is the volatile registration cache, not canonical scheduling authority
or a fully started/signed remote worker. `native._started` remains false.

Both actual engine/SDK workflows then pass their canonical result, lost-ACK,
reconnect, credential and accounting checks. The registration cache is explicitly
removed and independently confirmed absent. Private launcher retirement now also
closes its owned native Redis client, matching the upstream client's ownership;
the fixture checks that reference is cleared. It does not stop unacknowledged
work to force cleanup. Owned test containers after cleanup are empty.

## Dependency and Contract Boundary

| Upstream Facility | Classification / Evidence |
|---|---|
| `_get_installed_packages` invokes `pip list` through `subprocess.run` | Pip is Python code; this helper's process-spawning contract is the unsupported OS facility. A PyEmscripten build cannot provide that facility, so no compilation attempt was made for this operation. |
| Read package metadata/loaded module versions | Small read-only adapter using actual standard-library metadata and loaded pinned modules; measured in real local Python Worker. No package manager was recreated. |
| Redis `_register_worker` projection | Unchanged upstream code works in the Linux parent with actual validated Worker data; real readback and cleanup pass. |
| Linux pressure/dangerous-memory `configure` | Unimplemented for this target. Parent cgroup data is not isolate usage; unknown metrics remain null. |
| Package mutation | Explicitly refused. New immutable dependency artifacts would require normal build/provenance/qualification, not live pip in an isolate. |
| Signed startup, refresh/control listeners, cancellation and shutdown | Not qualified by a package-cache write. Public host still rejects experimental profile; Linux remains default. |

There is no unsupported new native extension to compile in this increment.
Existing core extensions are loaded in the real probe and actual SDK execution;
their build/ABI evidence remains in the prior native/runtime qualification.

## Commands and Evidence

Existing CT128: 2 CPU/8 GiB. Native API
`1d33150eb376b71f8a81b427920eda1b0447f829`, engine
`f49d4ba99cebd24aa497f1244833b940e640885f`; dependency image
`sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b`.
Unit/quality containers: network none, 0.5 CPU/1 GiB/64 PIDs. Local provider:
cached cf CLI, 1 CPU/2 GiB/128 PIDs, unchanged runtime bundle/limits/settings.

V1 passes 61 unit cases (3.446 seconds), but Pyright rejects the fixture Redis
stub's sync/async return union. No live run was started with that candidate.
V2 uses the upstream-style awaitable cast for the known async decode-response
client. Ruff/Pyright and Node syntax pass. Unit source hashes are unchanged and
the 61-case result is preserved rather than rerun until green. Then:

```sh
timeout -k 5 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/startup-inventory-v2/ct128-canonical-failure.py --startup-inventory --revision 2
node spikes/cloudflare-pyodide/backend/verify-startup-inventory.mjs
```

Scripts, failed/passing quality, source hashes and raw actual receipt are in
`../results/startup-inventory/`. Actual supervisor 73.270 seconds includes setup,
probe/prewarm, SDK waits and cleanup; it is not startup or CPU latency. No cloud
invocation, deployment, new account credentials/grants, spending, resources or
runtime/security settings changed. No shared platform code was edited.

## Shared Interface Handoff

For Eddie/normal upstream review, remaining source interfaces are
`remote_pool.py:start` (`configure`, memory read and lifecycle listeners),
`_remote_packages` (now characterized read-only), `stop`/`_request_terminate`,
and `host.py`'s profile admission guard. A portable startup contract must describe
actual fixed limits and supported control operations rather than ACK Linux
process settings. Registration-cache truth alone must not advertise a provider
as ready. Coordinate shared placement/capability/recovery edits with the Containers
owner; this increment changes none of those files or resources.

Next: characterize the remaining native startup/control operations and qualify
their actual needs. Output/memory and deployed retention/abnormal cancellation
gates remain open. No undocumented terminate API is demanded for normal success,
and no package probe is substituted for those acceptance requirements.
