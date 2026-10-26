# API hosting memory spike

These measurements began as a **local spike**, based on
`6f411bb9cf3c787acf11fbea2c3faa93ef7c9923`. Promotion validation is recorded below;
[PR #1070](https://github.com/gobifrost/bifrost/pull/1070) tracks delivery.

The matched workflow/job workload settled at **372.2 MiB**, versus **576.7 MiB**
originally: **204.4 MiB / 35.5% less memory**. Explicitly warming PDF, Word,
spreadsheet, previews, and SVG rendering afterwards raised it to **392.6 MiB**.
The **250 MiB target has not been achieved**. These are observed local workloads,
not a production limit or proof that every feature and concurrency level fits.

The same 24 live checks took **132.66 seconds versus 136.10 seconds** originally.
Cached-image launch-to-ready medians were **8.186 versus 9.932 seconds**, with
three cold containers per variant. SDK rebuild timing needs the separate phase
comparison below; the overall result does not mean every operation became faster.

## Where memory was saved

| Stage | Quiet working set MiB |
|---|---:|
| Original API | 576.7 |
| Share the eight S3 metadata sessions | 473.8 |
| Initial OpenAPI/catalog changes, isolated run | 449.3 |
| Explicit ORM loading | 441.4 |
| Count-only ORM queries and bundled MCP SDK | 426.9 |
| Narrow S3 transport and compact catalog | 396.0 |
| Defer format renderers and remove unused NumPy dependency | **372.2** |
| Same final process after actual generation in all four format families | **392.6** |

The largest single demonstrated saving was **about 103 MiB of duplicated AWS
metadata**. The old heap retained nine AioSessions/loaders; sharing them reduced
OrderedDict instances from 209,769 to 24,195 and their shallow storage from about
71 MiB to 8.6 MiB. Replacing the full AWS client later provided a smaller additional
saving. An isolated client probe's 57.5 MiB increment was never a promise of that
much additional whole-API saving.

The separate `fastmcp` dependency was replaced with **FastMCP bundled inside the
already-required official `mcp` SDK**. The matched warmed reduction at that stage
was about **15 MiB**, rather than its larger isolated import attribution.
OAuth, caller/agent scoping, discovery, native tools, dynamic workflow tools,
structured results, and stateless HTTP remain. The SDK is pinned to `1.27.0`
because the adapter uses its registry/session hooks; upgrades need the protocol
and refresh checks.

Other changes:

- OpenAPI retains encoded bytes instead of the full schema dictionary. Direct
  `app.openapi()` calls get a fresh dictionary; dynamic workflow route changes
  invalidate the cached response. Request-specific root paths do not mutate it.
- Catalog records first moved to validated slotted dataclasses. The final version
  retains normalized JSON bytes and a **catalog-wide 128-record LRU** of typed
  models. It does not retain raw upstream JSON alongside a full model graph.
- Runtime AI-profile resolution and agent loading each use one warmed SQL query
  instead of five and four respectively. Management queries request their needed
  relations explicitly, and SQL counts replace loading every referring agent.
  An independent expired catalog freshness check can still add a query.
- Metadata checks and basic artifact validation no longer import Word, XLSX, PDF,
  and SVG renderers. Each format imports its renderer when used.
- The unused Python `pgvector` dependency was removed. Bifrost already uses its
  existing local vector column adapter. PostgreSQL's vector extension remains;
  the Python removal also removes its sole hard dependency, NumPy. OpenPyXL now
  works without opportunistically loading NumPy, including after XLSX generation.

The lock removes **26 packages** and adds **`awscrt==0.38.0`**. Other locked versions
remain unchanged; `mcp` was pinned to its existing locked version. OpenAI and
Anthropic remain because the supported Pydantic AI provider adapters use them.
The AWS CLI remains a separate subprocess dependency for repository sync.

## Architecture and maintenance cost

This API pass changes **no process roles, worker/socket architecture, network
hops, public DTOs, or production configuration**. Existing authorization,
file-policy, runner, job, notification, and persistence paths remain.

The S3 replacement is one finite HTTPX adapter using Amazon's CRT signer and
checksums. It implements the existing operations rather than loading a general
AWS service-model catalog. It preserves guarded server-copy, ranged multipart
copy, conditional completion, metadata, pagination, signed URLs, bounded reads,
retry/error handling, and a refresh-capable PID-owned default credential provider.
Copy/part-copy/completion HTTP-200 XML errors enter the retry/error path. Default
regional redirects, checksums, cancellation/EOF closure, and repeated pagination
tokens have focused coverage. It does not fall back to the old SDK.

That adapter is the largest maintenance tradeoff in the spike: roughly 750 lines
of owned protocol code. Sharing the original SDK session captured most of the
AWS saving with substantially less code. The additional reduction from replacing
the SDK must be weighed against owning and validating that protocol surface.
**Actual AWS S3 conditional races and default-credential deployment environments
have not been live-tested**. DigitalOcean Spaces acceptance for the deployed
provider is recorded below; SeaweedFS and signing/error fixtures alone are not substitutes.

Review found and corrected these compatibility gaps before accepting the local
candidate:

- SDK duplicate tool registration kept the old callable. Refresh now constructs
  a fresh SDK tool and replaces its callable/schema together. A reused name must
  dispatch the current workflow UUID, including after replacement.
- Result helpers now preserve the former normalized content-block interface,
  including JSON display content for older MCP clients.
- File-handle uploads initially buffered the entire retained Solution archive.
  That caller now uses the existing shared chunked uploader; the narrow transport
  rejects file-like bodies. A guarded-read regression enforces bounded reads.
- The replacement HTTP mount initially omitted the OAuth resource-metadata URL
  from its authentication challenge and accepted GET on the stateless endpoint.
  It now preserves the old challenge and POST/DELETE method contract. Both
  exposing live tests failed before correction, then the full protocol/discovery
  and dynamic refresh selection passed.

## Speed, startup, and tradeoffs

| Measurement | Original | Final measured workload |
|---|---:|---:|
| Matched 24 live checks | 136.10 s | 132.66 s |
| Agent claim to started | 88 ms | 41 ms |
| Agent fixture execution | 87 ms | 58 ms |
| Enqueue through generated summary | 376 ms | 298 ms |
| Cached-image launch to ready, median of 3 | 9.932 s | 8.186 s |
| Docker StartedAt to ready, median of 3 | 9.305 s | 7.734 s |
| App deploy handler | 7.974 s | 7.944 s |
| SDK rebuild handler | 5.575 s | 7.298 s |

The dispatch numbers describe small deterministic fixtures, not paid model
latency or an arbitrary customer workflow. Job timings are individual handler
observations; they are not a throughput test or p95 guarantee.

The apparent SDK rebuild slowdown received a controlled follow-up using the
unchanged original API in its own worktree and the final candidate, with the
same cached Node toolchain and this same real build/Git lifecycle. Temporary
phase instrumentation showed:

| Follow-up SDK rebuild phase | Original code | Candidate |
|---|---:|---:|
| Whole SDK job | 8.470 s | 7.837 s |
| npm install | 4.815 s | 4.439 s |
| Vite | 0.515 s | 0.558 s |
| Materialize source | 0.008 s | 0.007 s |

The slowdown did not reproduce against unchanged original code in that pair.
Earlier original-code npm installs also varied from 10.179 to 2.058 seconds;
a previous paired SDK job was 5.485 versus 5.240 seconds. These observations
support install/build variability rather than a demonstrated API regression.
The slower 7.298-second final workload observation is retained above. This is
not a claim that SDK rebuild latency is solved or that every build is faster;
build memory/install behavior remains the next investigation.

The compact catalog has a measurable cost: a cold enumeration of the largest
provider's 597 models takes about **11–13 ms versus 0.02 ms** for the eager model
mapping. A 128-record cache cannot hold that entire list, so repeated full
enumerations can pay it again. Individual cached lookups remained below a
microsecond in the isolated probe; construction adds roughly 0.1–0.14 seconds.
These are internal controls, not HTTP latency measurements. The compact catalog
is a memory/CPU tradeoff, not a claim of universal speed improvement.

Format imports also move a small amount of work to first use. In the already
job-warmed API, actual generation plus Office preview took **86 ms PDF,
61 ms Word, 71 ms XLSX, and 148 ms SVG logo**. These are in-process operation
times, excluding HTTP, authorization, and storage. A separate fresh-process
probe measured PDF 227 ms and SVG 185 ms. The 392.6 MiB wider warm result includes
these actual operations; NumPy remained absent.

Cold starts use cached local test images and source archives without source
bytecode caches, polling `/health/ready` every 100 ms. They exclude image download,
registry, cluster scheduling, and production traffic. The test images reproduce
the locked dependency closure; they are not fresh production Docker builds or
image-size measurements.

## What still occupies memory

A configured, network-disabled fresh-process control measured these **cumulative
PSS** values, collecting garbage after each stage:

| Imported/initialized through | Cumulative PSS MiB |
|---|---:|
| Python | 6.6 |
| FastAPI and its dependencies | 35.0 |
| ORM models | 69.3 |
| API routes, MCP, and their transitive imports | 286.2 |
| Bundled catalog | 306.3 |
| OpenAPI generated | 331.1 |

This is a separate diagnostic, not the unforced headline or a breakdown into
exclusive package budgets. Imports construct Python classes, ORM mappings,
validation schemas, dependency graphs, tool wrappers, and caches. They load
transitive packages too. Their footprint is substantially larger than source
file bytes. Allocator retention can keep process memory above the live object
size even after temporary parsing/serialization data becomes unreachable.

An earlier instrumented lifecycle attributed allocations to Pydantic (83.2 MiB),
SQLAlchemy (72.0 MiB), and FastAPI (45.5 MiB). Those were **allocation origins in
an intermediate candidate**, not exclusive current ownership or independently
removable budgets. For example, actual schema-validator construction was about
12 MiB, not the entire Pydantic label. That lifecycle capture did not force GC
before ending; uncollected unreachable objects can appear retained. Profiler
process sizes are not used as headline API memory.

Rejected controls include removing duplicate router containers (~5.5 MiB after
diagnostic trimming), disabling beartype's import hook (~0.5 MiB), clearing SQL
compiled caches (~3 MiB), disposing three database pools (<1 MiB), Pydantic
schema deferral (small import saving but worse OpenAPI footprint), and mimalloc
(474.5 MiB versus the initial isolated candidate's 449.3 MiB). No periodic forced
GC, cache-clearing loop, allocator switch, or trim workaround was added.

### Follow-up: breaking down the application import increment

A follow-up on the final source (`603e1d72d`, documentation-only after the last
runtime change) traced direct imports from `src.main` and individual
`FastAPI.include_router` calls. Each checkpoint collected garbage and read
`/proc/self/smaps_rollup` in a fresh, network-disabled container using the final
no-NumPy image and read-only source mount. No lifespan, database connections,
production credentials, or real requests were used.

This reproduced **222.1 MiB added after the ORM checkpoint**, compared with
216.9 MiB in the earlier diagnostic. These are approximate import-stage PSS
observations, not the unforced workload measurement or a new production memory
result. Checkpointing and PSS sharing make small differences between these
diagnostics possible; no runtime code changed during this follow-up.

| First-use import or initialization group | Added PSS MiB |
|---|---:|
| AI/chat runtime and its dependencies | 43.5 |
| MCP plus shared agent/file services | 27.2 |
| Registering app route/dependency copies | 26.0 |
| Core Redis, authentication, and security | 21.6 |
| Git sync and source editing | 18.1 |
| Agent-run contracts, SDK, and HTTP helpers | 15.4 |
| Solutions and remote-worker control | 14.6 |
| Remaining endpoint models and services | 55.7 |
| **Total added after ORM** | **222.1** |

These groups partition this one import sequence. They are **not exclusive
library ownership or independently removable budgets**: for example, agents
imports MCP and file services before later endpoints need them. The remaining
55.7 MiB is spread across endpoints; its largest individual checkpoints were
permissions/access catalog (5.5 MiB), passkeys (4.6), app-code files (3.8), events
(3.3), files (3.0), auth (2.6), and forms (2.4). There was no additional single
large import hidden in that remainder.

Independent controls preloaded the actual libraries before importing application
routes, then reversed their order. They converged at **298.0 / 298.2 MiB PSS**
through app construction. Import order changed which group paid shared costs:
preloading the agent harness first also loaded Pydantic AI and HTTP support.
Loading the API/SDK contract modules used by this app accounted for another
**26.8–28.1 MiB** in those controls. Those contract bytes are already represented
in the table and must not be added again.

The source edges explain the largest groups:

- `routers/chat.py` imports `services/agent_executor.py`, which imports Pydantic
  AI, runtime composition, compaction, code mode, subagents, tool execution, and
  MCP client support. Those implement supported chat/agent behavior.
- `routers/agents.py` imports `routers/tools.py` and shared file services, loading
  the official MCP SDK, tool definitions, and the S3 transport. The same modules
  serve later MCP/file routes; there is one cached module graph.
- `routers/github.py` imports the Git operation definition and
  `services/github_sync.py`; the latter imports GitPython. The decorator-property
  service uses LibCST to preserve source formatting while editing decorators.
- `worker/remote/host.py` imports `worker/pool_main.py`, bringing in scheduler
  support and the Alembic-backed schema gate. The API-owned control host also
  uses that schema gate at startup, so merely moving this import would not remove
  its warmed footprint.
- Passkeys use the real WebAuthn verifier; authentication uses JWT/password
  cryptography. These are feature dependencies, not unused infrastructure.

No additional runtime change was made. Deferring GitPython, LibCST, or the agent
runtime could reduce startup memory until first use, but that is not a
fully-warmed reduction and moves import work onto those requests. Removing them
outright loses supported behavior. The prior duplicate-router removal control
saved only about 5.5 MiB after diagnostic trimming and broke app-factory reuse;
the 26 MiB registration stage is not a promise of 26 MiB removable memory.
This follow-up found no second demonstrated duplication comparable to the AWS
metadata and no new merge blocker. Real-AWS acceptance and deployed-workflow
dependency checks below remain unresolved promotion requirements.

Raw evidence and reproducible probe sources are retained under the report's
artifact directory: `route-breakdown-forward.jsonl`,
`route-breakdown-summary.json`, `route-components-full.jsonl`,
`route-components-reverse.jsonl`, `api-import-stages-confirmed.jsonl`,
`probe_route_breakdown.py`, and `probe_route_components_full.py`. This was
profiling and documentation only; functional suites were not rerun.

## Compatibility evidence

All **23 PlatformJob types** succeeded through real local runner processes:
19 types in the matched workload and four remaining scheduler types in the
separate final contract selection. Workflows, durable chat/replay/usage, agent
execution/summary, MCP discovery/calls/revocation, HTTP endpoint execution,
workflow SDK organization CRUD, dynamic OpenAPI, and remote runner packages
were exercised. Provider fixtures avoid paid AI calls; model-catalog refresh
uses a mutable public source.

Final focused checks include:

- 141 catalog, pricing, capabilities, refresh, MCP/result, and access-list tests.
- 73 checks with the final no-NumPy image: live S3 multipart/copy/streaming/listing/
  presign, actual presigned upload and download headers, MCP, AI settings/failover,
  scheduler jobs, endpoint execution, transport/core tests, bounded archive
  uploads, document/preview/logo generation, vector serialization/operators,
  and import hygiene.
- 24 matched live checks with the same final dependency closure.
- 10 protocol/discovery/dynamic-refresh checks after the final MCP HTTP parity fix.
- Final `./test.sh quality api`: Pyright and Ruff passed.

The real S3-wire test checks Unicode and literal-percent keys, source-guarded
copy, metadata, direct and server-side multipart upload, signed PUT/GET,
streamed hashes, page-size-one listing, abort, and cleanup against SeaweedFS.
A separate signing oracle covers canonical path/query/header cases. Final heap
inspection confirms no loaded `fastmcp`, `botocore`, `aiobotocore`, `pgvector`, or
`numpy` modules, even after XLSX generation.

No full backend, frontend, browser, sustained-load, production, or real-AWS suite
was run. An earlier changed-file CodeQL run passed before the SDK replacements;
it is not represented as a final-candidate scan. Known exposing failures received
specific source/harness corrections; there are no added retries, skips, or xfails
in tests.

The memory/startup captures used API source `2ba95ea1e`; the subsequent MCP HTTP
parity patch `1bf9e165e` was checked separately. That patch adds the auth discovery
URL and removes GET from route registration; its footprint was not separately
remeasured. All temporary profiling/build instrumentation is removed afterwards.

## Per-job observations

These are one original and one final observation per handler, in seconds. Four
scheduler-only types have separate success evidence rather than matched timings.

| Job | Original | Final |
|---|---:|---:|
| sdk.video_generation | 4.084 | 3.522 |
| chat.video_generation | 3.955 | 3.525 |
| embedding.reindex | 2.945 | 2.701 |
| model_catalog.refresh | 1.755 | 1.819 |
| artifact.retention_cleanup | 1.858 | 1.799 |
| audit.archive | 2.408 | 1.763 |
| audit.query | 1.883 | 1.803 |
| run.retention | 1.444 | 1.370 |
| files.move | 2.786 | 1.975 |
| workspace.reimport | 3.518 | 3.146 |
| workspace.bundle_import | 3.884 | 2.559 |
| agent.summary_backfill | 1.527 | 1.496 |
| application.deploy | 7.974 | 7.944 |
| application.sdk_update | 5.575 | 7.298 |
| application.publish | 3.142 | 2.337 |
| solution.deploy | 3.364 | 2.737 |
| solution.export | 2.668 | 2.399 |
| solution.git_sync | 3.084 | 2.476 |
| workspace.git | 3.257 | 3.038 |

## Reproduction and artifacts

Use this worktree's normal Docker harness with
`BIFROST_TEST_IMAGE_TAG=api-memory-no-numpy BIFROST_SKIP_BUILD=1 ./test.sh ...`.
The matched selectors are:

```text
tests/e2e/platform/test_chat_runtime_boundary.py
tests/e2e/api-integration/test_mcp_gateway.py::TestMCPAgentGateway
tests/e2e/platform/test_agent_runtime_boundary.py
tests/e2e/api/test_endpoint_execution.py::TestWorkflowEndpointOpenAPI
tests/e2e/platform/test_sdk_organizations_local.py
tests/e2e/platform/test_thin_platform_jobs_providers.py
tests/e2e/platform/test_thin_platform_jobs_misc.py
tests/e2e/platform/test_thin_platform_jobs_build.py
tests/e2e/platform/test_remote_runner_packages.py
-q -s --durations=0
```

Full exact commands, image tags, source/lock hashes, JUnit, job timings, cold-start
samples, phase logs, heap counts, native ownership, and negative controls are
retained under `/home/jack/.codex/reports/bifrost-api-hosting-2026-10-25/` and
`/tmp/bifrost-hosting-profile-20261025/`. The final logs are named
`full-warm-final-sweep`, `final-complete-contracts`, `final-mcp-discovery-parity`,
and `final-mcp-parity-quality`. No production credentials were used.

External observation samples cgroup working set and process PSS every 0.5 seconds.
Headline medians use 10–30 seconds of quiet after completion, before heap probes,
without forced GC or trimming. Working set subtracts inactive file cache; PSS is
separate and must not be added to it. The final sample had 39 observations and
27.6 millicores of quiet CPU, versus 26.5 originally. Each main stage is a local
observation, not a statistical performance guarantee. One earlier S3 run lost its
observer when the host filled its disk; its later 405 MiB reading is not used as
a matched-window result. The final reset wrapper reclaims only recorded unused anonymous volumes from
this worktree; the reports and raw measurements are retained.

## Remaining decisions

The local sweep established a substantially lower baseline and concrete tradeoffs.
It did not demonstrate a 250 MiB fully warmed API. Further large reductions need
more evidence about feature/process boundaries rather than subtracting library
attribution labels. Before promotion, the owned S3 transport needs deployment-provider
acceptance and credential checks, an audit of workflow requirements for packages
previously available only transitively, and normal PR/release verification.
The DigitalOcean deployment acceptance is recorded in the follow-up below.
Production load, sustained concurrency, and image-pull startup remain unmeasured.
Vite/build memory is still the next investigation after this API pass.

## Promotion acceptance follow-up — 26 October

The deployment uses DigitalOcean Spaces with explicit configured credentials.
The operator selected live acceptance against that provider for this promotion.
Both the candidate transport and the existing aiobotocore client passed the same
live object, metadata, guarded copy, ordinary/ranged multipart, streaming-read,
pagination, signed-browser upload/download, abort, and deletion control. Every
test used a unique `_tmp/api-memory-acceptance/` prefix and removed its own objects
and active multipart uploads. The temporary test pod was deleted afterwards.

The live test exposed a real candidate defect: HTTPX's default `Connection`
header was signed, then removed by the Spaces ingress, causing signature
verification to fail. Signing now excludes hop-by-hop headers while retaining
Host, date, payload hash, and Content-MD5. A focused regression failed before
the repair and passed afterwards. Async and sync regional recovery also preserve
the `400 AuthorizationHeaderMalformed` response with a bucket-region hint.

Ranged multipart copy from a key containing a literal encoded slash failed with
`NoSuchKey` on both the old SDK and the candidate. This is an existing provider
edge case; the live multipart control copies from an ordinary key while ordinary
copy, read, and listing still check the unusual key. The local container-store
control retains its original encoded-key multipart coverage. No alternate storage
path or provider-specific fallback was added.

Review also found and repaired agent manifest serialization after changing ORM
relationship defaults. Workspace export, Solution capture, and workspace-bundle
planning explicitly load the selected agent model profile. Regression fixtures
query and expunge ORM objects before serializing them. A null profile foreign key
does not read the relationship; a configured but unloaded profile still fails.

A read-only audit scanned 2,750 indexed workspace/Solution source and dependency
files for the import roots of all 26 removed distributions. There were no matches
or Python parse errors, and the deployed requirements file declared none of those
packages. Dynamic imports and transitive dependencies of arbitrary external
packages are not established by this static audit.

The frozen runtime revision passed 10,266 backend unit tests. Changed integration
files passed 20 tests after updating the requirements-helper fixture to seed via
the shared workspace writer rather than the worker's now-read-only sync client.
An initial in-flight source edit invalidated source-introspection checks; the
subsequent frozen run passed. All exposing failures and intermediate logs are
retained under `/tmp/bifrost-api-promotion-20261026/`.

AWS-specific live credential-chain/conditional-race acceptance remains unmeasured
for AWS deployments. Production rollout and before/after workflow results are
recorded separately when completed; this acceptance section is not a deployment
claim.

The first merge-queue run exposed three file-move recovery regressions. The
container store encodes spaces as `+` in URL-encoded listing responses; the
candidate used `unquote` where the previous SDK used `unquote_plus`. Renamed
copies were therefore absent from recovery's candidate set, producing incorrect
404s or selecting a different numbered path before the existing-copy permission
check. The transport now preserves the old decoding contract, including literal
`%2B` plus signs. Recovery and authorization logic are unchanged. A parser test
reproduced the defect before repair, and the real-storage wire test now checks
a space/parentheses/literal-plus key with the rename-candidate prefix.
