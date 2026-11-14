# Native package qualification in CT128

Resumed from clean branch head 688e7a0f4f25aa8a280838da2a7d185740d2aa72.
The existing CT128 allocation remained 2 CPU/4 GiB, with no running foreign
containers. No heavy laptop tests, cloud invocation, R2 access, credential,
security setting, resource expansion or baseline/control-auth rerun occurred.

## Workerd supply diagnosis

The pinned native binary, not a reconstructed URL, was asked to select its
bundle with network=none and verbose diagnostics. It selected exactly:

```
https://pyodide-capnp-bin.edgeworker.net/pyodide_314.0.6_2026-08-17_6.capnp.bin
```

Its log shows the lookup was refused by the disabled network before any GET.
This confirms the earlier selection was correct; it does not retry or repair
the preserved 403. The native probe used 0.5 CPU/512 MiB and stopped with exit 1.
Cloudflare runtime supply remains a separate prerequisite.

## Independent official Pyodide execution

Instead of treating that blocker as the end of dependency work, official
Pyodide's matching core distribution was obtained from its actual publisher
release, for **package testing only**:

```
https://github.com/pyodide/pyodide/releases/download/314.0.6/pyodide-core-314.0.6.tar.bz2
sha256:1016c31e39ce3764d9a418cbb491a392c802c1b86ccc1367f009f5c59bf8f5fd
```

This is the hash already recorded for 314.0.6 in pinned workerd source metadata.
It is a different distribution from workerd's capnp bundle, not an alternate
mirror of the denied object, and was not supplied to the Cloudflare backend.
Existing pinned native wheels were mounted read-only. No extension was replaced,
retagged or rebuilt; matching native wheels were already available.

Actual observed runtime:

| Field | Observed value |
|---|---|
| Pyodide | 314.0.6 |
| Python | 3.14.2 |
| cache tag | cpython-314 |
| SOABI | cpython-314-wasm32-emscripten |
| platform | emscripten-5.0.3-wasm32 |
| Pydantic | 2.14.2 |
| pydantic-core | 2.50.2 |
| PyYAML | 6.0.3 |

Initial native checks passed: integer coercion, strict integer rejection,
Pydantic model validation, and CSafeLoader parsing. The `.so` extensions ran
under WASM, not Linux Python. Guest containers were network-disabled with
1 CPU/1024 MiB/no swap/128 PIDs and a 120 s execution timeout. No peak memory
measurement is claimed; the enforced limit is not measured consumption.

## Upstream tests, not rewritten assertions

**pydantic-core: 32 passed, zero failures/errors/skips**, 0.196 s reported by
pytest inside Pyodide. The exact source archive came from the existing uv.lock:

```
pydantic_core-2.50.2.tar.gz
sha256:1b0e8dc3932cf4e9ed5727570325cacc716b9409ffc794a221c0ccc72e8eb775
```

tests/serializers/test_none.py and test_json.py were copied byte-for-byte from
that verified archive. These two self-contained modules execute their real
native SchemaSerializer assertions with real pytest 9.0.2. Test-tool wheels
and transitive hashes came from the verified core's pyodide-lock.json; they were
downloaded before execution. Shared upstream conftest/Hypothesis/GC fixtures,
other modules and the complete package suite are **not qualified** by this subset.
No test assertion was replaced or source-specific skip added.

An initial attempt to inspect tag v2.50.2 in the old pydantic-core Git repository
failed because that tag is absent. Wheel metadata identifies the source as the
Pydantic monorepo. The exact pinned PyPI source archive, which contains the tests,
resolved this without guessing a moving Git revision.

**PyYAML: 261 upstream native scanner/parser/emitter cases passed.** The exact
6.0.3 source archive hash is
d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f.
Its unchanged test_appliance and test_yaml_ext modules ran against
/packages/yaml/_yaml.cpython-314-wasm32-emscripten.so. Selection used the
upstream harness's YAML_TEST_FUNCTIONS setting for test_c_scanner,
test_c_parser and test_c_emitter. Existing upstream fixture selection and
.skip-ext exclusions remain unchanged; this is not the entire PyYAML suite.

The first PyYAML observer failed to produce a result: its Python regex was
incorrectly escaped through a JavaScript template literal. The failed generated
driver and receipt are retained. The correction uses simple line parsing and
captures exceptions separately. A subsequent launch was refused (exit 125)
because the original stopped container name was preserved. A new explicit name
and receipt directory then completed successfully. These are diagnosed harness
defects, not unexplained flaky tests: no timeout increase, unchanged retry,
test suppression or removal of the old containers/receipts was used.

## Reproduction and evidence

The following source harnesses were sent using existing SSH/pct access, e.g.:

```powershell
Get-Content -Raw spikes/cloudflare-pyodide/backend/ct128-pyodide-package-qualification.py | ssh -o BatchMode=yes -o ConnectTimeout=6 pve-t340 "pct exec 128 -- python3 -"
```

Then ct128-pydantic-upstream-tests.py and ct128-pyyaml-upstream-tests.py ran
the package selections. These reserve unique owner paths and refuse duplicates.
Do not rerun an existing reservation; preserve or reconcile it first. The source
sdists were verified and staged in the owner scratch during source inspection;
their exact URLs/hashes are in uv.lock and the receipts/documents above.

Raw records are in results/native-package-resume: observed ABI/package receipt,
upstream dependency/test hashes, JUnit, PyYAML output, the two failed PyYAML
receipts, native selection and an evidence index binding the exact harnesses.
All transferred artifact hashes were read back. All owned containers are stopped,
with exit 0 on the successful package runs and OOMKilled=false; original failed
containers, guest scratch and volumes remain. No private cache was copied.

This advances native dependency compatibility while preserving the architectural
boundary. It does **not** establish Cloudflare Worker startup, real BiFrost SDK
execution in this Node harness, deployed acceptance, isolation or physical stop.
The Cloudflare adapter must still obtain its exact workerd bundle and pass those
separate gates. Node Pyodide has not been introduced as a replacement executor,
and execution admission remains disabled.
