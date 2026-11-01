# Real upstream SDK under guest Pyodide

Outcome: the unchanged upstream execution path and SDK performed authenticated API effects under official Pyodide 314.0.6 / CPython 3.14.2 in Node on CT128. This is not a deployed Cloudflare acceptance result. Execution admission remains disabled pending isolation acceptance.

API checkout: `f417d03f5c8d5e3a182e68f69e2bf601a3b60125`. Prepared runtime closure: `785b560b9973020e1c13401383e1a73a87a54940`. Exact additional fixture bytes are pinned in `results/node-sdk-resume/success.json`. Native package ABI/provenance is recorded in `NATIVE-PACKAGE-QUALIFICATION.md`; no replacement SDK or native-package emulation was used.

The actual canonical consumer claimed the execution and issued run credentials. The actual runtime runner loaded hash-pinned workflow and helper workspace sources through the API, read config through the SDK, and upserted a table document. Independent API readback returned **70**. The upstream table SDK first received 404, created its missing table (201), and successfully upserted once (200); these are preserved API observations, not a hidden retry added by this harness.

Canonical terminal consumption succeeded. Injected lost acknowledgement followed by owner reconnect/reconciliation retained one provider launch and one persisted log. Cross-tenant access returned 403; the run token returned 401 after terminal consumption. The result credential probe was redacted and the parent-secret probe was false. These probes do not establish general sandbox isolation or comprehensive secret redaction.

The one-use Node provider forwards real requests only to the isolated test API. It uses actual engine/SDK code and is a qualification harness, not a production executor. Signed worker-control authentication was tested separately; this run does not combine full WebSocket dispatch with a deployed provider.

## Reproduce and inspect

Offline evidence check, without services or credentials:

```sh
node spikes/cloudflare-pyodide/backend/verify-node-sdk.mjs
```

Guest supervisor: `backend/ct128-node-sdk-qualification.py`. Its exact Docker/Compose commands and exit codes are in the success receipt. It requires the existing prepared closure, pinned official runtime, owned checkout and staged packet at its explicit paths. It deliberately refuses an existing run directory or active containers; do not overwrite the preserved run to repeat it. Use a separately reviewed exclusive packet/run name for a future execution.

Guest: `pve-t340` CT128, 2 CPU / 4 GiB. Provider: 1 CPU / 1 GiB, 128 PIDs; total wall guard 900 seconds and disk-used guard 24 GiB. Observed supervisor elapsed 62.901 seconds, provider request wall time 244.994 ms, upstream engine duration 134 ms. These are single observations, not cold/warm benchmarks or Cloudflare CPU/cost measurements. Peak WASM memory was not measured. All owned containers stopped; volumes and source retained.

## Experiment log

| Hypothesis/change | Command | Observation | Next step |
|---|---|---|---|
| Existing prepared runtime imports directly | v1 supervisor, preserved startup receipt | Provider failed before credential admission | Capture bounded bootstrap diagnostic |
| Bounded startup diagnostic exposes smallest blocker | network-disabled v2 bootstrap | `ModuleNotFoundError: runtime_runner` | Match existing bundle module-name mapping |
| Import unchanged hyphenated runner using explicit module spec | v3 supervisor, canonical fixture `--node-pyodide` | Real SDK effects, canonical reconciliation and cleanup passed | Preserve evidence; qualify actual workerd runtime when its pinned bundle is legitimately available |

The workerd-specific integrity-pinned bundle remains unavailable following its recorded HTTP 403. The public official Pyodide core used here does not replace that bundle. Cloudflare deployment/SDK reachability, fresh per-run isolate guarantees and independently observed physical termination of noncooperative CPU work remain material acceptance blockers. Killing this Linux harness, logical cancellation, token revocation or refusing late results would not close them.
