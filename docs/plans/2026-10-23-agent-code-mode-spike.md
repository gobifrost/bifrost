# CodeMode on the consolidated executors

Related work: [#1015](https://github.com/gobifrost/bifrost/issues/1015).
Base: `origin/main` at `a120b676e` (PostgreSQL-only executor claims,
per-worktree test images, and recovery of previously queued legacy work).

## Question and scope

Can the owning agent compose tools in code, keep intermediate results out of
model context, and run on ordinary Bifrost executors without requiring operators
to provide a separate sandbox service or privileged Linux namespace setup?

This branch now supplies an Automatic-mode integration for both chat and
background runtimes, following the initial opt-in probe. It has not been deployed.
The integrated provider comparison below fails the promotion gate: the large
mechanism-level data reductions have not translated into whole-run savings.
Direct calls and the outer code result retain the existing model result cap.

**Interpretation corrected after auditing generated code:** the initial live-model
comparison mostly measured an unfiltered tool wrapper. Generated snippets printed
or implicitly returned complete responses and copied discovered IDs through later
model turns. It did not establish the economics of the filtering/chaining pattern
described by Anthropic and Cloudflare. The audit and implementation guidance below
supersede the earlier broad interpretation of the flat token totals.

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

## Dataflow audit and published implementation guidance

An offline replay of twelve saved SharePoint/device runs reproduced their exact
business-call names/arguments, final outputs and errors. Across six CodeMode runs,
76,627 bytes of raw business results became 84,507 bytes of model-visible
`run_code` content. Several snippets used `print(full_result)`; others ended with
a bare tool call or full-response variable. A code execution boundary alone does
not cause semantic filtering. These measurements count UTF-8 tool-return content,
exclude application-only metadata, and are not provider token attribution.

The published pattern has two separate benefits: discovering a small part of a
large tool catalog and processing intermediate results without model round trips.
Bifrost already defers tool discovery in Automatic mode. Cloudflare's advertised
99.9% reduction compares the tool-definition footprint of its full API with a
search/execute surface, rather than promising that reduction for every task.
See [Cloudflare's example](https://blog.cloudflare.com/code-mode-mcp/).

The implementation should follow these concrete source-backed practices:

| Practice | Published guidance | Current spike and remaining gap |
| --- | --- | --- |
| Describe return structures and types | [Anthropic tool design](https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling#best-practices) recommends documented JSON shapes and field types; [Cloudflare connectors](https://developers.cloudflare.com/agents/tools/codemode/mcp/) derive typed methods from schemas | Bifrost propagates input schemas but currently exposes `Any` returns, including JSON strings. Return-contract propagation remains unimplemented. |
| Compose a complete related read phase | [Anthropic's example](https://www.anthropic.com/research/advanced-tool-use) chains lookups, loops and aggregation within one execution | The runtime supports dependent callbacks. Live snippets still frequently stop between reads and copy IDs through the model. |
| Keep raw intermediate data in execution | [Anthropic's MCP pattern](https://www.anthropic.com/research/code-execution-with-mcp) returns selected rows/fields to the model | Complete nested results stay in code unless the program prints or returns them. The new instructions explain implicit returns and reject string clipping as semantic filtering. |
| Discover detailed signatures only when needed | [Cloudflare search/describe](https://developers.cloudflare.com/agents/tools/codemode/) separates catalog search from detailed method documentation | Existing ToolSearch remains in use; there is no new eager catalog. |
| Preserve authorization and approvals per effect | [Cloudflare's MCP connector](https://developers.cloudflare.com/agents/tools/codemode/mcp/) pauses approval-required operations before dispatch | Existing Bifrost binding/graph/schema checks and cancellation apply to every callback. No automatic replay of completed mutations. |
| Evaluate workload shape and actual usage | [Anthropic's fit guidance](https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling#when-to-use-programmatic-calling) identifies large results/fan-out as strong fits and calls requiring model reasoning between steps as weaker fits | Measure successful outcomes, whole-run input/output/cache usage, round trips, and cumulative returned-content rereads. Smaller individual returns alone do not establish savings. |

These are integration requirements, not reasons to keep adding task-specific
prompt instructions. The next implementation should carry supported return
contracts through the shared tool catalog and adapter, document actual
string/object envelopes, and supply a small generic worked example. It must not
invent output schemas for unconstrained dynamic workflows. Tool definitions and
the same authorization path should serve Automatic and CodeMode consumers.

The opt-in capability now gives explicit generic guidance: discover the real
signature, fetch/decode/filter/project in code, derive identifiers inside the
snippet, and return compact evidence plus uncertainty. A new native regression
verifies one search plus two dependent reads inside one code call, no raw sentinel
content in model-visible tool returns, and full original results retained in the
application audit. It tests runtime capability and instruction transport with a
prescribed `FunctionModel` program, not live-model adoption of the guidance.

Two additional three-repetition SharePoint arms tested generic guidance and then
explicit output-boundary guidance against the frozen Automatic baselines. The
first still often string-sliced JSON. The stronger guidance reduced the summed
unique `run_code` content from 73,410 to 47,906 bytes (34.7%) relative to the
earlier CodeMode arm, but cumulative reread content only fell from 258,496 to
254,286 bytes (1.6%). Median model responses rose from 8 to 10, and median input
was 312,689 versus Automatic's 248,440. Two stronger-guidance repetitions had
mock coverage gaps; only one was fully covered. Including framework discovery
returns, cumulative content grew from 405,602 to 449,320 bytes. These diagnostic
arms show partial filtering without
consistent chaining; they do not demonstrate net savings. They are retained,
including their mock gaps, rather than selecting only the favorable runs.

A separate **human-authored reference program**, executed through the same native
boundary against frozen SharePoint data, completed six dependent business reads
in one `run_code` call. It derived site/drive/item identifiers from earlier results
and projected 15,484 raw business-result bytes to 3,758 returned bytes (75.7% less).
The projection preserves relevant sharing roles, group identities, identity
counts, inheritance and uncertainty about effective access. Two framework
discovery returns add 6,322 bytes. This proves the mechanism works on the captured
read phase; it is not a live-model result, a whole-ticket completion, or a token
or dollar saving. The full private artifact records the program and its checks.

The experiment now contains 42 provider-backed runs, with all business callbacks
mocked. The reference and saved-transcript audits use no provider. The next live
comparison should follow the return-contract work, freeze its instructions and
semantic outcome rubric before execution, and verify that generated programs
actually filter and chain before interpreting their cost. No further prompt
search or production rollout is implied by these diagnostic results.

Final focused verification passed nine native unit tests, the existing one-job
workflow E2E, and API Pyright/Ruff. The E2E spent 4.40 seconds in setup, 2.08 seconds
in its call and 0.20 seconds in teardown. A stale wording assertion initially
failed after the guidance changed; it now checks transport of the actual complete
instruction constant, while the behavioral dataflow assertions remain intact.
The final commands used an external Compose image override pinned to the same
Monty image above:

```bash
PATH=/tmp/bifrost-code-mode-filtering-checks:$PATH BIFROST_SKIP_BUILD=1 ./test.sh tests/unit/services/test_agent_code_mode.py -v
PATH=/tmp/bifrost-code-mode-filtering-checks:$PATH BIFROST_SKIP_BUILD=1 ./test.sh tests/e2e/api/test_agent_code_mode_spike.py::test_code_mode_dispatches_one_bound_workflow_on_existing_executors -v --durations=0
PATH=/tmp/bifrost-code-mode-filtering-checks:$PATH BIFROST_SKIP_BUILD=1 ./test.sh quality api
```

Full backend/frontend suites and a production deployment were not run.

## Integrated Automatic candidate and fixed proof gate

The candidate now applies one shared native CodeMode policy to Automatic chat
and background execution. It covers agents with no delegates, compositions with
only specialist tools, and ordinary chat's tool catalog. Workflow/MCP/system
business tools run through code; framework discovery, capability loading and
explicit isolated delegation retain their native lifecycle. This is a branch
candidate, not a production enablement or a measured fleet-wide improvement.

The shared tool definition now carries an optional return schema through
specialist qualification. Bifrost's adapters declare their truthful **string**
transport contract, with JSON decoding and tool-specific error formats documented.
This does not supply an inner object schema for an arbitrary workflow, or pass
an MCP vendor schema off as the Bifrost envelope's schema.

Chat now registers each nested call from its scoped outer invocation, persists
an explicit child/outer audit relation before dispatch, and retains full results
on audit rows. Rebuilt history excludes only those explicitly related rows and
keeps the bounded outer receipt. Ordinary tool IDs containing double underscores
remain ordinary history. The relation is durable even if the outer invocation
is interrupted. Code variables are local to a run; later chat turns use persisted
receipts and identifiers rather than assuming the REPL survived.

Interrupted-history coverage proves exclusion of raw nested data, not a durable
Monty resume protocol. Before rollout, interrupted code runs also need a compact
continuation record distinguishing completed effects from effects with unknown
outcomes; filtering all child rows alone must not leave the next model guessing
which mutations completed. This candidate is not promoted as safe crash-resume.

Context diagnostics and conservative preflight estimates now omit application-only
`ToolReturnPart.metadata`, including full nested receipts. They do not mutate the
original audit messages or alter actual provider billing counters. The prior
warning about raw serialized diagnostics above describes the initial spike.

A database-backed chat test executes three reads of more than 32,000 characters,
then reloads the conversation with a fresh session and executor. It verifies
complete nested audit records and only the compact aggregate in the continuation
history. It starts zero business-worker jobs. The existing single-workflow E2E
now uses the production Automatic policy without injected CodeMode capabilities;
it still starts exactly one workflow job through the existing executors.

Before six fresh provider runs, the private protocol froze three SharePoint
pairs, the source/input/schema/rubric hashes, three correction attempts, and a
strict promotion gate: covered successful evidence in every pair and lower
whole-run usage per successful task. Both arms used identical return-contract
documentation, production-derived inputs, current prompts, model and frozen mocks.
No fixture widening, extra provider trials or post-result prompt search occurred.
The old Automatic baseline was rerun, not reused for this comparison.

| Observed median over three fresh repetitions | Existing Automatic | Integrated CodeMode |
| --- | ---: | ---: |
| Provider input tokens, including cache tokens | 259,141 | 388,502 |
| Provider output tokens | 4,125 | 5,230 |
| Cache-read tokens | 229,752 | 344,438 |
| Cache-write tokens | 28,390 | 44,038 |
| Model responses | 8 | 12 |
| Prescribed task / fully covered passes | 3 / 3 | 3 / 0 |

**The promotion gate failed.** Observed median input rose 49.9%. All CodeMode
repetitions encountered missing mock coverage (15 strict replay/off-script events
in total), so these numbers do not establish a clean production cost regression
estimate either. They provide no basis for a savings claim. Business callbacks
were mocked, real business executor calls were zero, and all final outcomes were
`needs_human` without SharePoint permission mutations. Provisional source-based
semantic review also flags access/root-cause inferences requiring adjudication;
it is not an independent human certification.

The traces explain why a smaller return did not suffice. Across three CodeMode
runs, 29 snippets made 63 nested calls, with repeated successful reads, discovery
round trips, four native retry-feedback events and continued raw/string-clipped
or mixed outputs. Unique returned content fell 13.8% (81,939 to 70,668 bytes),
but cumulative content reread rose 61.7% (340,365 to 550,506 bytes). These are
tool-return content bytes including framework results, not provider tokens.

The continuation is therefore an experimental integration with verified history
and dispatch boundaries, not a rollout. The next cost experiment needs a clearer
phase-level discovery/processing interface and actual structured result contracts
where supported, plus complete frozen coverage. Merely declaring the existing
transport `str` and enabling native CodeMode everywhere did not establish the
published filtering/chaining economics. Keep that distinction when evaluating
the earlier hand-written 75.7% payload reduction.

Final verification after rebasing onto `a120b676e` passed **131 tests**, with
zero failures, errors or skips, followed by API Pyright/Ruff. The test image was
rebuilt as the worktree-owned tag `bifrost-test-api-dev:code-mode-proof-1015`
(`sha256:107078a90c412bf81f45aa2fa22b01d7483f87215fe94e57ff6f01b64798bb38`)
to include main's new broker-recovery dependency. An old API container initially
failed to import that dependency; recreating services with the rebuilt image
resolved the harness state. The benchmark remains pinned to its earlier image
and immutable source snapshot. All nine shared policy, adapter, composition,
measurement and executor source files match the tested candidate byte-for-byte;
the later artifact factory also declares the same serialized return contract.

The selected commands were:

```bash
BIFROST_TEST_IMAGE_TAG=code-mode-proof-1015 BIFROST_SKIP_BUILD=1 ./test.sh \
  tests/unit/services/test_agent_code_mode.py \
  tests/unit/services/test_agent_tool_return_contract.py \
  tests/unit/services/test_agent_code_mode_runtime_policy.py \
  tests/unit/services/agent_runtime/test_tool_policy.py \
  tests/unit/services/agent_runtime/test_context_breakdown.py \
  tests/unit/services/test_agent_executor_runtime.py \
  tests/unit/services/test_agent_composition_stream.py \
  tests/unit/services/test_autonomous_agent_executor.py \
  tests/unit/services/agent_runtime/test_composition.py \
  tests/unit/services/test_agent_runtime_authorization.py \
  tests/unit/services/test_chat_artifacts.py \
  tests/e2e/api/test_agent_code_mode_spike.py \
  tests/e2e/api/test_agent_code_mode_chat.py -v --durations=0
BIFROST_TEST_IMAGE_TAG=code-mode-proof-1015 BIFROST_SKIP_BUILD=1 ./test.sh quality api
git diff --check
```

Earlier focused failures were fixed rather than retried unchanged: Pydantic
requires `include_return_schema` to expose a known schema; its provider fallback
can express that schema in the description, while the sandbox catalog retains
the actual return type. Test doubles now use the supported streaming callback,
structured outer results and dataclass message adapter. The old Automatic stream
test now checks the intentional code/child event sequence instead of direct
business calls. A missing `replace` import and one unused test import were fixed.
Concurrent harness resets were eliminated by giving the parent exclusive stack
ownership. No retries, skips or broad-failure waivers were added. Full backend
and frontend suites, production deployment and worker-density testing were not run.

## Primary references

- [Pydantic AI Harness CodeMode](https://pydantic.dev/docs/ai/harness/code-mode/)
- [Monty security model](https://pydantic.dev/docs/monty/concepts/security/)
- [Monty language limits](https://pydantic.dev/docs/monty/limitations/)
- [Bubblewrap](https://github.com/containers/bubblewrap)
- [Anthropic programmatic tool calling: best practices and workload fit](https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling)
- [Anthropic code execution with MCP](https://www.anthropic.com/research/code-execution-with-mcp)
- [Anthropic advanced tool use and worked examples](https://www.anthropic.com/research/advanced-tool-use)
- [Cloudflare Code Mode: composition, types and discovery](https://developers.cloudflare.com/agents/tools/codemode/)
- [Cloudflare MCP connectors: schema-derived methods and approvals](https://developers.cloudflare.com/agents/tools/codemode/mcp/)
