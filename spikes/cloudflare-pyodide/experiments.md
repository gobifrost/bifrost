# Compact experiment log

Platform source baseline: `e5da6d0c1fb751d4155b1b65458bfedfc2bec112`.
Artifacts are under `results/`; generated copies retain source hashes.

| Hypothesis / change | Command | Observed result / artifact | Next |
|---|---|---|---|
| Supported toolchain can start on Windows; choose isolated new uv | `py -3 -m uv tool run --from uv uv run --python 3.14 pywrangler dev --port 8793` | wasm installation selected system Python 3.13 despite 3.14 environment; pylock requires >=3.14.2 | Bounded workaround: Linux tools container |
| Linux pywrangler avoids interpreter selection issue | `docker build -f .../Dockerfile.tools ...`; local container start | Package install worked; workerd rejected missing system CA roots | Install CA package in tools image |
| Small source closure can import and execute exact engine | `py -3 prepare.py`; `node bench.mjs` | 64 files, 912622 bytes; actual engine JSON/context/import/HTTPS/logging pass; raw `benchmark.json` | Real isolated API operations |
| CPU asyncio timeout can substitute for process supervision | `node security.mjs` | Falsified: 150 ms loop succeeds under 50 ms wait_for; I/O sleep cancels | Treat preemption as material boundary |
| Per-request context cleanup isolates workflows | `node security.mjs` | Falsified: builtins marker persists; duplicate request executes twice | Fresh execution security boundary needed |
| Real API authorization works | `node sdk-proof.mjs`; `node security.mjs` | API unavailable until debug stack ready; invalid-token test initially exceeded driver 15 s budget while SDK retried connection; corrected to 30 s after readiness diagnosis. Invalid token then returns actual BifrostAuthenticationError | No token mocking |
| Actual upstream SDK integration/table operations run unchanged | `./debug.sh up`; `node sdk-proof.mjs`; `node verify.mjs results/benchmark-sdk.json --sdk` | Integration config 42; upsert/read 42; independent REST `sdk-readback.json`; no platform source edits | Credential scope/ownership still unproved |
| Linux engine reproduces fixture behavior | `docker exec ... uv run python linux_bench.py` | Same engine functions/SDK work; sync callable TypeError and missing stdout occur on Linux too; `linux-engine-sdk.json` | Do not call engine-only timings full executor baseline |
| Sync network path is a blocker | fixed `sync_http_probe` request | Falsified for this HTTPX package/runtime: sync HTTPS returns 200; first request before hot reload was unknown fixture, repeated only after reload | Keep actual transport |
| Local runtime validates production memory ceiling | fixed `memory_probe` request | Falsified: 160 MiB bytearray succeeded; `memory.json` | Need deployed isolate measurements |
| Termination has durable recovery | `node termination.mjs` | Restart interrupts active request; `termination.json`; no claimed execution or recovery implemented | Full owner adapter required |
| Package deploys syntactically | `npx wrangler deploy --dry-run --outdir /spike/dist` | Passed; 9246.93 KiB / gzip 2432.48 KiB at recorded revision; `bundle.log` | Cloud validation |
| Existing Wrangler login can deploy | `npx wrangler@4.156.0 whoami` | Expired, refresh failed | User directed use of cf CLI |
| cf CLI has independent working authentication | `cf auth whoami` | Authenticated/token valid; cf 1.0.0-beta.22 | Migrate only companion package and try isolated deployment |
| cf can migrate current Wrangler config | `cf migrate wrangler.jsonc --dry-run` | Proposed config/package/lock only; real migration stopped on dirty-worktree safety gate | Commit tested prototype, then migrate |

Earlier no-credential benchmark and later authenticated benchmark are separate
artifacts. Local host load and SDK client setup vary; no reliable speedup or
cost conclusion follows from these samples. Production CPU/memory/cold-start,
native extension breadth, persisted fencing and recovery are open experiments.
