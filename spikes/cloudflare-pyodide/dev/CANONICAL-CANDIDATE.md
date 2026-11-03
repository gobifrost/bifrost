# Guarded canonical candidate: offline build

The earlier deployed executor rejects canonical mode. The newer canonical RPC
path has real engine/SDK/ownership/reconciliation evidence on CT128, but has not
been deployed. canonical-entry.mjs is a separate SDK-only candidate for connecting
that transport to the existing dev API Service Binding. It does not replace the
ordinary entry/configuration or enable upstream public executor admission.

It reuses actual engine invocation, result envelopes, bounded/lossless RPC,
source verification, deadline egress gate, invocation stream and log-delivery
diagnostics. The existing temporary guard/expiry contract is retained. Only the
fixed canonical fixture shape is admitted; refresh fixtures and direct mode are
rejected. There is no general arbitrary-workload classifier or new job queue.
BiFrost still owns authorization, claims, attempts, results and recovery.

The candidate passes trusted public URL metadata through the existing bootstrap
parameter instead of presenting the virtual SDK URL as the platform URL. Ordinary
local callers retain the bootstrap's existing None default. That composition has
source tests; the new cloud candidate has no runtime acceptance yet.

## Assembly and dry run

```sh
# Run from spikes/cloudflare-pyodide. Output must not already exist.
node dev/stage-canonical-candidate.mjs /path/to/qualified/generated-runtime.mjs /new/owned/project
cd /new/owned/project/dev/executor
DEV_EXECUTOR_OPEN=false DEV_EXECUTOR_EXPIRES_AT=0 cf deploy --dry-run
```

The stager requires bundle SHA256 61d818aef7f66a129951c7eb0cc4dc638277da35e635e13965b90f8a23575731,
checks its actual engine/module identity through stageQualifiedRuntime, copies
only fixed source/lock files, and writes provenance last. It never copies env
files, profiles or credentials. The existing repository executor config is
unchanged; only the separate output points to canonical-entry.mjs.

Actual CT128 output: /opt/bifrost-tests/pyodide-ddf316c1/inbox/canonical-build-v1/project.
cf 1.0.0-beta.26 / Wrangler 4.156.0 / Node 26.12.1 builds successfully. The container
had network=none, 1 CPU, 2 GiB memory/swap ceiling, 128 PIDs, 120-second wall bound,
an isolated HOME and no supplied credentials. Cached dependencies were mounted
read-only at both project/node_modules and project/dev/executor/node_modules;
the CLI's package-local Wrangler check needs the latter. No install occurred.

The first assembly failed before output creation because the image's default
entrypoint user could not write the scratch directory. Readback confirmed output
absent; the established direct Node entrypoint resolved it. The first cf build
then failed at the package-local dependency check; adding the second read-only
mount resolved that. Both failures remain recorded in canonical-build-v2-receipt.json.

The successful command was the cf CLI, which delegated bundling to its pinned
Wrangler internally. Official documentation says
[cf deploy --dry-run](https://developers.cloudflare.com/cf/projects/#deploy)
builds/validates without API requests or upload. Here network isolation also
prevented account access. Measured build output: 14,940.39 KiB / gzip 4,309.83 KiB.
The main bundle is 15,298,957 bytes with SHA256
09d84c2b9db9578c11a053ab82b4ac9e49f62f92b57c8aefcc1e901894cd5859.

Emitted worker configuration readback confirms the existing test Worker name,
workersDev=false, previewUrls=false, expiry 0, no DEV_AUTH binding, existing API
Service Binding and CPU 1000 ms. The child keeps the existing cloud 12-subrequest
cap. This cap has not yet been qualified for canonical fixtures; no increase is
silently applied. Builder containers are absent after cleanup; source/output
remain in owned scratch. Source, runtime, compiled config and command receipts
are preserved alongside this document. verify-canonical-build.mjs checks them.

## Remaining acceptance

This proves assembly/build, not startup, execution, deployment or cloud cost.
No account query, upload, deployment, secret creation, endpoint reopening or
Cloudflare invocation occurred. The standing live-test hold remains in effect.

Next: qualify the closed guard in the local runtime and prepare the exact bounded
cloud acceptance/cleanup packet before any applicable fresh approval. Live
canonical ownership/result reconciliation, cancellation control/recovery and
resource enforcement still need deployed proof. Untrusted admission remains
disabled. Logs after revocation have an explicit persistence gap, and physical
settlement remains unproved; successful completion does not require isolate kill.

The next live plan must also reconcile the API origin/source: the existing dev
Service Binding and newer canonical test stack are separate resources. Build
success does not establish matched credentials, schema or upstream versions.
Read-only guest/source checks remain available without cloud access.

Post-build free disk was 8177 MiB, below the existing 8192 MiB heavy-test guard.
The build's .wrangler temporary directory was under 1 MiB; nothing was deleted
to force the guard through, and no volume, resource size or threshold changed.
Recheck capacity before further heavy tests. The supported cf build passed;
a separate direct Wrangler version probe lacked standalone esbuild resolution.
Installed package metadata confirmed Wrangler 4.156.0. This packet uses cf CLI,
not that unsupported standalone invocation.

The later closed-guard and actual schema readbacks are in CANONICAL-ALIGNMENT.md.
The compiled bundle/source map are now retained in a checksum-verified archive;
restore the verified members before any future prebuilt use. Source and pinned
runtime inputs remain available, and no live deployment has been performed.
