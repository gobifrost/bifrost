# Supported runtime bundle supply, run73

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
laptop Docker remained stopped. ABI, package execution and physical termination
remain unproven in CT128. Admission remains disabled. The smallest next step is
publisher resolution or authorized supply of the matching single file.
