# Fully warmed API memory and S3 transport spike

This is a local hosting-efficiency spike against `6f411bb9cf3c787acf11fbea2c3faa93ef7c9923`.
It has not been merged or deployed. The API's fully warmed working set fell
from **576.7 MiB to 449.3 MiB**, a **127.4 MiB / 22.1%** reduction in the initial
pass. The subsequent ORM loading pass measured **441.4 MiB**. The 250 MiB
target has not been reached. A repeat of the initial pass under concurrent static-analysis load
settled at 468.8 MiB; these are observed workloads, not a guaranteed ceiling.

## What changed

- All eight asynchronous S3 client factories now share one process-owned
  aiobotocore metadata session. Clients and their HTTP transports still close
  at the same boundaries. The session is recreated after a fork. Endpoint,
  credentials, region, signing settings, error handling and authorization stay
  with the existing storage services.
- OpenAPI retains the encoded response instead of a large schema dictionary.
  Direct `app.openapi()` calls receive a fresh dictionary. Dynamic workflow
  registration and removal invalidate the bytes; request-specific `root_path`
  servers do not mutate the canonical response. The dynamic-route test also
  exposed an existing assignment to FastAPI's read-only `app.routes`; this now
  updates `app.router.routes`.
- Internal model-catalog records use validated slotted Pydantic dataclasses.
  Public DTOs, parsing/coercion, routing and pricing remain compatible.
- A real durable-chat completion test fills a missing verification boundary.

There are no new product dependencies, API contract changes, process roles,
network hops, worker/socket changes, or production configuration changes.

## Memory evidence

External observation samples the API cgroup and `/proc/<pid>/smaps_rollup` every
0.5 seconds. These medians cover 10–30 seconds after each workload finishes,
before heap inspection. No profiler, forced garbage collection or `malloc_trim`
runs in the headline measurements. Working set is cgroup current minus inactive
file cache; PSS is a separate process metric and must not be added to it.

| Variant | Working set MiB | Anonymous MiB | Process PSS MiB | Quiet CPU millicores |
|---|---:|---:|---:|---:|
| Original API | 576.7 | 564.3 | 592.2 | 26.5 |
| Shared S3 session only | 473.8 | 461.6 | 490.1 | 30.7 |
| Combined, isolated validation | 449.3 | 437.2 | 465.7 | 28.9 |
| Combined, concurrent CodeQL | 468.8 | 456.7 | 484.9 | 46.2 |
| Follow-up ORM loading pass | 441.4 | 429.3 | 447.3 | 36.6 |

The S3 change accounts for about **103 MiB** in the matched workload. The original
heap retained nine AioSessions/loaders versus one afterwards. OrderedDict
instances fell from 209,769 to 24,195; their shallow storage fell from about
71 MiB to 8.6 MiB. This is duplicated SDK metadata and associated retention,
not 103 MiB of application source text.

Three fresh-process catalog controls estimated a 15.2 MiB retained-PSS reduction
and parsing time of 0.355 to 0.272 seconds, with identical normalized field,
routing and validation hashes. That isolated estimate is not an additive
promise about the full API's working set. The combined API retained the current
refreshed catalog: 232 providers and 8,929 models, plus 692 routes and a
1,140,667-byte encoded OpenAPI response.

A separate instrumented lifecycle attributed retained allocations to Pydantic
(83.2 MiB), SQLAlchemy (72.0 MiB), and FastAPI (45.5 MiB), among others. These are
allocation origins, not exclusive ownership or a removable budget. The beartype
import hook appeared large because it compiles other modules; disabling it in
a fresh control saved only about 0.5 MiB. Profiler overhead inflated instrumented
RSS substantially; none of those process sizes are used above.

Two other controls were rejected:

- Clearing module-owned duplicate routers was a diagnostic upper bound, not a
  safe implementation. It returned only about 5.5 MiB after diagnostic trimming
  and would compromise app construction/reuse.
- `PYTHONMALLOC=mimalloc` completed the same checks, but settled at 474.5 MiB in
  its run. It demonstrated no memory benefit and is not part of the candidate.
  The temporary Compose setting was removed.

Clearing the three SQLAlchemy compiled-query caches recovered only about 3 MiB
after diagnostic GC/trimming. Disposing all three database pools recovered less
than 1 MiB. Their allocation attribution is not a removable 72 MiB budget;
neither cache clearing nor smaller pools is part of this candidate.

## ORM loading follow-up

The provider/profile/agent relationships previously loaded reverse graphs
automatically. Resolving one runtime model configuration issued five SQL
statements; reading one agent issued four. Explicit loading reduces each of
those warmed relationship reads to one statement, without loading sibling
profiles or other agents. Catalog freshness checks remain independent and can
add a query when the catalog's process cache expires.

The same 24 live checks passed in 146.68 seconds. The unforced quiet working set
was 441.4 MiB. This is about 7.9 MiB below the initial isolated candidate; one
run does not establish a latency improvement. This pass removes data-dependent
overfetch as well as retained loader state.

Management responses still need assignment keys, failover names and reference
counts. Their queries explicitly request those fields. A subsequent refinement
computes profile/agent counts in SQL, avoiding agent objects even when a profile
has many references; it is not yet separately included in the 441.4 MiB figure.
The HTTP merge test now creates an actual referencing agent and checks the
migrated reference, assignment keys, agent count, and provider profile count.
Profile/failover/service/manifest/bundle units (84) and four settings/failover HTTP
checks passed before the additional count-only regression cases.

Two exposing failures received specific corrections: an assignment back-reference
reloaded a profile and invalidated an explicitly loaded collection, so management
assignment queries now load only keys and suppress that unused reverse relation.
A diagnostic command also mixed committed HTTP fixtures with units that require
an empty transaction-isolated database; verification now runs those groups in
separate reset-backed commands, matching the harness's unit/E2E modes. The
query-count regression explicitly warms the independent model catalog before
measuring relationship loading, so its result does not depend on test order.

## Speed and compatibility

| Workload | Original | Candidate |
|---|---:|---:|
| Live workload, elapsed (24 baseline; 25 candidate) | 136.10 s | 136.37 s including one extra factory unit test |
| Workspace read, median / p95 | 87.9 / 170.8 ms | 20.5 / 26.5 ms |
| Temporary file read, median / p95 | 92.7 / 153.3 ms | 21.4 / 32.4 ms |
| Paired SDK rebuild handler | 5.485 s | 5.240 s |
| SDK rebuild npm install phase | 2.058 s | 2.078 s |
| SDK rebuild Vite phase | 0.526 s | 0.550 s |

File timings use real HTTP authorization, file-policy checks and SeaweedFS:
three warmups followed by 40 serial reads per location and variant. Every result
was checked and each fixture removed. This is a local measurement, not a
production latency or throughput guarantee.

The first combined run overlapped CodeQL and took 176.18 seconds; an isolated
run returned to 136.37 seconds. App-build timing also varies independently:
temporary phase tracing measured original-code npm installs of 10.179 and
2.058 seconds in consecutive builds. The paired candidate's SDK rebuild was
slightly faster overall with nearly identical npm/Vite time. The temporary
phase instrumentation was removed. Individual single-run job timings must not
be described as universally faster.

All **23 PlatformJob types** succeeded through real local runner processes.
Other exercised paths include durable chat and persisted usage, agent execution
and summary, MCP discovery/tools/revocation, workflow SDK organizations,
workflow OpenAPI, HTTP endpoint execution and remote-runner packages. The local
provider fixtures avoid paid AI calls; model-catalog refresh uses the public
models.dev endpoint, whose content can change.

Focused unit coverage: 145 tests passed, including OpenAPI cache/dynamic route,
catalog/pricing/capabilities, storage factory/health and CLI/DTO tripwires. A
separate 74-test storage set passed. Final Python quality checks and a freshly
rebuilt changed-file CodeQL analysis passed with zero findings. The exact
candidate's process-owned factory also passed in the 25-check live validation.
No full backend, browser, or frontend suite was run for this API-only spike.

Full container cold start, registry pull time, production load, sustained
concurrency and hosting cost were not benchmarked in this pass. Isolated import
and catalog parse timings do not substitute for container readiness.

## Replacing the full AWS client

There is no boto3 dependency. Current storage uses aiobotocore/botocore for
roughly 101 call sites across 19 modules, with eight async client factories and
a synchronous module/requirements-cache reader. The AWS CLI is a separate
subprocess dependency for Git repository sync; removing Python SDK imports
alone would not remove it from the image.

Fresh network-disabled production-image processes showed these additional PSS
costs relative to their own pre-import baseline:

| Client | After import MiB | After client construction MiB |
|---|---:|---:|
| aiobotocore 3.5.0 | 45.1 | 57.5 |
| obstore 0.12.0 | 7.7 | 9.5 |
| miniopy-async 1.23.5 | 19.3 | 19.4 |
| aiohttp-s3-client 1.2.0 | 17.1 | 17.2 |

These are dependency probes, not measured additional savings in the fully warmed
API. Shared imports, lifetime, allocator retention and real request activity
change the eventual saving.

No inspected client is a drop-in replacement. We need source-ETag guarded
server-side copy, arbitrary recovery metadata, ranged multipart copy over
5 GiB, destination `If-None-Match: *` on completion, bounded streaming, and
content-type-bound upload URLs. Obstore exposes no source-condition or metadata
parameters on its public copy API and no signed-header parameters on presigning.
Miniopy lacks the required conditional multipart completion and PUT header
binding. Aiohttp-s3-client exposes lower-level requests but would still need our
multipart-copy adapter. A mixed SDK path that eventually loads both clients in
the API would forfeit much of the proposed saving.

A separate prototype uses HTTPX and Amazon's standalone `aws-sdk-signers 0.3.1`:
its signer added about 3.9 MiB above HTTPX in that process. Two fixed-date vectors
matched botocore's canonical request and Authorization, including Unicode,
literal percent escapes, duplicate slashes and query ordering. This dependency
is alpha and has no public query-presigning API. It remains an experimental
transport, not a production replacement. A manual Docker-harness probe passed
against SeaweedFS in 1.20 seconds: signed bucket/object HEAD, Unicode/percent-key
PUT and streamed GET, paginated listing, metadata-preserving copy, multipart
upload/completion/abort and ranged multipart server-copy/completion. The copied
bytes were verified, including a 5 MiB result hashed in 64 KiB chunks. It sends
the required precondition headers; it does not establish AWS race semantics or
query-presigning support.

A complete replacement must cover the existing operation contract in one shared
adapter, including signed upload/download URLs, sync reads, retry/error behavior,
checksums, streaming cancellation/close, paginated list/delimiter semantics, and
HTTP-200 embedded copy/completion errors. Verify the conditional/race behavior
against actual AWS S3 as well as SeaweedFS before migration. Do not delete these
protections to improve an idle-memory number.

Official reference material: [obstore copy](https://developmentseed.org/obstore/latest/api/copy/),
[obstore API](https://developmentseed.org/obstore/),
[miniopy source](https://github.com/hlf20010508/miniopy-async),
[aiohttp-s3-client](https://github.com/aiokitchen/aiohttp-s3-client), and
[Amazon standalone signer](https://pypi.org/project/aws-sdk-signers/).

## Reproduction and artifacts

All tests use the isolated worktree and the existing Docker test harness:
`BIFROST_TEST_IMAGE_TAG=bifrost-test-b60bcc82 BIFROST_SKIP_BUILD=1 ./test.sh ...`.
The baseline image supplies matching dependencies; current worktree source is
mounted into each service. Baseline comparisons restore the original eight
storage factories, then restore the candidate before final checks. No production
credentials or workflows are used.

The matched workload arguments are:

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

The final isolated run additionally selects
`tests/unit/core/test_s3_session.py`. Remaining scheduler-job types and endpoint
execution use `tests/e2e/platform/test_scheduler_fixture_workloads.py` and
`tests/e2e/api/test_endpoint_execution.py::TestWorkflowEndpointExecution::test_execute_endpoint_via_post`.
Quality uses `./test.sh quality api`; CodeQL uses
`scripts/codeql-local.sh --rebuild --changed-only --fail-on-alerts`.

Host-side artifacts are retained under
`/home/jack/.codex/reports/bifrost-api-hosting-2026-10-25/`, with raw working
captures under `/tmp/bifrost-hosting-profile-20261025/`. JUnit, per-request
samples, job timings, allocator/cache controls, native allocation attribution,
standalone signer vectors and replacement-library inventory are retained.

## Continuing work

The full API sweep remains active. Next investigations are retained ORM/query
and schema/tool graphs, then completing a viable narrow S3 adapter and measuring
its fully warmed footprint. The current 449 MiB result is an intermediate
baseline, not completion of the roughly 250 MiB target. Vite/npm/SDK packaging
memory is queued after the API sweep reaches a better idle baseline.
