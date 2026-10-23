# CodeMode on the consolidated executors

Related work: [#1015](https://github.com/gobifrost/bifrost/issues/1015).
Base: `origin/main` at `735063786` (PostgreSQL-only executor claims).

## Question and scope

Can the owning agent compose tools in code, keep intermediate results out of
model context, and run on ordinary Bifrost executors without requiring operators
to provide a separate sandbox service or privileged Linux namespace setup?

This branch supplies an opt-in runtime capability, a credential-free synthetic
probe, focused regression tests, and one real workflow dispatch. It does not
enable CodeMode in production chat or autonomous configuration. Automatic mode
alone retains the existing result cap. A follow-up frozen production-derived
comparison measures actual provider usage; its limitations and results are below.

## Execution and ownership

The existing agent remains responsible for planning, semantic decisions,
authorization scope, evidence, terminal output, and its shared usage ledger.
`pydantic_ai_harness.CodeMode` executes selected discovered tools in a restricted
Python program. Those functions still dispatch through `BifrostToolset` and the
owning executor callback. Composition revalidates the original caller, approved
agent graph path, live schema, and exact bound workflow before the effect.

| Stage | Execution | Contract |
| --- | --- | --- |
| Plan and interpret evidence | Existing owning model and loop | Existing instructions, scope and budget |
| Load specialist instructions and find tools | Existing capabilities and ToolSearch | Existing approved graph; undiscovered tools stay unavailable |
| Fetch and transform intermediate data | Monty code and existing tool dispatch | Complete tool values; explicit selected tools; bounded code resources |
| Act and verify | Existing workflow, system, knowledge and MCP executors | Existing authorization, ordering and receipts |
| Return to model | CodeMode result boundary | Existing model result cap; execution evidence remains available separately |

No workflow-engine, dispatch table, queue, claim loop, status endpoint, or
PlatformJob definition is added. The test opts into capabilities on the current
autonomous loop and runs its bound workflow through the unchanged execution
service and PostgreSQL-backed worker. This is not the trusted inline-Python
`run_code` endpoint and does not grant the sandbox SDK access or credentials.

## Sandboxing and deployment

The dependency addition is `pydantic-monty==1.3.0`, which brings matching client
and runtime distributions. The existing Harness 0.55.0 and Pydantic AI 2.55.0
pins remain unchanged. The hashed lock is generated with the repository's
Docker/pip-tools command. Packages include Linux AMD64 and ARM64 wheels.

Monty implements a restricted Python interpreter in a local subprocess. Its
language boundary exposes no ambient filesystem, environment, sockets, shell,
or general native-library imports. The spike supplies no mounts or OS handler.
It exposes only the tools explicitly selected from the existing agent runtime.
Bubblewrap instead isolates ordinary programs with configured Linux namespaces
and filesystem views. Monty requires its shipped binary and permission to start
an ordinary child process; it does not require Bubblewrap, a Docker socket,
privileged containers, additional capabilities, or a remote sandbox service.

The interpreter is still part of the trusted computing base. Subprocess crash
isolation is not an OS security sandbox, and authorized host tool callbacks
remain responsible for their own security. It is appropriate for tool
orchestration and bounded calculations, not arbitrary CPython applications,
package installation, shell commands, or customer scripts needing native
libraries.

The experiment sets a 10-second code-execution budget, 128 MiB sandbox heap,
128 suspensions, and at most 16 nested tool calls per snippet. Host tool waiting
and process/cgroup overhead are separate from the interpreter heap. Existing
sequential tool flags remain intact. Eager and speculative execution are off.
The capability permits three native correction attempts: the model receives
feedback and supplies a revised snippet within its existing run budget. It does
not automatically replay a snippet or its completed effects. Explicit callers
may select a smaller allowance. Missing sandbox dependencies must produce
an explicit failure, never execution through unrestricted Python.

## Result and cancellation boundaries

An invocation-local context marks only nested code execution. Inside it,
`BifrostToolset` preserves complete structured results for code to inspect.
Ordinary direct calls keep the current 32,000-character model result policy.
On exit, the wrapper bounds the public `run_code` return value while preserving
the framework's nested-call metadata. Existing tool lifecycle events continue
to carry full execution evidence outside the model-visible result.

Pydantic's nested-call machinery catches ordinary exceptions, including
`AgentRunCancelled`. The spike latches cancellation in the invocation scope,
blocks subsequent nested effects even if generated code catches the exception,
and propagates cancellation outside the native code handler. There is no
automatic replay of a partially executed snippet.

## Evidence and limitations

The restricted-container probe passed on Linux AMD64 with Harness 0.55.0,
Pydantic AI 2.55.0, and Monty 1.3.0. It ran as UID/GID 1000 with no network,
all Linux capabilities dropped, no-new-privileges, a read-only root filesystem,
a 64 MiB temporary filesystem, a 512 MiB container memory limit, and 64 PIDs.
There was no Docker socket, host sandbox service, Bubblewrap, or policy
relaxation.

| Observation | Direct calls | CodeMode |
| --- | ---: | ---: |
| Tool calls | 3 | 3 |
| Model requests | 2 | 2 |
| Model-visible tool-result content | 72,240 bytes | 25 bytes |
| Complete-data aggregate | Not evaluated by the synthetic model | Correct: 96,003 characters |
| Observed child processes during tools | 0 | 1 |
| Sampled child RSS | 0 | 31,707,136 bytes (about 30.2 MiB) |
| Children remaining after the run | 0 | 0 |
| Local elapsed time in this sample | 16.98 ms | 42.75 ms |

The direct baseline already batches the three independent calls into one
model response, so CodeMode makes no request-count improvement here. It avoids
showing three truncated payloads to the model and computes over the full
96,003-character input instead. The byte comparison covers tool-result content
only, excluding prompts, tool signatures, generated program text, and app-only
metadata; it is not a complete request comparison. RSS was sampled during host
tool callbacks, not measured as a container peak or a concurrency benchmark.

Five native CodeMode unit tests passed, covering the full intermediate result,
bounded final result and subsequent direct call, schema and dispatch denial,
nested-call budget, cancellation, and refusal to read an existing unexposed
host file. The combined known-consumer and database run passed 65 tests with
zero failures, errors, or skips. Python type checking and Ruff passed.

The new database happy path starts exactly one workflow job. Setup took 3.83 s,
the test call 1.83 s, and teardown 0.16 s (5.83 s summed). Fixture source/agent
registration dominates the case; only one dispatch/result crosses the worker
boundary. The root agent loop is invoked directly with test-only capability
injection; this does not prove root agent queue admission, worker-claim cleanup,
or crash-resume of a CodeMode run. Existing composition tests also passed for
revoked edges/bindings and foreign-organization denial.

Exact checks from this worktree:

```bash
BIFROST_SKIP_BUILD=1 ./test.sh tests/unit/services/test_agent_code_mode.py -v
BIFROST_SKIP_BUILD=1 ./test.sh tests/unit/services/test_agent_runtime.py tests/unit/services/agent_runtime/test_composition.py tests/unit/services/test_agent_composition_stream.py tests/unit/services/test_agent_runtime_authorization.py tests/e2e/api/test_agent_code_mode_spike.py tests/e2e/api/test_agent_capability_composition.py -v --durations=0
BIFROST_SKIP_BUILD=1 ./test.sh quality api
git diff --check
```

The initial synthetic validation did not run the full backend or frontend
suites, a real-provider comparison, or a production deployment. Initial failures
were resolved: explicitly
loading the built dependency image fixed the missing-package harness state;
generated code now follows the existing sequential tools' synchronous Python
signatures; invalid-input tests use compile-valid strings to exercise the live
JSON schema validator; and the cancellation fixture discovers its exact tools
before calling them. No retries or skips were added to hide a failure.

Reproduce the local image and credential-free probe:

```bash
docker build --load -f api/Dockerfile.dev -t bifrost-code-mode-spike:latest .
docker run --rm --network none --user 1000:1000 --cap-drop ALL \
  --security-opt no-new-privileges --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m --memory 512m --pids-limit 64 \
  -v "$PWD/api/src:/app/src:ro" -v "$PWD/api/shared:/app/shared:ro" \
  -v "$PWD/api/scripts:/app/scripts:ro" --entrypoint python \
  bifrost-code-mode-spike:latest /app/scripts/agent_code_mode_spike.py
```

The measured run pinned image
`sha256:3f058dffe642a9a744097510cda357049f840e23f206e86825f8813446801db1`.
The source mounts above are for this development probe; production uses the
image's baked source. They are not mounts exposed to sandboxed Python.

The synthetic probe uses `FunctionModel`: it measures correctness, request
count, model-visible bytes, and local execution time without a provider API
key. Those byte counts are not provider token counts or a cost forecast.
Its prescribed program does not establish how often a live model will choose
good programs, preserve necessary evidence, or recover from malformed code.

Production rollout needs broader fixture coverage and actual cost per
successful task beyond the production-derived sample below. It also needs deliberate
configuration and per-run policy pinning, chat-history continuation, code-run
presentation, and worker-density/cancellation measurements at realistic
concurrency. This spike introduces no autonomous crash-resume contract for
Monty REPL state and makes no worker-capacity claim.

Native CodeMode keeps nested receipts in application-only tool-return metadata.
Providers do not receive that metadata, but Bifrost's raw serialized byte
diagnostics and conservative preflight estimates can include it. A production
integration must distinguish that evidence from provider-facing input when
reporting context or predicting a request budget. Return-schema propagation
also remains future work: the existing Bifrost adapter currently exposes
`Any` results to the generated program, and upstream emits an explicit warning.

## Frozen production-derived comparison

A follow-up private experiment completed 36 provider-backed agent runs: six
strict transcript diagnostics, 18 frozen comparisons across three expensive
Automatic cases and three repetitions, three one-correction SharePoint controls,
and nine CodeMode runs with the requested three-correction allowance. The last
arm reuses the unchanged nine Automatic baselines. All business effects went
through frozen mocks; real business executor calls were zero.

The sources are recent identity, SharePoint, and device runs originally using
about 511k, 341k, and 266k tokens. Historical prompts were not retained. Both arms
therefore use identical original inputs/results, current prompts and schemas
from 25 approved agents, and `claude-sonnet-5-5` with its production 8,000-token
output cap. Input, prompt, graph, schema, and fixture hashes match across arms.
These are production-derived simulations, not exact historical replays.

Mocks reject unknown data instead of borrowing an answer from another query.
They support pagination over complete captured text, equivalent Graph select
ordering, and search over an explicitly closed-world captured SharePoint site
corpus. Missing captures make a comparison inconclusive. Prescribed diagnostic
calls and effect arguments are a conservative contract, not a general semantic
quality judge.

| Three-correction arm, three repetitions | Automatic | CodeMode |
| --- | ---: | ---: |
| SharePoint prescribed task / fully covered passes | 3 / 3 | 3 / 2 |
| SharePoint median input tokens | 248,440 | 249,102 |
| SharePoint median output tokens | 4,269 | 3,579 |
| SharePoint median cache-write tokens | 27,082 | 46,717 |
| SharePoint median model responses | 8 | 8 |
| Identity prescribed task / fully covered passes | 3 / 0 | 1 / 0 |
| Device comparisons | Inconclusive | Inconclusive |

SharePoint median input is effectively flat (+0.27%), with more cache writes
(+72.5%). One CodeMode repetition has a mock gap, so those medians are descriptive,
not a fully controlled savings estimate. The two fully covered SharePoint pairs
used 22.10% and 1.02% less input but 27.87% and 70.94% more cache-write tokens.
This small sample does not establish a repeatable overall token or dollar saving.
Provider input/output/cache counts were measured; no billed-dollar claim is made.

Grace is necessary: the zero-correction arm had three fatal `run_code` errors.
The three-correction arm had none and recovered from an undiscovered function
name and JSON-string/dictionary confusion. One ordinary `load_capability` lookup
still exhausted its separate existing allowance. Incomplete identity/device
captures limit outcome comparisons, and early exits never count as savings.
The three-correction setting retains the shared run budget; it does not replay
completed effects automatically or weaken callback authorization/cancellation.

Automatic remains the production default. The next evidence needed is broader
coverage of exploratory reads and cost per successful task. Typed return schemas
and values are a concrete integration issue: current string-valued/`Any` tool
results make generated code more error-prone. No workflow-engine change or
separate sandbox service was required for this experiment. The provider runs do
not exercise live authorization, production worker claims, business-tool latency,
or worker density. Private source captures, fixtures, responses, integrity
manifests, and detailed judgments are retained outside Git.

The three-correction follow-up passed eight native CodeMode unit tests, the
existing single-workflow CodeMode E2E, and API Pyright/Ruff. The new tests cover
three syntax corrections before one successful effect, bounded retry exhaustion,
and a partial effect followed by model-selected correction without automatic
replay. The E2E still starts one workflow job.

The shared mutable test-image tag initially selected an older image missing
Monty. That collection failure was resolved by pinning the already-built Monty
image through a temporary external Compose override; no application fallback,
retry, or skip was added. The temporary wrapper/override was removed and the
shared image tag restored. Exact follow-up commands (the PATH prefix supplied
that image-pinning wrapper) were:

```bash
PATH=/tmp/code-mode-grace-docker:$PATH BIFROST_SKIP_BUILD=1 ./test.sh tests/unit/services/test_agent_code_mode.py -v
PATH=/tmp/code-mode-grace-docker:$PATH BIFROST_SKIP_BUILD=1 ./test.sh tests/e2e/api/test_agent_code_mode_spike.py -v
PATH=/tmp/code-mode-grace-docker:$PATH BIFROST_SKIP_BUILD=1 ./test.sh quality api
```

The full backend/frontend suites and a production deployment were not run.

## Primary references

- [Pydantic AI Harness CodeMode](https://pydantic.dev/docs/ai/harness/code-mode/)
- [Monty security model](https://pydantic.dev/docs/monty/concepts/security/)
- [Monty language limits](https://pydantic.dev/docs/monty/limitations/)
- [Bubblewrap](https://github.com/containers/bubblewrap)
