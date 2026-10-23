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
alone retains the existing result cap. Provider token savings have not been
measured.

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
sequential tool flags remain intact. Eager and speculative execution are off;
automatic code retries are disabled. Missing sandbox dependencies must produce
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

No full backend suite, frontend suites, real-provider comparison, or
production deployment was run. Initial failures were resolved: explicitly
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

Production rollout needs a reviewed fixture comparison using actual provider
input/output/cache usage and cost per successful task. It also needs deliberate
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

## Primary references

- [Pydantic AI Harness CodeMode](https://pydantic.dev/docs/ai/harness/code-mode/)
- [Monty security model](https://pydantic.dev/docs/monty/concepts/security/)
- [Monty language limits](https://pydantic.dev/docs/monty/limitations/)
- [Bubblewrap](https://github.com/containers/bubblewrap)
