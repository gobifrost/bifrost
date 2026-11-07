# Explicit staging of the locally qualified SDK artifact

Follow-up: [actual staged runtime engine/SDK execution](../backend/STAGED-RUNTIME-EXECUTION.md)
now passes separately on local cf CLI/Pyodide. The assembly receipt below retains
python_executed=false; the new execution receipt supplies the stronger local proof.

The current SDK artifact can now be assembled into a fresh runtime directory
without rebuilding, rewriting or selecting a different Python closure. The new
explicit command accepts only SHA256
61d818aef7f66a129951c7eb0cc4dc638277da35e635e13965b90f8a23575731.
Historical stage_runtime.mjs keeps its original base pin for reproducing old
cloud receipts; this is not an automatic fallback or a relaxed artifact check.

The accepted artifact contains 466 packaged modules and 15,287,043 serialized bytes:
engine f49d4ba99cebd24aa497f1244833b940e640885f, SDK
139596497d57b219ddcc3616aa66ec58e1b3cf3c, and the unchanged certifi 2026.4.22 pure
wheel. Its native package/ABI and real SDK execution evidence remain in
../backend/SDK1217-EXECUTION.md and the earlier qualification receipts. This staging
operation adds no wheel, compilation, ABI retagging or Python replacement.

From the repository root:

```sh
node spikes/cloudflare-pyodide/dev/stage-qualified-runtime.mjs /path/to/exact/generated-runtime-sdk.mjs /new/task-owned/runtime
node --test spikes/cloudflare-pyodide/dev/stage-qualified-runtime.test.mjs spikes/cloudflare-pyodide/dev/stage-runtime-loader.test.mjs spikes/cloudflare-pyodide/dev/stage_runtime.test.mjs
node spikes/cloudflare-pyodide/dev/verify-qualified-runtime-stage.mjs
```

The output parent must already exist. Existing output directories are refused,
including partial attempts. Provenance is written last; a failed stage is not a
deployable success and is retained for reconciliation. The payload is copied
byte-for-byte. Both loader JavaScript dependencies and their hashes accompany it.
The command does not deploy, change the active dev runtime directory, select a
Cloudflare account or issue an execution request.

## Actual CT128 assembly

The exact artifact was read from the retained sdk-refresh-v5 run, mounted read-only
at /input/runtime.mjs. The packet uses current stage-qualified-runtime,
stage-runtime-loader and qualify-staged-runtime scripts plus the two current
backend JavaScript loader files. Cached image33cd92be... / Node v26.12.1 ran with
network none, 0.5 CPU/512 MiB/no additional swap/64 PIDs, 40-second outer bound.
CT128 stays 2 CPU/8 GiB/no swap. Output is the exclusive retained directory
/opt/bifrost-tests/pyodide-ddf316c1/runs/qualified-runtime-stage-v1/runtime.

The driver imports the full staged loader, constructs 468 module entries (including
the entrypoint and owned SDK cleanup adapter), verifies the certifi data buffer,
and verifies globalOutbound=null. A second valid-input staging attempt refuses
the existing directory and leaves the payload unchanged. Source hashes and the
actual result are in ../results/qualified-runtime-stage/assembly-result.json.
The task-owned --rm container is confirmed absent afterward; existing volumes
and all previous source/artifact/evidence paths are preserved.

Four focused tests pass: unqualified input before output creation, protection of
existing provenance on rejected input, complete loader dependency assembly, and
the unchanged historical stager's invalid-base guard. Node syntax checks pass.
No broad platform or Python suite was rerun for JavaScript assembly code.

This result is deliberately marked python_executed=false and cloud_invocations=0.
Node importing serialized Python sources/native-wheel data does not execute Python
or establish deployed 128 MiB memory, SDK transport, authorization or cancellation.
Serialized bytes are not compressed upload size or peak memory. Actual existing
engine/SDK run proofs keep their original scope; this is packaging readiness only.

## Remaining deployed gate

The existing isolated target is bifrost-pyodide-spike-ddf316c1, with the existing
dev API binding. Its last test window/temporary secret was retired. Selecting this
artifact in a separately reviewed cf CLI deployment package, reopening guarded
invocation, rotating a temporary guard, and paying for fresh children all remain
subject to the current explicit cloud hold. No such action occurred here.
Use the existing approved identity and cf CLI when an applicable bounded window
is granted; verify current target/API availability and cleanup before claiming
deployment or acceptance. Do not reuse historical allowances or source an alternate
credential. Canonical cloud transport, public Pyodide startup and deployed
resource/isolation enforcement are still unqualified.
