# Exact publisher bundle built; actual workerd resumed

Subsequent [cold and warm canonical SDK qualification](WORKERD-SDK-QUALIFICATION.md)
now passes in guest workerd. The ABI and negative termination observations below
remain separate evidence, and deployed acceptance remains incomplete.

The recorded HTTP 403 is preserved. Without retrying that download, changing identity or weakening integrity checks, the publisher's public source produced the **exact existing bundle SHA256**:

`3c3fd5a4179230e21e018e28b0e735e7c4abd8fe8e4ff39d2fd6bb2ea3a2e260`

The generated 13,727,600-byte file was independently hashed, copied with exclusive creation into a task-owned cache, hashed again, and accepted by unchanged workerd's own integrity verification. The bundle-supply blocker is resolved for this guest. No binary is substituted for a different version or uploaded to Cloudflare.

## Build changes and exact results

Publisher source remains `cloudflare/workerd` commit `7caf6dee5bfe9af1b7710ddf66222900007af080`. Target: `//src/pyodide:pyodide.capnp.bin@rule@314.0.6`. Bazel 9.3.0 and the task-local patch tool retain their verified release/package checksums. No publisher source or compiler flag was patched.

| Attempt | Specific change | Actual result |
|---|---|---|
| v5 | `--legacy_globbing_threads=1` | Passed previous thread bottleneck; exit 1 because compiler was absent. |
| v6 | Disposable build-container Clang/LLD, pinned `1:19.0-63` | Reached native compilation; exit 1 because the publisher-selected libc++ headers were absent. |
| v7 | Add matching pinned libc++/libc++abi development packages | Exit 0; exact bundle digest matches. Supervisor elapsed 157.073 seconds. |

The pinned [Bazel source](https://raw.githubusercontent.com/bazelbuild/bazel/9.3.0/src/main/java/com/google/devtools/build/lib/pkgcache/PackageOptions.java) declares a separate default 100-thread legacy globbing pool; `--jobs=1` alone did not bound it. The final build retained one CPU / 2 GiB / 256 PIDs, 512 MiB JVM heap, one loading/globbing/build job, 900-second wall and 24 GiB disk-used guards. Existing CT128 stayed 2 CPU / 4 GiB, no swap. No resource limit, sandbox enforcement, workflow runtime setting or host package was changed. Clang/LLD/libc++ were installed only in the disposable build container through normal signed Debian repositories; exact installed versions are preserved in `v7-toolchain-packages.tsv`.

The supervisor does not request the target asset. Bazel's downloader blocks the denied asset host and r2.dev without rewrites; this is a configured boundary, not an independently captured packet trace. No account/R2 API, upload, new credential, remote build service, laptop Docker or production resource was used. Previous failures and exact scripts are retained.

## Actual workerd ABI/native operation

`backend/ct128-runtime-from-source.py` used existing Wrangler 4.156.0 and workerd 1.20261023.1, native binary SHA256 `b6aff93fcc97091fa38b4ba78c172276349c4940000694c5baf382c218b8e6ee`. A minimal shell wrapper supplies only the supported `--pyodide-bundle-disk-cache-dir` option. The runtime binary, security flags and integrity pin are unchanged.

In a **network-disabled** one CPU / 1 GiB / 256-PID container, the probe returned HTTP 200 and verified Pyodide 314.0.6, Python 3.14.2, cpython-314, cpython-314-wasm32-emscripten and emscripten-5.0.3-wasm32. Actual native pydantic-core 2.50.2 validated coercion to `[1,2]` and rejected invalid input. Supervisor elapsed 10.877 seconds. This is actual local-workerd ABI/native-operation evidence, not a deployed Cloudflare execution or a full package test suite.

## Actual timed cancellation counterexample

`backend/ct128-source-termination.py` then ran the prepared fixed synthetic fixture in network-disabled workerd with the same verified cache and one CPU / 1 GiB / 256 PIDs. The independent Node observer recorded:

- Completion control: `before`, then `after`, HTTP 200.
- Catch case: finite CPU loop completed with HTTP 200; no quota exception was caught. This does not reproduce or refute Cloudflare's separate caught-quota observation.
- Abort case: `before` at 2156.225 ms; independent abort request at 2166.582 ms; later `after` at 3838.886 ms. The later effect occurred **1672.304 ms after the recorded abort request**.

The abort's HTTP outcome serialized as `{}` because its fixed string rejection has no `name`; the ordered independent effect events establish this counterexample. No physical-stop inference comes from that empty outcome or subsequent harness cleanup. Observer exited 0 because it records observations, not because cancellation acceptance passed. Physical termination, canonical late-result fencing in this same experiment and execution admission all remain **false**. Overall supervisor elapsed 43.599 seconds. The finite loop and bounded observation cannot prove cessation; the later effect positively disproves prompt physical cancellation by this request-abort mechanism.

All owned containers stopped after each phase; volumes, source, cache and receipts retained. No deployed Cloudflare invocation occurred. Upstream scheduling, authorization and Linux defaults remain unchanged. The next permitted implementation step is the real canonical SDK path in this now-usable guest workerd; it must not be mislabeled deployed acceptance or untrusted termination equivalence.

## Evidence and commands

Raw `results/publisher-build-resume/v5-*`, `v6-*`, `v7-*`, `results/source-runtime-resume/` and `results/source-termination-resume/` retain exact commands, source/packet hashes, ABI, events and cleanup. Supervisor paths above refuse existing output directories; future runs require new reviewed exclusive names.

```sh
node spikes/cloudflare-pyodide/backend/verify-source-runtime.mjs
node --test spikes/cloudflare-pyodide/backend/termination-observer.test.mjs spikes/cloudflare-pyodide/backend/timed-termination.test.mjs spikes/cloudflare-pyodide/backend/termination-evidence.test.mjs
```

The verifier checks raw receipt digests and asserts the negative termination outcome. No cloud or API request is made by these offline commands.
