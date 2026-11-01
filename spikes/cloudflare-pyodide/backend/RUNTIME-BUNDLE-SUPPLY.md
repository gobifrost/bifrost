# Supported runtime bundle supply, run73

Subsequent bounded publisher-source build evidence is below. It did not produce
a matching bundle and does not supersede the preserved download denial.

Thomas authorized identifying a legitimate supply path following the preserved
403. Delivery 811c5b11-2e83-457a-9d42-aaf2557acbe5 was acknowledged in native
run73. Starting source: b4009a664f00ae3d40450960a70091ffdbf29622. No denied
request was retried, and no different identity/host was used to fetch it.

## Publisher contract verified from source

Official cloudflare/workerd v1.20261023.1 resolves to
7caf6dee5bfe9af1b7710ddf66222900007af080. The following files were read through
the existing public Git checkout in CT128, without running publisher upload code:

* [build/python_metadata.bzl](https://github.com/cloudflare/workerd/blob/7caf6dee5bfe9af1b7710ddf66222900007af080/build/python_metadata.bzl)
  binds 314.0.6 / 2026-08-17 / backport 6 to integrity
  sha256-PD/VpBeSMOIeAY4osOc158Sr2P6OT/OdL9a7LqOi4mA=.
* [src/workerd/server/pyodide.c++](https://github.com/cloudflare/workerd/blob/7caf6dee5bfe9af1b7710ddf66222900007af080/src/workerd/server/pyodide.c%2B%2B)
  first checks the configured bundle cache for the exact filename, verifies its
  integrity and loads it. On cache miss it performs an ordinary GET against
  the canonical asset host using normal system TLS trust. The source attaches
  no login token or special authentication header. It requires HTTP 200 and
  verifies the downloaded bytes before caching them.
* src/pyodide/upload_bundles.py is publisher tooling. It uploads the named
  object to the publisher's bucket. That code is not consumer permission to
  access R2, mint credentials, create another mirror or run an upload.

Exact supported filename and digest:

```
pyodide_314.0.6_2026-08-17_6.capnp.bin
sha256:3c3fd5a4179230e21e018e28b0e735e7c4abd8fe8e4ff39d2fd6bb2ea3a2e260
```

The canonical URL remains the previously attempted URL in
results/ct128-run72/cache-denial.json. Its HTTP 403 is preserved, not retried.
The available evidence does not distinguish access policy, publication error,
missing object or another server-side cause. Changing user agents, credentials,
addresses or mirrors to defeat that response was not attempted.

## Existing supply checked

The publisher's [matching GitHub release](https://github.com/cloudflare/workerd/releases/tag/v1.20261023.1)
was inspected with the existing GitHub identity. All five listed assets are
native workerd binaries (Linux x64/arm64, Darwin x64/arm64, Windows x64). No
separate bundle asset is listed, so no guessed release URL was fetched.

The existing stopped bifrost-pyodide-ddf316c1 container was inspected read-only
using Docker changed-file metadata, selecting only bundle/cache pathnames. No
matching .capnp.bin was found. Selected existing workerd/Wrangler task cache
directories were searched by exact bundle filename patterns; no candidate was
found. This is not an exhaustive search of private caches or all machines.
No container was started, private cache copied, secret read or alternate-host
download attempted. No already available matching artifact was established.

## Exact remaining owner/publisher step

Either the publisher must restore anonymous access to the canonical object,
or an already legitimately held copy of exactly these bytes must be supplied
through the existing owner transfer route. A general authorization cannot
repair the vendor 403. No personal login flow is documented in the verified
consumer source; do not ask for a speculative token or introduce credentials.
If the publisher instead requires a new grant, account login or different
bundle/version, describe that concrete requirement for explicit approval first.

For an existing matching copy, the approved single-file destination is inside
CT128's exclusive /opt/bifrost-tests/pyodide-ddf316c1/inbox. Verify the SHA-256
before and after transfer using the existing pve-t340 -> pct push route; reject
symlinks, oversized/nonregular files and any digest mismatch. Do not copy a
whole cache or replace a conflicting existing object. Only after verification,
stage the single file in a task-owned bundle cache under this same scratch.
The pinned binary advertises --pyodide-bundle-disk-cache-dir and verifies cached
bytes itself. No runtime version, isolation flags, trust store or resource
settings may change as part of cache supply.

This procedure is prepared, not executed. Cache staging and the corrected
network-disabled ABI invocation must each receive a new receipt reservation;
retain the run72 failed receipts. Observe the actual five-field runtime ABI and
exercise native Pydantic validation before running the prepared termination
fixture. Do not run baseline/control-auth tests again for this supply check.

No new artifact became available in run73, so no ABI or termination invocation
occurred. CT128 remained idle at its existing 2 CPU/4 GiB allocation; heavy
laptop Docker remained stopped. Actual-workerd ABI, package execution and physical termination
remained unproven in that run. Subsequent official Node-hosted Pyodide qualification
is documented separately and does not replace workerd acceptance. Admission remains disabled. The smallest next step is
publisher resolution or authorized supply of the matching single file.

## Bounded publisher-source build attempt

The same verified public workerd commit exposes
`//src/pyodide:pyodide.capnp.bin@rule@314.0.6` in its `helpers.bzl`.
It packages the public, SHA256-pinned Pyodide core using publisher AST patches,
esbuild and the Cap'n Proto compiler. Version 314.0.6 has no legacy stdlib-wheel
lock tag in this source; it does not need the old package-bucket wheel inputs.
Building this target is a legitimate source route to investigate, but it is
not proof of byte-for-byte reproduction of the released backport. Only the
existing expected SHA256 could qualify an output.

The task-owned source was exported with `git archive` from commit
`7caf6dee5bfe9af1b7710ddf66222900007af080`, preserving the existing no-checkout
inspection repository. The checked-in `.bazelversion` pins Bazel 9.3.0. The
official Bazel binary was verified against its publisher release checksum
`d302d22ed77ee5658b87857249baa579db84300ecebe0a1e9a357b5b20f80eb8`.
No publisher upload script or R2/account API was executed.

| Attempt | Specific change | Observed outcome |
|---|---|---|
| v1 | Publisher target, one job, existing test image | Exit 37: publisher-specified SQLite patch could not run because `patch` was absent. |
| v2 | Extract task-local Debian patch 2.8-2 amd64, SHA256 `80c46a0085b33acc74bf451fddb6a70cb43d4ebcc4a873aec04d8d0a63ffc630` from existing Debian package metadata | Exit 33 during repository mapping: JVM unable to create native threads, EAGAIN. Container OOMKilled=false. |
| v3 | Add JVM ActiveProcessorCount=1 and loading_phase_threads=1 | Harness exit 125: accidentally reused preserved v2 container name. Bazel did not start; old container state in this receipt belongs to v2. |
| v4 | New exclusive container/run/packet name, same narrowed concurrency | Exit 33 during repository mapping: globbing pool still exhausted native thread allowance. Container OOMKilled=false. No bundle candidate generated. |

No compiler or publisher source patch was made. The small `patch` binary was
extracted under task scratch and selected only through this build's PATH; no
host package, container base image or workflow runtime was changed. Bazel's
downloader config blocks `pyodide-capnp-bin.edgeworker.net` and `r2.dev`, with
no rewrites. The supervisor never requests the target bundle. This records the
configured build boundary, not an independently captured network trace.

All build attempts used one CPU / 2 GiB / 256 PIDs inside existing CT128's
unchanged 2 CPU / 4 GiB allocation. JVM heap was capped at 512 MiB, with a
900-second total wall guard and 24 GiB disk-used guard. No limit was raised
after thread exhaustion. The respective supervisor durations were 17.404,
37.405, 3.029 and 35.034 seconds. No running containers remain; scratch,
source exports, downloaded public dependencies, receipts and stopped containers
are retained. Heavy laptop Docker stayed stopped.

Raw receipts/logs and prior exact scripts are in
`results/publisher-build-resume/`; current tested v4 supervisor is
`backend/ct128-publisher-bundle-build.py`. Every receipt's SHA256 was matched
after transfer. Offline check:

```sh
node spikes/cloudflare-pyodide/backend/verify-publisher-build.mjs
```

The failed builds are build-environment/thread-concurrency evidence, not a
Pyodide OS-contract incompatibility or a proven minimum resource requirement.
Next bounded source-build investigation must identify the exact Bazel globbing
and repository concurrency controls rather than repeat these commands or
increase capacity. Until a matching artifact actually exists, cache staging,
actual-workerd qualification and physical termination remain unexecuted. No
integrity pin or admission gate was relaxed to force progress.
