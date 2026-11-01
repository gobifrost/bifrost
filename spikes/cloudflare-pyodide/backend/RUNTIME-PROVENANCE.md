# Runtime and wheel provenance

Historical guarded bundle below was qualified at d09c4e113. The current
[refresh bridge bundle](REFRESH-CHALLENGE.md) adds one companion module while
retaining the runtime fingerprint guard. Use its verifier for current source;
the original guarded-probe source verification requires the historical checkout.

The resolver lock and running interpreter report different Pyodide patch
versions. The lock references Pyodide 314.0.7 CDN wheels; both the minimal Worker
and the full packaged closure actually report **314.0.6** under local Wrangler
4.156.0 / workerd 1.20261023.1. This is now a measured fact, not a guessed runtime
version inferred from a URL.

The full-closure probe records CPython 3.14.2, cache tag `cpython-314`, SOABI
`cpython-314-wasm32-emscripten`, and platform `emscripten-5.0.3-wasm32`.
`results/runtime-fingerprint.json` binds its source and actual Pydantic positive
and negative validation results. Existing `pydantic_core` 2.50.2 wheel metadata
states `cp314-cp314-pyemscripten_2026_0_wasm32` and `Root-Is-Purelib: false`.
The lock pins its archive SHA-256
`9a8a62281fd9c6a373b3fd3d473685e6838d43341eecde5f47729748e2c11ee3`.

No missing wheel was encountered in this experiment and no compilation,
retagging, native patch or replacement implementation was attempted. Actual
validation of coercible integer lists and rejection of invalid input succeeds.
This is relevant native-operation integration evidence, not the dependency's
upstream test suite or a general ABI compatibility theorem. Peak native memory
overhead is unmeasured. The original dependency qualification procedure still
requires bounded native builds and relevant upstream tests for viable unsupported
packages before considering replacements.

## Bootstrap guard

`runtime_guard.py` pins those five observed local runtime identity fields.
`runtime-runner.py` checks them before importing any upstream execution module
or setting runtime credentials. Drift raises `RuntimeCompatibilityError`;
there is no version fallback, token bypass or automatic requalification.
`pack-engine.py --current-runtime` includes the guard and its source digest.
The final guarded manifest has 478 modules and payload digest
`da076facf8aa457f7414952b5554a6ffc27829f3279c9803fe53986e8c6fefb9`.
Historical unguarded manifests remain unchanged.

The local fingerprint is not qualification for an unmeasured cloud runtime.
Cloud runtime drift should fail bootstrap until it is separately observed and
qualified under a new authorized test. This compatibility check does not prove
physical termination, isolation, trusted artifact selection or authorization.

Three Docker companion unit tests cover exact identity, each mismatched field,
and missing/extra observations. An actual Pyodide Worker imports the guarded
upstream worker and rejects an intentionally mismatched fingerprint before the
test effect marker. `results/guarded-runtime-final.json` binds the guard, runner,
packer, entrypoint and manifest. This negative probe changes a test observation;
it does not spoof the actual interpreter identity.

The initial guard used normal imports after its module-level compatibility
check. Its runtime probes passed, but focused Ruff reported E402. The final
bootstrap uses `importlib.import_module` after that check and after
`mark_execution_child`, preserving the necessary order without a suppression.
The initial runner, observer and manifest are retained under
`results/guarded-initial-*`; their earlier receipts are historical, not claims
about the final source. Focused Ruff passes for the final guard/runner/packer.

The final bundle also passes the actual two-tenant canonical local fixture:
`results/canonical-isolation-guarded-final.json` records genuine scoped SDK
reads/writes, wrong-peer 403s, independent values 70/78, duplicate-delivery single
launch, terminal credential 401s, fenced lost-ACK reconciliation, fresh state
canaries and cleanup. The earlier guarded pair is retained separately. The
final source passed 3 companion guard tests and 29 focused Node tests; no broad
API/client suite or type-check rerun is claimed for this companion-only change.

```sh
node backend/canonical-isolation.mjs --verify canonical-isolation-guarded-final --commit d09c4e113efea3ba9e3c8c94b42a6dfc6b5474fa
```

All three fingerprint/guard/authorized local supervisors were stopped afterward,
the test-network attachment was removed, and the test API started for this
qualification was restored to its previous stopped state. No cloud invocation,
runtime security setting change, credential grant or production change occurred.

## Commands

From the spike directory in the existing isolated tools environment:

```sh
python -m unittest discover -s backend -p test_runtime_guard.py -v
python backend/pack-engine.py --current-runtime --manifest guarded-runtime-manifest.json
wrangler dev --config backend/wrangler.runtime-probe.jsonc --port 8802 --ip 127.0.0.1
node backend/runtime-probe.mjs --run
node backend/runtime-probe.mjs --verify
```

Stop the first supervisor before running the guarded probe:

```sh
wrangler dev --config backend/wrangler.guarded-runtime.jsonc --port 8803 --ip 127.0.0.1
node backend/guarded-runtime-probe.mjs --run
node backend/guarded-runtime-probe.mjs --verify
```

The retained invocations run Wrangler/observers through the existing local
`bifrost-pyodide-ddf316c1` container, with no credentials or outbound capability.
Each observer refuses to overwrite its receipt; `--verify` is offline. These
are local companion tests, not host pytest or deployed acceptance.
