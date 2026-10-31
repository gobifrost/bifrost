# Dependency qualification: native compatibility first

This is the required procedure for subsequent experiments in this spike.
An unavailable wheel or a failed import does not establish runtime incompatibility.
Prefer an upstream-compatible native build over replacement code.

## Triage before adapting

Record the exact distribution/version, importing upstream function, failing
command/traceback, and whether the dependency is necessary on that execution path.
Distinguish these cases:

1. **Pure Python:** inspect transitive imports and runtime requirements. Package
   and exercise the real code. No PyEmscripten compilation is required for a
   genuinely pure wheel, but pure Python can still depend on unsupported sockets,
   threads, subprocesses or host services. Do not equate purity with compatibility.
2. **Compiled extension, matching wheel available:** install the exact wheel,
   record its digest and ABI, run relevant upstream package tests under Pyodide,
   then exercise it in an actual deployed Python Worker. A successful SDK fixture
   is useful integration evidence but is not the package's upstream test suite.
3. **Compiled extension, matching wheel missing:** inspect the native code and
   required OS facilities. If the runtime contract appears satisfiable, attempt
   a bounded native PyEmscripten cross-build in an isolated Linux tool environment.
   Missing distribution alone is not a reason to rewrite the dependency.
4. **Unsupported OS contract:** identify the exact required facility and relevant
   call path. Stop compilation once it cannot supply that facility. Do not emulate
   process isolation, authorization or durability merely to obtain an import.

## Build, test and deployment gates

Use the runtime selected by the pinned Cloudflare compatibility date and the
actual resolver output, not whichever Pyodide release happens to be newest.
The current tested package lock selects CPython `cp314`, ABI `cp314`, platform
`pyemscripten_2026_0_wasm32`, and Pyodide 314.0.7. A future runtime update requires
requalification. Never retag an incompatible wheel to bypass the resolver.

For a missing-wheel candidate, retain the source archive/commit and digest;
pin pyodide-build, cross-build environment, Emscripten, compiler/linker flags,
Python headers and dependent libraries. Verify their compatibility with the
runtime's PyEmscripten platform before building. Inspect wheel contents, imports,
linked libraries and platform tags. Record every patch as a reviewable diff.

The documented command sequence, after configuring the matching toolchain, is:

```bash
# In the candidate package source tree inside an isolated build container:
pyodide build . -o wheelhouse/
pyodide venv .venv-pyodide
.venv-pyodide/bin/python -m pip install wheelhouse/<exact-built-wheel>.whl pytest
.venv-pyodide/bin/python -m pytest <relevant-upstream-package-tests>
```

These are a procedure template, not evidence that a candidate build was run.
The package test selection must cover the native functions used by BiFrost,
error/cleanup paths and applicable async behavior. Preserve the test source
revision and exact command/results. Run third-party tests in that package's
isolated Pyodide environment; Bifrost's own backend tests still use `./test.sh`.
Do not replace difficult tests with import-only probes or add skips/retries.

Install the built wheel into the companion package through the supported Python
Worker dependency mechanism, then use **cf CLI** for isolated cloud deployment.
Exercise actual relevant operations there, including an upstream workflow consumer
when practical. Node/Pyodide test success does not establish Workers networking,
snapshot, filesystem, lifecycle or resource compatibility. Restore the private
resource configuration after any temporary guarded invocation tests.

Measure a baseline without the candidate and with it under equivalent conditions.
Record bundle size and available provider memory metrics. If peak linear memory,
heap or RSS is unavailable, report memory overhead as unmeasured; allocation size
and bundle bytes are not substitutes. Capture startup, CPU/wall time, concurrency,
exception and cleanup effects separately. Label estimates and instrumented-local
measurements distinctly from cloud measurements.

Stop when a required OS/runtime contract is demonstrably unsatisfied, or when a
bounded build attempt exposes a substantial new subsystem requirement. Record the
boundary and move to another experiment; do not silently replace native semantics.

## Required per-candidate record

```text
Dependency/version and upstream consumer:
Need on tested path; pure/compiled; OS requirements:
Failure and classification evidence:
Source URL/revision/digest; wheel availability:
Runtime, Python ABI, PyEmscripten platform, Emscripten and flags:
Build command/container identity/log; wheel digest:
Patches/build changes and why:
Relevant upstream tests/revision/commands/results under Pyodide:
cf deployment/version; actual Worker operation/result:
Baseline/candidate memory and bundle overhead; measurement limitations:
Runtime failures, cleanup/cancellation/security consequences:
Disposition and next bounded experiment:
```

## Current candidate inventory

| Dependency/surface | Evidence and disposition |
|---|---|
| pydantic-core 2.50.2 | Matching cp314/cp314/PyEmscripten wheel exists in pylock. Actual SDK DTO use passed locally and engine imports ran in deployed Worker. No custom build attempted or needed. Relevant package upstream tests have not been run; do not claim full package qualification. |
| PyYAML 6.0.3 | Matching wheel from pinned Pyodide exists in pylock. Bundling/import evidence does not prove all YAML native paths. No missing-wheel failure encountered; upstream package tests not run. |
| HTTPX 0.28.1 and context/engine Python code | Current runtime-supplied transport exercised with real synchronous/asynchronous HTTP and local SDK calls. No missing native wheel identified. OS assumptions must still be assessed per operation. |
| virtual_import / module_cache_sync | Pure Python source. The current child path already supports ordinary authenticated SDK HTTP without direct Redis/S3 access, but the module eagerly imports redis and has thread-local/locking assumptions. The 2026-10-31 follow-on imports the actual current worker/loader closure in a fresh local Python Worker with redis 7.4.0's pure-Python wheel. Its real synchronous RefreshingClient reaches the configured egress denial boundary. Authorized workspace retrieval and actual cloud qualification remain open. |
| SQLAlchemy/asyncpg, Redis and optional CLI/AI packages | Excluded from the tested execution closure, not classified as unavailable or fundamentally incompatible. Triage a specific necessary path before adding them or selecting a native build candidate. |
| fork/process supervisor, signals and /proc sampling | Required OS semantics in existing Linux launcher/pool code. A wheel build cannot create killable Linux child processes or their ownership boundary in a shared Python Worker. Preserve that material contract incompatibility. |

No missing-wheel candidate was encountered in the successful closure, so this
update makes no new native-build, package-test, cloud-deployment or memory-overhead
claim. Existing tested commits and raw evidence remain applicable to their exact
scope. The next dependency experiment should attempt the real virtual loader
closure before designing an alternate loader.

Sources checked for this procedure:

- [Building extension wheels](https://pyodide-build.readthedocs.io/en/latest/tutorials/quickstart.html)
- [Running package tests under Pyodide](https://pyodide-build.readthedocs.io/en/latest/how-to/testing.html)
- [PyEmscripten ABI requirements](https://pyodide.org/en/stable/development/abi.html)
- [Cloudflare Python package support](https://developers.cloudflare.com/workers/languages/python/packages/)
