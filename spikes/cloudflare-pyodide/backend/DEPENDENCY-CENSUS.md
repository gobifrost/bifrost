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
