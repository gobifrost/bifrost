# CT128 runtime qualification, run72

Historical stopping point. [Subsequent exact publisher-source reconstruction](../SOURCE-RUNTIME-QUALIFICATION.md)
resolved bundle supply and passed actual-workerd ABI/native validation. The new
timed cancellation observation still fails the physical-stop requirement.

Delivery 8e8c7bc2-9477-4a1a-b1cc-5b68d2c08e64 was acknowledged in native run72.
Tested source: 785b560b9973020e1c13401383e1a73a87a54940. Existing CT128 ownership,
hostname, idle container state and 2 CPU/4096 MiB/zero-swap allocation were
rechecked before work. Laptop heavy containers stayed stopped. Baseline and
control-auth tests were not repeated. No cloud execution, R2 access, credentials,
guest settings, resource expansion or production changes occurred.

## Observed commands and results

Each Python harness was sent through the existing connection, for example:

```powershell
Get-Content -Raw spikes/cloudflare-pyodide/backend/ct128-runtime-stage.py | ssh -o BatchMode=yes -o ConnectTimeout=6 pve-t340 "pct exec 128 -- python3 -"
```

Use the same command form for ct128-runtime-probe.py and ct128-runtime-cache.py.
All harnesses reserve unique run directories and refuse reuse; do not replay
them or discard their existing reservations. Full argv and timestamps are in
results/ct128-run72. The tested harness hashes are in evidence-index.json.

1. **Dependency staging passed**, 12.34 s. Public npm dependencies installed
   using the existing package-lock.json, npm ci --ignore-scripts --no-audit
   --no-fund. No private profiles/caches were transferred. Wrangler 4.156.0 and
   workerd 1.20261023.1 remain pinned. All eleven wheels from pylock.toml were
   downloaded from its exact PyPI/jsDelivr URLs and SHA-256 verified before
   extraction. Redirects outside those package hosts were refused.
2. **Source packaging passed**: 73 current upstream source files, 455 modules,
   payload SHA-256 335dcadf196bd86b679caf3af7a5cb6ca9bdf5c346c8f349be69fb35d98d9ea8.
   This is provenance, not compatibility. No generated patches from a private
   laptop environment were silently copied into the guest bundle.
3. **Local ABI probe failed**, 3.52 s including cleanup. Wrangler became ready,
   but POST returned HTTP 500 because workerd needed its Pyodide runtime bundle
   and network=none refused DNS for pyodide-capnp-bin.edgeworker.net. Python did
   not start; imports, native extension execution and ABI are not qualified here.
4. **Bounded dependency workaround failed with HTTP 403** at the exact official
   public bundle URL below. No redirect, alternate host, identity, R2 route,
   credential or automatic retry was used. The original ABI failure and this
   denial have distinct preserved receipts.

The npm container and ABI container were bounded to 1.5 CPU, 3072 MiB, no swap
and 256 PIDs, within the existing guest ceiling. Staging has a 900 s deadline;
the ABI harness has a 180 s envelope (90 s readiness + 70 s request observer).
Disk guards use 24 GiB used and initial free-space checks, not a filesystem
quota. The runtime probe used network=none and published no host ports. Both
owned containers were stopped/read back, volumes and scratch preserved. Guest
readback after work: about 31 GiB free, no running containers. No process peak
memory or Python memory overhead was measured because Python never started.

## Dependency and ABI boundary

The matching native wheel is available, not a missing-wheel build candidate:
pydantic-core 2.50.2, cp314-cp314-pyemscripten_2026_0_wasm32,
SHA-256 9a8a62281fd9c6a373b3fd3d473685e6838d43341eecde5f47729748e2c11ee3.
Pure-wheel dependencies are annotated-types 0.8.0, httpx 0.28.1, idna 3.20,
pydantic 2.14.2, python-dotenv 1.2.4, redis 7.4.0, typing-extensions 4.16.0,
typing-inspection 0.4.4 and workers-runtime-sdk 1.9.5. PyYAML 6.0.3 also uses an
existing matching PyEmscripten wheel. Raw wheel URLs/hashes are in staging.json.
No package was replaced or compiled; package tests under Pyodide remain unrun.

Expected ABI is the previously qualified pin: Pyodide 314.0.6, CPython 3.14.2,
cpython-314, cpython-314-wasm32-emscripten, emscripten-5.0.3-wasm32. These are
**expected**, not observations from this guest. The resolver's 314.0.7 wheel
URLs do not establish the executing interpreter version.

The official workerd tag v1.20261023.1 resolved to
7caf6dee5bfe9af1b7710ddf66222900007af080. Its
[runtime metadata](https://github.com/cloudflare/workerd/blob/7caf6dee5bfe9af1b7710ddf66222900007af080/build/python_metadata.bzl)
and server/pyodide.c++ identify and integrity-check:

```
https://pyodide-capnp-bin.edgeworker.net/pyodide_314.0.6_2026-08-17_6.capnp.bin
sha256:3c3fd5a4179230e21e018e28b0e735e7c4abd8fe8e4ff39d2fd6bb2ea3a2e260
```

The pinned binary's serve --help exposes local Pyodide bundle/package cache
options. Supplying this exact authorized public artifact through an existing
permitted route is the smallest remaining prerequisite. The 403 was not worked
around. This is an artifact-access blocker, not proof of an incompatible OS
contract or permission to rebuild/replace the runtime to evade the denial.

## Prepared termination fixture, not executed

guest-termination-entry.mjs uses the existing actual LOADER.load + Python
workerCode mechanism, globalOutbound=null, fixed CPU/catch fixtures and fresh
loads. It accepts no arbitrary workflow source or platform credentials.
wrangler.guest-termination.jsonc is local-only with public routes disabled.

After ABI/dependencies qualify, an owner may start the local parent at 8811
inside the network-disabled guest test container using the pinned Wrangler:

```sh
node node_modules/wrangler/bin/wrangler.js dev --local --ip 127.0.0.1 --port 8811 --config backend/wrangler.guest-termination.jsonc
node backend/guest-termination-probe.mjs --run
```

Do not run this now: the required runtime artifact is still unavailable. Use a
new bounded supervisor reservation (at most 120 s), same guest/container limits,
no host ports and only synthetic data. No cloud variant or authorization follows.

The Node driver observes callbacks on a separate event loop at loopback 8820.
It records monotonic before/caught/after and abort-request timestamps. Modes:
completion negative control first (must emit after), caught-quota negative
control, then request abort. A failed completion control prevents the abort run.
Each request is capped at 20 s plus a 5 s observation interval. Existing raw
receipts are protected by exclusive reservation. A later effect after the
recorded abort is a counterexample; bounded silence is only an observation.
Harness/container shutdown is cleanup and never physical-stop acceptance.

The reviewed [loader API](https://developers.cloudflare.com/dynamic-workers/api-reference/)
does not supply a proven physical-stop receipt to this adapter. Its documented
[custom resource limits](https://developers.cloudflare.com/dynamic-workers/usage/limits/)
throw exceptions; an exception or accepted abort is not proof noncooperative
work ceased. The fixture deliberately reports physical_termination_verified=false
and canonical_late_result_fencing_verified=false. It does not replace the
canonical platform result authority with a synthetic queue or fake fence.

Required future cloud proof is separate: a newly authorized fresh Python Worker
run, a trusted actual executor stop mechanism/receipt, independent observation
that noncooperative work ceased, successful no-stop/caught-exception controls,
and a real upstream owner/attempt-fenced rejection of late terminal delivery
with logs, credentials and cleanup verified. Prior Linux fencing tests cannot
be combined with an unmeasured stop to claim this. No such cloud action is
authorized in this continuation. Execution admission stays disabled.

## Lightweight verification and CI

Eleven offline tests pass: eight diagnostic guards and three timed-termination
classification/driver opt-in tests. Command:

```sh
node --test spikes/cloudflare-pyodide/backend/timed-termination.test.mjs spikes/cloudflare-pyodide/probe-io.test.mjs
```

The new regression test's pathname check/use pattern was removed: absence is
observed by directory listing and the subsequent independent safe receipt is
created with wx. Existing network-response write flows and await dispositions
remain documented; no scanner alerts were dismissed. The exact starting head
785b560b has passing ordinary checks but CodeQL remains failed (22 annotations,
including those two test races). New-head CI must confirm any change in findings.
