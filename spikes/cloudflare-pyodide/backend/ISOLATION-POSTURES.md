# Isolation postures without per-attempt Dynamic Workers

Measured 2026-11-12 (local `workerd` 2026-10-23, `wrangler` 4.156.0, Python 3.14.2 / Pyodide,
spike `4afd838d0`). 39 attempts per posture, each posture on its own supervisor. Extends the
Phase-1 hostile canary suite to the postures we would use if we stopped calling `LOADER.load`
per attempt.

## Answer

**Without per-attempt `LOADER.load` you get tenant-to-tenant isolation and nothing stronger.**
Attempt-to-attempt isolation is not reachable by any in-isolate technique in this runtime.

| Posture | Across attempts | Across tenants | Cost | Verdict |
|---|---|---|---|---|
| `fresh` — `LOADER.load` per attempt (current) | full | full | 1 Dynamic Worker **per attempt** (~$18/10k after the 1,000 included) + ~2.1 s Python boot | only posture passing every canary |
| **A** one long-lived worker, no reset | none | none | ~0 creation; ~2 ms/attempt | indefensible |
| **B** sub-interpreter / fresh Pyodide per attempt | not available | n/a | n/a | does not exist |
| **C** cached + best-effort manual reset | partial, unsound | none | ~0 creation + ~14 ms reset | must not ship |
| **D** one Worker per tenant | none within a tenant | full | 1 Dynamic Worker **per tenant per day** | strongest defensible alternative |

Canary totals (PASS = a later attempt observed nothing): fresh **19/19**, A **1/19**,
C **13/19**, D **3/19**.

## Why manual reset (C) cannot be rescued

The reset tested was stronger than anything plausible in production — bulk builtins removal,
non-baseline `sys.modules` deletion, shallow `__dict__` restore of every baseline module,
`meta_path`/`path`/`environ` restore, `/tmp` wipe — and it failed four independent ways:

1. **Nested object graph.** `json.JSONEncoder._LAB_CRED` is a class attribute, not a module-dict
   key; a successor recovered the secret from `type:json.JSONEncoder._LAB_CRED`.
2. **C-level registries.** `codecs.register_error("lab_evil", …)` still resolves after reset;
   there is no public clear API.
3. **The exclusion set is the hiding place.** `sys`, `os`, `asyncio`, `workers`, `_pyodide*`,
   `importlib` cannot be rewritten without breaking the runtime, so `sys._LAB_STASH` survives.
   This needs no harness knowledge.
4. **The reset runs inside the interpreter it is cleaning.** A hostile that recognises the hook
   disarms it (`disarmed=["main"]`).

Plus `atexit` callbacks cannot be removed (`atexit_delta: 1`), and `/session` is writable and
outside a `/tmp`-only wipe.

## Why B is not an option

Inside a cached isolate: `_xxsubinterpreters`/`interpreters` → `ModuleNotFoundError`;
`globalThis` exposes no pyodide/instantiate/wasm symbol; `pyodide_js.runPython` shares one
namespace across calls (counter 1 → 2); `js.eval` is disallowed; `/lib` contains no `.wasm`.
There is no fresh runtime to create.

## Posture D's real win and its limit

```
PASS tenant 6a-cross-tenant-bleed — A_sees_B=false B_sees_A=false
PASS tenant 5b-worker-env-scope
FAIL tenant 6b-within-boundary-bleed — B reads A's builtins/env/module/fs markers
FAIL tenant 5-creds-separation — a later attempt recovers the prior attempt's secret
```

D is defensible **only as an explicit policy change**: the trust boundary moves from *attempt*
to *tenant*. Inside a tenant, a hostile or buggy first attempt still owns every later attempt's
credentials, `/tmp`, builtins and module cache — D degrades to A for retries and re-runs of the
same org. Under the trusted-operator model that is a blast-radius decision an operator can
legitimately make; it is not an isolation guarantee.

## Two runtime facts that constrain every option

- **Entropy is blocked during startup**: `uuid.uuid4()` at import raises
  `OSError: Randomness is not allowed while a Worker is starting...`. Per-isolate nonces must be
  minted post-start.
- **A cached name's code is immutable**: `get(sameName, () => differentCode)` keeps the old code;
  only a new name returns new code. Under D, attempt-code updates force id rotation, which
  re-multiplies the creation meter.

## Consequence for the platform

- Anything needing "an untrusted attempt inherits nothing" **requires** per-attempt Dynamic
  Workers. The Phase-1 result stands.
- Per-tenant Workers are the cost-efficient posture for trusted tenants, with the boundary
  explicitly documented as tenant-level.
- Manual reset is not an isolation mechanism and must never be presented as one.

## Residual gaps

Canary 11 (class-attribute dispatcher seizure) reported `seized=true` but did not take over
dispatch in this runtime — unproven, not proven-safe. All timings are local, not cloud-metered.
