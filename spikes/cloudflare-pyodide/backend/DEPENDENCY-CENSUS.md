# Dependency census: what the qualified bundle actually ships

Measured 2026-11-12 from the qualified runtime (`spikes/cloudflare-pyodide/python_modules`,
`pylock.toml`) on spike head `627bc65aa`. This is a census of shipped packages, not a
claim about what any particular customer workspace needs.

## What ships today

Third-party distributions present in the bundled `python_modules`:

| Package | Kind | Purpose |
|---|---|---|
| `httpx` (+ `idna`, `certifi` overlay) | pure Python | SDK/engine HTTP transport |
| `pydantic` + `pydantic_core` | pure + wasm wheel | SDK models |
| `python-dotenv` | pure | config |
| `PyYAML` (`yaml`, `_yaml`) | pure + wasm wheel | manifest/workflow config |
| `redis` | pure Python | engine module cache path (child never connects) |
| `typing_extensions`, `typing_inspection`, `annotated_types` | pure | transitive |
| `workers-runtime-sdk` | platform | Cloudflare Python entrypoint glue |

Lock file declares exactly: `httpx`, `pydantic`, `python-dotenv`, `pyyaml`,
`workers-runtime-sdk`, `redis`.

## The gap this creates

Linux workers `pip install` the workspace's `requirements.txt` per workspace
(`api/src/services/execution/simple_worker.py`). Workers cannot: there is no `pip`, only
pre-bundled Pyodide wheels. So any workflow importing a package outside the table above
fails at import time with `ModuleNotFoundError`, and the capability gate now rejects it
before effects when the module is declared.

Common MSP automation imports that are **not** bundled today: `requests`, `msal`,
`azure-identity`/`azure-*`, `msgraph-sdk`, `boto3`, `pandas`, `cryptography`, `paramiko`,
`msoffcrypto-tool`, `beautifulsoup4`, `jinja2`.

## Decision this sets up

Two viable strategies, and the choice is per-package, not global:

1. **Pre-bundle a curated common set.** Cheap for pure-Python packages; bounded by the
   64 MiB uncompressed Worker limit (current bundle ~15 MiB). Needs a measured experiment
   per package: does it import and work under Pyodide, and what does it add to the bundle?
2. **Leave it on Linux.** Whatever fails (1) stays on the Linux backend, which is the
   documented routing boundary rather than a defect.

Packages needing native extensions beyond available wasm wheels are strategy-2 by
construction; pure-Python ones are strategy-1 candidates pending the measured import test.

## Next experiment

Bundle `requests` (+`urllib3`, `charset-normalizer`) and `msal` into the qualified runtime,
prove import + a trivial call inside the real Worker child, and record bundle growth. That
converts the census into a bounded allowlist proposal.

## Result (measured 2026-11-12, spike `05439e42c`)

Both package lines qualify inside the real Worker child (Pyodide 314.0.6 / `emscripten-5.0.3-wasm32`):

| Check | Result |
|---|---|
| `import requests` | ✅ 2.34.2 |
| `import msal` | ✅ 1.39.0 |
| `msal.ConfidentialClientApplication(...)` | ✅ built (no-network OIDC stub) |
| `requests.post` to allowlisted test endpoint via gateway | ✅ HTTP 200, child never held the bearer |
| Import-time failure | none |

Bundle growth for `requests` 2.34.2, `urllib3` 2.8.0, `charset-normalizer` 3.5.2,
`msal` 1.39.0, `PyJWT` 2.15.1: **+1,996,147 B (+13.0 %)**, 472 → 607 modules,
upload 14,986 → 16,936 KiB. No existing module key changed. `cryptography` deliberately
not bundled.

### Boundaries this established

- **`requests` is genuinely usable**, not merely importable — it rode urllib3's Emscripten
  transport through the gateway bridge.
- **msal is only partly offline.** `Authority` eagerly performs OIDC tenant discovery.
  Real token acquisition needs an allowlisted egress host plus a tenant, and RSA/EC signing
  needs `cryptography` (unbundled); HS256 needs only PyJWT.
- Curated pre-bundling is therefore viable for pure-Python HTTP/auth packages at roughly
  2 MB per family, well inside the 64 MiB Worker limit with ~47 MiB headroom remaining.

### Proposed initial allowlist

Bundle as measured: `requests`, `urllib3`, `charset-normalizer`, `msal`, `PyJWT`.
Defer, and route to Linux: anything needing `cryptography`/native extensions, and anything
outside the curated set until it has its own measured import test.

## Result 2 (measured 2026-11-13): the five packages the real workspace needs

The workspace census (`WORKSPACE-NEXT-CENSUS.md`) found exactly five packages outside the
allowlist, used by 14 workflows in one solution (`bifrost-grc`). All five were imported **and
exercised** inside a real Python Worker (`backend/package-import`); a package that imports but
cannot do its job is recorded as a failure.

| Package | Version | Operation performed | Result | Unpacked |
|---|---|---|---|---|
| `pypdf` | 7.0.1 | build + serialize a one-page PDF | ✅ | 1,745 KiB |
| `beautifulsoup4` | 4.15.0 | parse HTML, read text | ✅ | 388 KiB |
| `mammoth` | 1.13.0 | import + `convert_to_html` present | ✅ | 151 KiB |
| `openrouter` | 1.3.173 | import + `OpenRouter` client present | ✅ | 7,405 KiB |
| `reportlab` | 5.1.0 | draw + serialize a PDF | ✅ | 4,732 KiB |
| `pillow` | 12.2.0 | create + PNG-encode an image | ✅ | 3,036 KiB |

Closure: `soupsieve`, `cobble`, `httpcore`, `jsonpath-python` (all pure); `pydantic` and
`httpx` already ship. `reportlab` requires `pillow`, which has a Pyodide wasm wheel — that was
the only suspected blocker, and it is not one.

### Bundle arithmetic (local `wrangler deploy --dry-run`, same harness)

| Bundle | Modules | Upload | Gzip |
|---|---|---|---|
| Current | 475 | 11,498 KiB | 2,882 KiB |
| + these five | 3,896 | **43,981 KiB** | **9,154 KiB** |
| delta | +3,421 | +32,483 KiB | +6,272 KiB |

Cold and warm, same harness:

- **Bundle startup is unaffected:** 120 ms cold with the enlarged bundle vs 102 ms for the
  baseline (both handlers import nothing).
- **First import of all five costs ~4.9 s** (4,904 ms), then 32–40 ms warm. Only workflows
  that actually import them pay it, and it is per isolate.

**Compressed size is now the binding budget**: 9.15 MiB gzip for the whole Worker. Further
packages must be treated as budgeted, not free. `openrouter` alone is 1,229 files / 7.4 MiB —
the largest single line item, and it is imported by 10 of the 14 workflows.

### Decision

All five are added to `pylock.toml` and the allowlist. That converts those 14 workflows from
"Linux by dependency" to eligible, and the real workspace reaches **317/317 at allowance 50**.

Caveats: local `workerd`, not a deployed Worker; per-package functional checks, not a
workflow-level run.

