# Fresh runtime staging includes the SDK cleanup module

The current backend runtime-code.mjs imports sdk-cleanup-code.mjs, but the dev
stager copied only runtime-code.mjs. A fresh stage therefore could not import its
loader. A previously populated directory or historical deployed version is not
proof that a new deployment bundle closes this dependency.

The stager now calls stageRuntimeLoader, which copies both unchanged JavaScript
files and returns their SHA256 values. Those values are included in the generated
runtime provenance as loader_files_sha256. The existing base artifact digest and
engine source checks are unchanged; no alternate unpinned artifact is accepted.
This is a pure JavaScript packaging dependency, not a missing PyEmscripten wheel
or an unsupported Python OS operation. No dependency compilation/replacement is
needed for this fix.

The new regression stages into an empty task-owned temporary directory, imports
the resulting runtime loader, and checks that it injects the actual sdk_cleanup
adapter source and resolves both JavaScript files byte-for-byte. Before the fix,
the same test fails ERR_MODULE_NOT_FOUND for sdk-cleanup-code.mjs. The existing
invalid-base test still rejects before derived staging. After the fix both pass:

```sh
node --test spikes/cloudflare-pyodide/dev/stage_runtime.test.mjs spikes/cloudflare-pyodide/dev/stage-runtime-loader.test.mjs
```

The regression uses a tiny packaged-module sentinel. It verifies loader assembly,
not Python imports, SDK API calls, full bundle deployment, startup latency, runtime
memory or cloud compatibility. Earlier real engine/SDK evidence retains its exact
pins. The production bundle and existing dev runtime output directory were not
restaged or deployed in this increment. No credential or account API is touched.

Observed post-fix test output is in ../results/loader-staging/after.txt. Existing
cloud windows remain retired. The next deployed canonical/SDK acceptance still
requires an applicable bounded window, its actual pinned artifact, isolated API
identity/provenance and cleanup; this packaging fix does not authorize invocation.
