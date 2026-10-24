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
The original integrated provider comparison below fails the promotion gate: the large
mechanism-level data reductions have not translated into whole-run savings.
Direct calls and the outer code result retain the existing model result cap.
The later Cloudflare-pattern revision replaces that candidate's model-side tool
search with a fixed execution-resident SDK; its implementation and verification
are described separately below.

**Current candidate:** Automatic chat and background runs now use native-generated
Python function bindings behind lazy sandbox discovery. The generic
`bifrost_call(name, arguments)` adapter used in the recorded provider experiments
has been removed. See **Direct function bindings and reference-audit correction**
below. Those experiments remain historical observations of the previous adapter;
they do not measure this candidate.
The **Cloudflare source-conformance follow-up** further aligns search and describe
with the pinned reference and preserves native return contracts.

**Latest provider benchmark:** the direct-function candidate at `4db7aaa79` has a frozen
20-attempt comparison with high thinking in both arms. Eight pairs passed the
required structured report/action contract in both arms; median within-pair
reductions were 52.0% input tokens, 84.4% estimated model cost, and 17.6% elapsed
time. Separate audit found prose errors and one large raw return. The subsequent
source-conformance revision below has not received another provider benchmark. See **Provider
benchmark of direct function bindings** below for the evidence and limits.

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
PATH=/tmp/bifrost-code-mode-filtering-checks:$PATH BIFROST_SKIP_BUILD=1 ./test.sh tests/unit/services/agent_runtime/test_context_breakdown.py \
  tests/unit/services/test_chat_artifacts.py -v
./test.sh quality api
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

## Reference-architecture and benchmark-confound audit

The observed increase must not be presented as normal CodeMode overhead.
[Anthropic documents](https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling)
that small sequential workloads can cost more (roughly 8% in one cited benchmark),
while large-result/fan-out workloads are its stronger fit. That does not explain
or validate this candidate's observed 49.9% input increase, especially with missing
fixture coverage.

The more specific reference comparison is:

| Concern | Published implementation | Candidate |
| --- | --- | --- |
| Discovery | [Cloudflare's runtime](https://github.com/cloudflare/agents/blob/main/docs/codemode/index.md) exposes `search`/`describe` inside running code, allowing focused discovery data to remain there | Native capability loading and ToolSearch remain separate model exchanges. This is valid Pydantic behavior, but not equivalent to Cloudflare's execution-resident discovery interface. |
| Result processing | Anthropic recommends documented JSON structures/field types and processing intermediate results within execution | The candidate documents a serialized string transport. It does not yet expose reliable inner result schemas. |
| Cache stability | [Pydantic's guidance](https://pydantic.dev/docs/ai/harness/code-mode/) recommends `dynamic_catalog=True` with discovery | Already enabled. The active code-tool schema stayed stable at 4,946 measured bytes; this is not an omitted setting to toggle again. |

Trace counts also narrow the diagnosis: **both arms made one capability load and
two tool searches in every repetition**. Those discovery calls alone do not
explain the extra CodeMode turns. Their placement is an architectural difference
to test, not an isolated cause of this increase. CodeMode moved discovered
signatures into instructions, while reducing the active tool-schema footprint;
neither byte count is itself provider token attribution.

The mocks introduced concrete confounds. CodeMode repetitions 1 and 2 each tried
`list_items(limit=200)` twice. The input schema permits that value, but the replay
matcher rejected it; calls omitting the limit succeeded. Repetition 3 queried
permissions for files whose permission results were not captured. Three native
code retries were JSON-decoding failures after these missing replay responses;
one further retry involved an undiscovered direct tool name. These are not clean
steady-state execution samples. Do not infer that the published approach normally
increases usage from this matrix.

Before another provider comparison, the benchmark needs operation-level mock
semantics instead of exact historical argument matching where equivalence can
be established. A page-size variation may use captured data only when the source
and operation prove that capture complete. Uncaptured permission states must
remain unknown; use a separately declared complete synthetic corpus or additional
read-only captures rather than inventing historical answers. The candidate also
needs an execution-resident search/describe/call interface over the same live
authorization path, plus truthful structured return contracts. These changes
must be tested independently before attributing a future result to any one of
them. This audit made no provider calls and did not change the frozen matrix.

## Cloudflare-pattern revision and cheaper provider comparison

The next candidate follows Cloudflare's execution-resident discovery pattern:
search returns ranked method names and short descriptions, describe returns the
contract for one method, and execution processes complete intermediate values
before returning selected evidence. The
[reference runtime](https://developers.cloudflare.com/agents/tools/codemode/how-it-works/)
explicitly allows discovery and execution to occupy separate model turns when
the model needs to interpret the discovered contracts. A one-turn solution is
possible for data-dependent orchestration, not a universal guarantee.
The source reference was checked against Cloudflare Agents commit
[`321814b6c13f72cff82161e7eb20312574ac9b7a`](https://github.com/cloudflare/agents/tree/321814b6c13f72cff82161e7eb20312574ac9b7a)
on 2026-10-23; Bifrost's interpreter and Harness pins remain those listed above.

This is a protocol adaptation to Bifrost's existing Python/Monty executor, not
installation of Cloudflare's JavaScript package. Cloudflare's default
`DynamicWorkerExecutor` requires its Worker Loader binding. The Bifrost candidate
uses fixed sandbox functions `bifrost_search`, `bifrost_describe`, and
`bifrost_call` over the same authorized Bifrost toolset. It does not implement
Cloudflare's Durable Object storage, saved snippets, rollback, or approval replay.
Those features must not be claimed on the strength of this spike.

Discovery and business results use a fixed `{ok, data, error}` envelope. JSON
adapter strings can become structured `data`; plain text remains text. The
envelope supplies a reliable transport contract without inventing result-field
schemas for workflows that do not declare them. Full original execution results
remain in Bifrost's audit. Existing ownership, authorization, cancellation,
effect ordering, and workflow dispatch continue to apply.

The implementation removes model-side ToolSearch from Automatic composition.
The outer model receives `run_code` and any native capability-loading/delegation
controls. Search and describe resolve the current authorized catalog inside the
sandbox; describe exposes the original input schema and labels the original
adapter return schema separately from the SDK's decoded transport. Each call
rechecks the current catalog and dispatches through the assembled toolset's
wrapper chain. An original leaf toolset must not receive an assembled wrapper's
tool descriptor directly. The SDK does not cache authority from discovery.

The SDK permits 64 total SDK interactions per code invocation and separately
limits business callbacks to 16. The business counter belongs to that invocation
and is discarded with its scope. Cancellation latches there too, preventing a
later callback even when generated code catches an exception. Search and describe
do not consume the business-call allowance. SDK-owned return schemas document
the exact envelope without claiming types for unknown business fields.

The final focused boundary check passed 17 tests: SDK discovery and dependent
reads, native capability loading without catalog leakage, refreshed catalog
revocation, schema denial, JSON/plain-text/error transport, per-invocation call
limits, cancellation through both Bifrost and native function toolsets, stream
events, one workflow dispatch through the existing worker, and chat continuation
that retains full audit while replaying only the outer compact result. Earlier
targeted consumer checks also exercised composition authorization and the
autonomous loop. No full backend or frontend suite was run for this revision.
The final combined consumer run subsequently passed **116 tests**, with zero
failures, errors, or skips. API Pyright and Ruff also passed. Exact commands:

```bash
BIFROST_TEST_IMAGE_TAG=code-mode-proof-1015 BIFROST_SKIP_BUILD=1 ./test.sh \
  tests/unit/services/agent_runtime/test_sandbox_sdk.py \
  tests/unit/services/test_agent_code_mode_runtime_policy.py \
  tests/unit/services/agent_runtime/test_tool_policy.py \
  tests/unit/services/test_agent_code_mode.py \
  tests/unit/services/test_agent_composition_stream.py \
  tests/unit/services/test_autonomous_agent_executor.py \
  tests/unit/services/agent_runtime/test_composition.py \
  tests/unit/services/test_agent_runtime_authorization.py \
  tests/e2e/api/test_agent_code_mode_spike.py \
  tests/e2e/api/test_agent_code_mode_chat.py -v --durations=0
BIFROST_TEST_IMAGE_TAG=code-mode-proof-1015 BIFROST_SKIP_BUILD=1 ./test.sh quality api
git diff --check
```

The new provider comparison uses the direct DeepSeek endpoint and canonical
`deepseek-flash` model ID in **both** arms. DeepSeek's
[current documentation](https://api-docs.deepseek.com/quick_start/pricing/)
maps that ID to `DeepSeek-V4.1-Flash-1017`; model pricing and context-cache hits
are separate from a CodeMode token reduction. The benchmark explicitly disables
thinking with the provider's documented `thinking.type=disabled` request body.
The pinned generic reasoning setting alone does not emit that DeepSeek option.

The new fixtures are complete, fictional, production-shaped datasets. They are
separate from the frozen historical-capture comparisons above. Valid queries
must have operation-level semantics, including pagination and queries for every
listed object, rather than matching only previously captured argument tuples.
Only quality-passing paired runs can support a savings claim; a task failure or
missing mock response cannot count as an efficiency improvement.

The first DeepSeek pilot was stopped after seven runs because the synthetic
world was incomplete: search returned an archive site and a template library
without follow-up records, named principals lacked resolution outcomes, and
the work-log schema failed to constrain the router's accepted outcome values.
Those seven immutable artifacts are invalidated for comparison. They consumed
54 provider responses, 206,691 input tokens, and 14,357 output tokens; these are
experiment-provenance totals, not a savings result.

The replacement v2 fixture defines a finite global world. Valid missing reads
return ordinary empty collections or structured NotFound results. Known
references have positive records or explicitly empty collections. Before
launch, a closure crawl traversed 2 sites, 2 libraries, 48 items and their
permission records, and the named principals; it also verified both allowed
work-log outcome values. Uncertainty in generated assessments receives a
source-based semantic review, rather than an exact-phrase check. The new matrix
uses an independent protocol and result directory; the earlier artifacts and
protocol remain unchanged.

### Completed DeepSeek Flash v2 comparison

All 18 provider-backed runs completed with zero transport errors and zero real
business executor calls. Source, corpus, protocol, model settings, and tool
schema hashes match across arms. Every CodeMode provider request offers only
`run_code`; the complete business catalog is not sent as outer function tools.
All cases are synthetic, with a much smaller catalog and shorter operational
prompt than the earlier production-derived simulations. This matrix validates
the SDK integration and records model behavior; it is not an exact production
replay or a production savings estimate.

| Three repetitions per arm | Direct baseline input / output / responses (median) | CodeMode input / output / responses (median) | Preregistered coverage passes, baseline / code |
| --- | ---: | ---: | ---: |
| SharePoint assessment | 20,831 / 1,622 / 6 | 10,900 / 967 / 5 | 2 / 0 |
| Identity correlation | 13,738 / 1,108 / 4 | 15,669 / 1,297 / 6 | 0 / 0 |
| Small-response control | 9,266 / 1,152 / 4 | 14,878 / 980 / 6 | 2 / 1 |

These are raw provider-usage observations, **not credited savings**. Across
nine runs per arm, input totals were 123,388 direct and 141,399 CodeMode;
output totals were 11,618 and 10,347. Cache-read totals were 103,808 and
123,904, respectively; cache-write totals were zero. Input volume alone is
not billed cost, especially with DeepSeek's different cached-input rate.
Only the first small-response control pair passes the primary gate in both
arms; it is insufficient to establish a general savings claim.

The gate also has limits. Some failures require a particular discovery/list
sequence even though the input already supplies the target IDs. Those failures
alone do not establish a wrong answer. Baseline identity runs correctly
correlated membership with the grant and retained uncertainty, but recorded
`resolved` where the protocol expected `needs_human`. The minimal synthetic
prompt is not equivalent to the production agents' operating instructions.
These issues are retained in source-based review, not silently reclassified
to manufacture a passing comparison.

Other failures are substantive. The assessment CodeMode runs omitted work-log
recording; two identity runs passed a UPN as a membership object ID and did not
recover the known group relationship. Generated programs still print full
business responses or string-clipped payloads in several runs. Moving discovery
into the sandbox is therefore implemented and verified, but the live model has
not consistently selected the intended complete, compact orchestration pattern.

Raw `ToolCallPart.args` from this provider are JSON strings. The frozen runner's
convenience `generated_snippets` field consequently missed them. The independent
analyzer parses the retained response arguments to recover the actual programs;
the raw artifacts and provider usage were not modified. Source-based review is
Codex's inspection of those receipts and programs, not human outcome certification.

**Disposition:** the Automatic chat/background spike is implemented and locally
verified, and the cheaper provider route works. Whole-run savings and production
promotion remain unproven. Do not deploy this revision on the strength of the
synthetic data reductions or relax its quality gate. A production decision needs
representative operating prompts, task-outcome evaluation rather than prescribed
call sequences, and a competent model-generated filtering/chaining trace.

### Focused proof with production prompts

The next comparison preserves the frozen production graph: 25 agents, 237 tool
definitions, 206 distinct original tool names, the ticket owner's operating
prompt, and each specialist's operating prompt. Both arms use the production
composition and Bifrost toolset with snapshot authorization and a closed-world
mock executor. This tests model behavior, not live authorization or production
effects. The direct arm retains native discovery; Automatic uses the sandbox
SDK and native capability loading.

The single fictional task inventories five SharePoint folders, 300 files, and
210 permission records. It joins file metadata with folder permissions, filters
CSV files by size, and records exact counts, byte totals, and anonymous permission
IDs in one mocked terminal action. Each complete read response is below the
existing 32,000-character cap: the largest observed serialized response is 29,284
characters, including mock listing metadata. The source inventory/permission
payloads total 249,636 characters. Neither arm loses required evidence
to truncation. This is deliberately a favorable mechanism test, not a historical
production replay or a representative estimate for all Automatic traffic.

The preregistered matrix has ten pairs, alternating which arm runs first. Both
use direct `deepseek-flash` with thinking explicitly disabled, the same request
and token budgets, and three native code-correction attempts. A private ordinary
HTTP client disables transport retries in both arms while retaining the shared
production model factory and DeepSeek profile. The production transport's
bounded retries are otherwise still present; setting the provider SDK's retry
count to zero alone does not disable them.

Success requires exact structured report facts, complete evidence coverage,
one correct terminal action to the requesting employee, and no forbidden effects.
The behavior gate separately requires dependent reads and aggregation in one
processing program, compact returned evidence, and no full raw business results.
Promotion requires at least eight successful matched pairs, at least eight code
semantic/action and behavior passes, no material success deficit, at least 25%
median per-pair input reduction, and no increase in median model responses.
Failed tasks receive no savings credit. Input tokens already include cached
tokens in the pinned Pydantic AI version; cache reads must not be added again.

Before provider execution, a network-disabled Docker check traversed 327 normal
mock operations, including inherited permission reads for all 300 listed files,
with no missing responses or real executor calls. Both scripted arms passed the
semantic/action oracle. Additional checks rejected wrong aggregates and answer
leakage, exercised actual raw printed and final-expression CodeMode returns,
verified one HTTP attempt on an injected 429, and checked DeepSeek's inclusive
cache accounting. Source, shared modules, fixture, model settings, and private
runner/analyzer hashes were frozen before the first paid call.

The initial writer saved six trials, then failed while serializing a Python set
inside a captured CodeMode return for repetition 4. That attempt's receipt and
usage are unknown. It remains an invalid observation; it is not a model failure,
a zero-cost run, or a successful matched pair. It was not repeated. A separate
recording-only wrapper retained the original frozen hashes, recorded each later
provider response to a durable JSONL log, and normalized set containers when
writing artifacts. An offline test exercised that path through actual CodeMode.
The wrapper continued the thirteen remaining scheduled trials. Whole-cohort
provider billing remains incomplete because the lost receipt cannot be priced.

The promotion decision was already closed after the first three observed
CodeMode trials failed the behavior gate: at most seven of ten could subsequently
pass its eight-pass threshold. Completing the remaining trials characterizes
the result; it cannot convert this failed gate into rollout approval. No prompts,
model settings, tool policies, or runtime source were tuned between paid trials.

The completed cohort contains ten direct receipts, nine CodeMode receipts, and
the one lost CodeMode attempt. These are descriptive observations across
different task-success rates, not matched-success savings:

| Captured observations | Direct | CodeMode |
| --- | ---: | ---: |
| Exact structured report and terminal contract | 1 / 10 | 9 / 9 |
| Median inclusive input tokens | 530,516.5 | 292,573 |
| Median output tokens | 4,937.5 | 4,355 |
| Median model responses | 10 | 17 |
| Median elapsed time | 32.37 s | 35.27 s |
| Required combined processing program | Not applicable | 0 / 9 |

The only successful direct repetition was number 4, whose CodeMode receipt is
lost. There are therefore zero successful matched pairs, and the preregistered
efficiency comparison is not estimable. Among captured requests, direct used
6,286,860 inclusive input tokens (4,413,568 cached; 1,873,292 misses), while
CodeMode used 2,750,420 (2,622,464 cached; 127,956 misses). The different run
counts, different success rates, and unknown lost usage preclude a whole-cohort
cost or savings claim. No captured run had a provider error or real business
executor call.

Source-based inspection confirms that none of the nine captured CodeMode
traces returned a complete business response container larger than 10 KB.
This is evidence of code-side projection, not proof against clipped/partial
field exposure. Programs still printed small raw metadata or clipped permission
values and split the task across 14–17 snippets, including discovery and separate
calculation/formatting snippets. Extra successful turns, rather than exhausted
code-correction attempts, account for the fragmentation.

Two evaluation limits are retained explicitly. The frozen byte counter includes
discovery-only returns although the protocol describes business-derived
evidence. A separate observer analysis excluding pure discovery measures
8,117–36,470 bytes per run; no run has the required combined processing program,
so the zero-pass behavior disposition is unchanged. Also, exact JSON success
does not certify every narrative sentence: source review found contradictory
threshold prose in repetition 7 and incorrect direct-versus-inherited wording
in repetitions 1 and 8. The structured reports remain exact under the frozen
oracle; these prose defects remain additional reasons to withhold promotion.

**Disposition:** runtime conformance passed, code-side data processing is
demonstrated, and the focused promotion gate failed. The shared Automatic/chat
implementation remains undeployed. This one favorable fictional workload does
not establish general quality or efficiency across Automatic traffic.

Astra recommends a separate causal test with ten CodeMode-only repetitions,
changing only DeepSeek Flash thinking from disabled to its normal high setting.
Require at least eight exact report/action and compact-behavior passes and at
most ten median model responses before running another paired cohort. DeepSeek
[documents thinking as enabled by default](https://api-docs.deepseek.com/guides/thinking_mode/)
and requires reasoning content to survive tool turns. A network-disabled mock
probe through this frozen production model factory already verified that
reasoning-content round trip. No additional paid thinking-mode experiment has
been run. A later savings comparison needs fresh direct and code arms under the
same intended production reasoning setting.

## Direct function bindings and reference-audit correction

An implementation audit found a material departure from the reference pattern.
Cloudflare's pinned [proxy instructions](https://github.com/cloudflare/agents/blob/321814b6c13f72cff82161e7eb20312574ac9b7a/packages/codemode/src/proxy-tool.ts)
tell generated code to search, describe, and call generated connector methods.
Bifrost instead required a generic function name plus an argument dictionary and
wrapped every business result in an SDK envelope. The existing Harness already
supports generated Python function bindings; adding the generic business RPC
was unnecessary.

The current candidate uses those native bindings. Generated code calls an
approved function with its schema-derived keyword arguments and consumes the
source result directly. JSON adapter text is decoded inside the sandbox; plain
text, source error receipts, and decoded JSON error objects retain their source
meaning. In that candidate, search and describe kept their fixed discovery envelopes.
Describe supplied a generated Python signature and nested parameter type
declarations, the original input schema, and the source return schema. An
undeclared business result stays unknown rather than acquiring invented fields.
Explicit structured return types are retained when JSON-text decoding cannot
change their type; text adapters and unresolved unions remain `Any`. Native
asynchronous functions are described as asynchronous and require `await`.

Native CodeMode's `dynamic_catalog=True` normally moves its generated catalog
into model instructions; it does not make the catalog private. A narrow wrapper
suppresses that catalog instruction while retaining native binding generation,
Monty execution, argument handling, and tool dispatch. Search and describe still
resolve only the live authorized entry and loaded-specialist tools. Callable
identity comes from trusted composition metadata rather than owner/name prose.
Names shared anywhere in the approved composition receive stable owner-qualified
Python aliases before any specialist loads, so loading another capability does
not rename an existing callable.

Every function call refreshes the live mapping, checks its input schema, and
passes the original name and descriptor through the assembled toolset wrapper
chain and existing executor. The shared per-program budget still allows 16
business callbacks and 64 total interactions; discovery does not consume the
business allowance. Cancellation remains latched across caught exceptions.
There is no workflow-engine, worker, deployment-topology, or customer sandbox
installation change. Full intermediate values stay available inside code and
in the audit; the existing outer model-result bound still applies. Automatic
chat, background execution, and native isolated children use the shared policy.
This remains an undeployed candidate.

The reference audit also corrects how the prior experiment was described:

- **All nine saved CodeMode runs batched business reads.** They performed 14–15
  reads across 5–8 read-containing programs, with 3–6 reads in the largest batch.
  The reported zero passes referred to our stricter requirement that all
  dependent reads and aggregation occupy one processing program. Cloudflare
  does not specify that as a universal acceptance rule. Some extra calculation
  and formatting programs remain avoidable, but zero batching was not observed.
- **The exact scripted offline program had information the model did not.**
  The production workflows describe results in prose and expose generic return
  contracts, without schemas for the nested response fields used by that
  program. Discovering their actual shapes can legitimately require inspection.
- **The compactness meter counted pure discovery returns against a business
  evidence budget.** The separate observer result above is diagnostic; the
  frozen protocol and raw scores remain unchanged.
- **There is no successful paired cost comparison.** CodeMode's 9/9 captured
  exact reports versus direct's 1/10 is evidence favoring task completion in
  this fixture. The only successful direct counterpart is the lost CodeMode
  receipt, so equivalent-success savings remain unmeasured. Response count,
  input tokens, cache usage, elapsed time, and billed cost need separate reporting.

The thinking-only experiment proposed above is therefore superseded as the
immediate next step. Validate this function-binding candidate first, then freeze
a fresh comparison with the same intended reasoning setting in both arms.
Acceptance should measure correct completed work, authorized effects, actual
code-side filtering/chaining, business-derived evidence separately from discovery,
and cost and latency per successful task. A single-program target can remain a
workload-specific diagnostic, but cannot stand in for Cloudflare conformance.
No additional paid provider run was made for this implementation change, and the
old experiment files and manifests remain frozen.

A separate network-disabled probe exercised the new generator against the
frozen production catalog: 25 agents, 237 bound definitions, 206 distinct
original names. It described the 21 root aliases before specialist loading,
loaded all 24 specialists, and described all 237 generated aliases successfully.
The outer catalog remained `run_code` and `load_capability`; the probe made zero
provider calls and zero business callbacks. Its script and source-hashed result
are in the private `function-bindings-catalog-probe-20261023` directory alongside,
outside the frozen experiment directory. This verifies catalog compatibility,
not completion quality or economics.

That probe also clarified the native resource boundary: 128 suspensions is a
cumulative limit for one Monty REPL session, not an allowance renewed by every
snippet. Describing the entire catalog exceeded it. The final catalog-only probe
uses nine explicit fresh sessions, discarding unneeded state between chunks;
it does not increase a limit or replay business effects. Automatic code retains
native `restart: true` for a fresh session, while the SDK's 16-business-call and
64-total-call bounds remain per outer invocation.

Focused verification of this candidate passed **121 tests** with zero failures,
errors, or skips, plus API Pyright (zero errors/warnings) and Ruff. The selected
checks cover direct dependent reads, typed discovery, catalog privacy, capability
loading, live revocation, schema denial, cancellation, call budgets, stable
composition identity, chat history/audit separation, and one workflow dispatch
through the existing worker. The two existing E2E cases were updated rather than
adding another worker job. The full backend and frontend suites were not run.
No API DTO or frontend surface changed.
The workflow E2E spent 4.10 seconds in fixture setup, 1.70 in the request/worker
path, and 0.28 in teardown; the chat E2E call took 0.48 seconds.

Exact commands from the worktree (using its existing Docker test image):

```bash
BIFROST_TEST_IMAGE_TAG=code-mode-proof-1015 BIFROST_SKIP_BUILD=1 ./test.sh \
  tests/unit/services/agent_runtime/test_sandbox_sdk.py \
  tests/unit/services/agent_runtime/test_tool_policy.py \
  tests/unit/services/test_agent_code_mode_runtime_policy.py \
  tests/unit/services/test_agent_code_mode.py \
  tests/unit/services/test_agent_composition_stream.py \
  tests/unit/services/test_autonomous_agent_executor.py \
  tests/unit/services/agent_runtime/test_composition.py \
  tests/unit/services/test_agent_runtime_authorization.py \
  tests/e2e/api/test_agent_code_mode_spike.py \
  tests/e2e/api/test_agent_code_mode_chat.py -v --durations=0
BIFROST_TEST_IMAGE_TAG=code-mode-proof-1015 BIFROST_SKIP_BUILD=1 ./test.sh quality api
```

## Provider benchmark of direct function bindings

The corrected candidate at `4db7aaa79` was benchmarked in a fresh, frozen cohort:
10 alternating pairs, direct DeepSeek Flash with thinking enabled and effort
`high` in both arms, identical production operating prompts and business schemas,
and the same fictional five-folder/300-file SharePoint workload. This remains
one favorable synthetic task using a frozen 25-agent/237-definition production
graph, not a replay of historical runs or a sample of general chat traffic.
The source/shared trees, fixture, protocol, runner, recorder, wire probe, and
analyzer were hashed before the first generation request. No model settings,
prompts, budgets, schemas, or source were tuned between attempts.

The new protocol makes required report/action correctness the task contract.
Batch size, model responses, a single-program target, and returned-data size are
separate diagnostics. They do not redefine task success. Both arms retain the
40-request, 1.5-million-total-token and 360-second budgets; native code allows
three corrections. All complete read responses are below the ordinary
32,000-character model result cap, so the direct arm does not lose evidence to
truncation. Both execute against the same closed-world mock router, with no
live business executor or production authorization reads.

Offline checks passed the exact report/action oracle for both scripted arms,
327 normal mock operations with no gaps, raw-result detection, inclusive cache
accounting, high-thinking/reasoning-content tool continuation through the HTTP
adapter, and nested-set serialization through durable recording. During paid
execution, each provider response was fsynced before the next request and each
artifact was written atomically. Final verification found 20 distinct attempts,
270 received provider responses, no replacement attempts, no oracle leakage,
zero real business effects, and unchanged frozen helpers and runtime trees.

Two first-request connection timeouts stopped the harness as configured:
direct repetition 5 and CodeMode repetition 7. After non-billable connectivity
checks, the remaining scheduled attempts resumed with identical hashes; neither
failed attempt was retried or replaced. Both have zero received model responses
and no returned usage. Their actual billing is unknown, rather than assumed zero.
The other direct failure (repetition 7) delivered an otherwise equivalent JSON
report followed by prose, violating the required standalone-JSON contract.

| Required report/action outcome | Direct | CodeMode |
| --- | ---: | ---: |
| Passes / scheduled attempts | 8 / 10 | 9 / 10 |
| Connection timeouts | 1 | 1 |
| Received response, invalid required report | 1 | 0 |

Eight ordinal pairs passed that required contract in both arms: 1, 2, 3, 4, 6,
8, 9 and 10. Costs below estimate received usage using DeepSeek's
[published rates](https://api-docs.deepseek.com/quick_start/pricing/); all response
timestamps fall outside peak hours. Input tokens already include cache hits,
and completion tokens are not increased by adding reasoning again. These are
model-cost estimates, not an invoice or worker-infrastructure cost calculation.

| Median across the eight matched pairs | Direct | CodeMode |
| --- | ---: | ---: |
| Inclusive input tokens | 635,730 | 300,075 |
| Output tokens | 8,223 | 4,822.5 |
| Estimated model cost | $0.04008 | $0.00611 |
| Elapsed time | 45.00 s | 38.55 s |
| Model responses | 11.5 | 17 |
| Uncached input tokens | 223,126 | 13,727.5 |

The **median of the individual paired reductions** is **52.0% for input,
84.4% for estimated model cost, and 17.6% for elapsed time**. These are medians
of per-pair ratios, not ratios of the independent medians in the table. Every
matched pair reduced input (44.8–72.2%) and estimated cost (83.8–87.1%). Most
of the cost difference comes from fewer uncached input tokens. This experiment
does not isolate how much came from stable discovery/catalog transport versus
code-side projection, or from function bindings versus the previous generic SDK.
The previous disabled-thinking cohort cannot supply that causal comparison.

More responses therefore did not imply more tokens, cost, or elapsed time.
Successful CodeMode runs received one or two framework correction messages;
the reported usage and latency include them. All nine performed real batching:
14–15 business reads across 4–6 read-containing programs, with 5–10 reads in
the largest batch. None satisfied the old monolithic-program target, which is
reported without treating it as a Cloudflare conformance requirement.

The quality and projection limits are material:

- **Required JSON correctness does not certify every narrative sentence.**
  Direct repetitions 3 and 4 overstate root organization-link counts; repetition
  8 understates anonymous links. CodeMode repetition 9 gives an invalid ID
  abbreviation in its final table, and repetition 10 gives an incorrect total
  qualifying-CSV count. Their required JSON reports remain exact under the
  frozen oracle. The CodeMode terminal work summaries retain the correct values.
- **Raw results still reached the model.** CodeMode repetition 6 fully returned
  a 15,995-byte permission response. The other eight completed CodeMode runs
  avoided complete top-level business responses above 10 KB but returned some
  smaller source containers, metadata, or partial values. The earlier chat update
  saying no exposure had been detected was too strong and was corrected.
- **No run passes the strict projection diagnostic.** Total `run_code` returned
  content was 25,330–37,982 bytes. Under the conservative classifier that counts
  ambiguous retained-state output as business-derived, the business/ambiguous
  count was 25,330–33,298 bytes, above the diagnostic 10,000-byte ceiling. Raw
  markers compare equal nested containers of at least 128 bytes; their nonzero
  counts do not by themselves prove a complete large response was exposed.

A post-hoc narrative sensitivity check leaves only three matched pairs without
identified prose contradictions (1, 2, 6). Their median paired reductions remain
53.3% input, 84.3% estimated cost and 11.9% elapsed, but that smaller sample is
not a replacement primary score or a strong whole-answer quality certification.
The original structured scores remain unchanged.

**Disposition:** the experiment demonstrates a substantial model-token and
model-cost advantage for this workload, including correction overhead. It does
not certify general Automatic/chat savings, perfect summary fidelity, or reliable
suppression of raw results. The implementation remains undeployed. The next
validation should address summaries derived from verified report data and
unnecessary raw returns, then cover ordinary chat and additional workloads
rather than extrapolating from this favorable inventory task.

Private reproducibility artifacts live in
`/home/jack/workspace/code-mode-production-benchmark-2026-10-23/function-bindings-benchmark/`:
`benchmark-protocol.json`, `function-bindings-launch-manifest.json`,
`function-bindings-offline-conformance.json`, the per-attempt results and durable
response captures, `analysis.json`, `final-receipt-verification.json`,
`final-evidence-review.json`, and `narrative-sensitivity.json`. Manifest SHA-256:
`cdf4a16d26bf22461b0f0b2ff91ba516dfabf59f6655d29507fdb81318b388ab`.
Runtime source SHA-256:
`0a2eb607ea0f9e9ab48ce02a45644eadb28d9bebd3e8d2479da49b2cf6c77991`.
Earlier experiment files, scores, and manifests remain unchanged. This turn
changes documentation only; no backend or frontend suite was rerun after the
previous 121 targeted tests and API quality checks. The benchmark's offline
conformance and final artifact-integrity checks were run as described above.

## Cloudflare source-conformance follow-up

A second source audit uses Cloudflare Agents commit
`321814b6c13f72cff82161e7eb20312574ac9b7a` (2026-10-23). It covers discovery,
generated contracts, connector adaptation, execution, result transformation and
output truncation. The benchmark above remains frozen at `4db7aaa79`; it does
not measure these follow-up changes.

The direct-function revision still had departures that could add model work:

| Boundary | Previous behavior | Follow-up behavior |
| --- | --- | --- |
| Search | Basic overlap ranking; eight results; silent cutoff; SDK envelope | Cloudflare field weights, normalization, phrase/prefix bonuses and coverage threshold; 50-result cap; direct `results`, `total`, `truncated` |
| Describe | One method; raw JSON schemas alongside Python declarations; SDK envelope | Direct method or connector documentation containing generated declarations; unknown targets return documentation saying not found |
| Return values | All JSON-looking tool strings decoded; native string/ref/union types discarded | Only explicitly marked Bifrost JSON/text adapters decode; native functions preserve their values and declared return schemas |
| Invocation failure | SDK validation, revocation and budget errors returned ordinary text | SDK-owned failures raise exceptions; source text receipts and JSON error-shaped data remain opaque source values |

References: Cloudflare's pinned
[search](https://github.com/cloudflare/agents/blob/321814b6c13f72cff82161e7eb20312574ac9b7a/packages/codemode/src/connectors/search.ts),
[describe](https://github.com/cloudflare/agents/blob/321814b6c13f72cff82161e7eb20312574ac9b7a/packages/codemode/src/connectors/describe.ts)
and [connector adaptation](https://github.com/cloudflare/agents/blob/321814b6c13f72cff82161e7eb20312574ac9b7a/packages/codemode/src/connectors/toolset.ts).
Connector describe includes only currently loaded, authorized tools. Combined
declarations use the native generator's conflicting-type-name resolution.

Deliberate platform adaptations remain: Cloudflare exposes JavaScript
`connector.method`; our search `path` is the actual flat Python callable alias,
with connector and original method available separately. We retain Monty and
the existing worker, live authorization, cancellation latch, call budgets and
outer result cap. Equal-score searches use stable Python code-point path
ordering rather than JavaScript locale-dependent ordering. Saved snippets and
Cloudflare's durable approval/replay engine are separate features, not added
by this change. No workflow-engine or customer sandbox installation change is
required.

The fixture `api/tests/fixtures/agent_runtime/cloudflare_search.json` was generated
by running the unmodified pinned TypeScript `searchConnectors` function using
Node 24's `stripTypeScriptTypes`, then serializing its results. Its 27 cases cover
exact/prefix matching, camelCase/separators, cross-field coverage, repeated tokens,
JavaScript rounding, empty/nonmatching queries, ranking and the 50-result limit. It records source
SHA-256 `15ef317d630b3df60e6cc48bb879d835d2a4e968214f0b4096265504364981e0`.
The port retains Cloudflare's MIT notice in `LICENSES/cloudflare-codemode-MIT.txt`.
These are source-parity tests, not a provider benchmark or a guarantee of identical
locale-sensitive sorting.

The source also limits what parity can promise. Cloudflare's
[`run-code.ts`](https://github.com/cloudflare/agents/blob/321814b6c13f72cff82161e7eb20312574ac9b7a/packages/codemode/src/run-code.ts)
returns the program's result and logs. In
[`proxy-tool.ts`](https://github.com/cloudflare/agents/blob/321814b6c13f72cff82161e7eb20312574ac9b7a/packages/codemode/src/proxy-tool.ts),
`transformResult` is optional; without it the raw result passes through before
model-output formatting. Its
[`truncate.ts`](https://github.com/cloudflare/agents/blob/321814b6c13f72cff82161e7eb20312574ac9b7a/packages/codemode/src/truncate.ts)
provides optional size reduction, not semantic selection or final-prose
verification. Our observed raw-return and prose mistakes therefore do not
identify a missing automatic Cloudflare filter. They remain quality limits to
evaluate across workloads before broad deployment.

A remaining contract limitation is distinct from runtime parity:
`execution/agent_helpers.py` gives system/workflow/MCP tools the shared text
adapter contract, and MCP catalog resolution currently consumes `inputSchema`
without carrying its `outputSchema` through as a structured success contract.
The frozen fixture likewise exposes generic return types. Cloudflare preserves
available output schemas and uses unknown when absent. This follow-up preserves
native schemas but does not invent field contracts for those adapters. A precise
adapter result contract must account for successful structured data, text and
source-specific failure receipts before output types can safely reach code;
that is a separate remaining improvement. It can reduce shape-inspection work,
but cannot guarantee correct final prose or automatic projection.

Focused verification passed **130 tests**, with zero failures, errors or skips.
The two existing E2E cases still exercise the worker dispatch and Automatic chat
history/audit boundary; no extra worker job was added. The workflow case spent
3.68 seconds in fixture setup, 1.66 in its request/worker path and 0.17 in teardown;
the chat call took 0.40 seconds. The full backend and frontend suites were not
run; no API DTO or frontend surface changed.

A separate network-disabled probe described the production-shaped frozen catalog:
21 root aliases before loading, all 24 specialists, all 237 callable aliases,
and all 25 connector groups. It checked generated declarations, returned paths,
and method/connector kinds, using ten explicit fresh native sessions. There were
zero provider calls and zero business callbacks. It uses the frozen composition
snapshot rather than live authorization reads; the Docker integration tests
cover the worker and authorization paths separately. Private script, results,
source hashes and verification manifest are in
`/home/jack/workspace/code-mode-production-benchmark-2026-10-23/cloudflare-conformance-catalog-probe-20261023/`.
The prior benchmark and catalog-probe directories remain unchanged.

Exact scoped test command:

```bash
BIFROST_TEST_IMAGE_TAG=code-mode-proof-1015 BIFROST_SKIP_BUILD=1 ./test.sh \
  tests/unit/services/agent_runtime/test_sandbox_search.py \
  tests/unit/services/agent_runtime/test_sandbox_sdk.py \
  tests/unit/services/agent_runtime/test_tool_policy.py \
  tests/unit/services/test_agent_code_mode_runtime_policy.py \
  tests/unit/services/test_agent_code_mode.py \
  tests/unit/services/test_agent_composition_stream.py \
  tests/unit/services/test_autonomous_agent_executor.py \
  tests/unit/services/agent_runtime/test_composition.py \
  tests/unit/services/test_agent_runtime_authorization.py \
  tests/e2e/api/test_agent_code_mode_spike.py \
  tests/e2e/api/test_agent_code_mode_chat.py -v --durations=0
BIFROST_TEST_IMAGE_TAG=code-mode-proof-1015 BIFROST_SKIP_BUILD=1 ./test.sh quality api
```

API quality passed: Pyright reported zero errors/warnings and Ruff passed.
The initial verification exposed a test snippet returning only inside an
`except` block; assigning the exception message and returning a top-level
projection repaired the test while retaining its no-effect assertion. Pyright
also required avoiding tuple indexes in the search first-token comparisons;
after that narrow refactor the three search tests, including all 27 upstream
cases, passed again with zero failures/errors/skips. Exact follow-up command:

```bash
BIFROST_TEST_IMAGE_TAG=code-mode-proof-1015 BIFROST_SKIP_BUILD=1 ./test.sh \
  tests/unit/services/agent_runtime/test_sandbox_search.py -v
```

A bounded independent read-only review found no actionable authorization or
dispatch regression. None of these checks measures model economics, validates
live production effects, or certifies full Cloudflare feature equivalence.
The candidate remains undeployed.

## MCP consolidation contract and required benchmark follow-up

The intended consolidation makes MCP and Automatic CodeMode consumers of the
same capability catalog, search/ranking, schema hydration and execution services.
Caller, organization, selected agent and approved composition scope remain
explicit inputs. A shared service must preserve those boundaries rather than
give an Automatic run the entire external gateway's discovery scope. Existing
worker execution and runtime audit/cancellation wrappers remain in use.
This consolidation is not implemented by `6d0f446c0`; that commit aligns the
sandbox SDK with the Cloudflare reference.

The external MCP experience retains its existing contract:

- `bifrost_search_capabilities` returns agents with `matching_tools`, stable
  `tool_ref` values and explicit counts/completeness fields. Selecting `agent_id`
  loads instructions; adding `tool_ref` loads the exact input schema. Shared
  ranking may change match order and relevance, without replacing the response
  with the sandbox's flat Python function catalog.
- `bifrost_execute_tool` keeps `agent_id`, `tool_ref`, `arguments` and optional
  `async`. Synchronous MCP calls return the selected tool's payload directly;
  asynchronous calls return an execution receipt consumed by
  `bifrost_get_execution`. The REST gateway's internal execution envelope is
  not the synchronous MCP response format.
- Required instructions, original caller authorization, execution ownership,
  organization reach and asynchronous defaults remain enforced.
- Output-schema information, when available, can be exposed additively during
  exact schema hydration. It describes the actual payload or nested structured
  content, not an unrelated transport envelope. Existing arbitrary workflow
  returns remain valid; no mandatory output annotation or new business envelope
  is introduced.

CodeMode bindings use the same resolved capability identities and services.
Their Python presentation is generated for the harness. This consolidation does
not itself require an additional public MCP code-execution tool; external
harnesses may compose the existing MCP calls in their own code environment.
An external client still making one call per model turn does not automatically
receive the savings from code-side batching and projection.

Meaningful runtime changes require a new provider-backed benchmark after focused
contract, authorization, worker and chat tests pass. The historical 84.4% median
estimated-cost reduction belongs to `4db7aaa79`; neither source-parity tests nor
the catalog probe establish that a later candidate retains it.

Before generating requests, freeze candidate sources, fixtures, oracle, attempt
counts, budgets, model/effort settings and pricing/accounting rules. Use fresh
matched comparisons against direct tools and the last measured CodeMode version,
with alternating execution order and identical business fixtures. Keep the
existing complex workload, add ordinary chat and small-tool tasks, and report
each workload separately so the favorable inventory case cannot hide overhead
elsewhere. Provider calls use mocks; no production business effects are allowed.

Quality checks cover required structured reports, authorized action receipts,
verification, final-prose totals and identifiers, plus known previous mistakes.
Batching, returned bytes and raw-data projection remain separate diagnostics.
Report success rates, failures/timeouts, inclusive input/cache/output usage,
estimated model cost, latency, corrections and cost per correct completion,
including unsuccessful attempts in cohort totals. Show matched successful-pair
results separately and disclose selection and sample-size limits. Do not replace
failed attempts or tune a cohort midway through execution.

Promotion requires demonstrated savings against the direct control and no
observed quality regression on the locked cases. Compare the previous CodeMode
candidate to detect lost savings; investigate regressions before promotion.
Record uncertainty rather than treating the historical percentage as a universal
guarantee or a small cohort as proof of statistical equivalence. A candidate with
unrun benchmarks remains explicitly unbenchmarked. No new provider calls were
made while recording this contract and verification requirement.

## Shared capability implementation

The implementation now shares capability routing, identity and schema validation
in `services/capabilities/catalog.py`, and Cloudflare-derived tool ranking in
`services/capabilities/search.py`. The existing `resolve_agent_tools` remains the
single database resolver. MCP renders grouped agent/tool responses; the sandbox
renders generated Python declarations and callable aliases from its currently
loaded view. Trusted metadata gives those views the same owner/tool search
inputs and stable source reference. Existing UUID5 references remain unchanged.
Agent-level discovery still uses MCP caller reach; the sandbox cannot use it to
expand its approved composition graph.

Chat and autonomous executors use the same source-route selection as the MCP
catalog. Their existing lifecycle handlers retain original-caller checks,
acting-user behavior, native delegation, knowledge budgets, workflow worker
execution, receipts and audit events. Composition additionally rechecks its
planned source reference after live path/binding/schema checks. Python aliases
remain presentation, never authorization grants.

External MCP output schemas now survive vendor catalog sync, stored tool
resolution, composition copies and toolset metadata. Exact MCP hydration exposes
optional `output_schema`; synchronous execution still returns the selected
payload directly. Sandbox declarations document the schema of successful
`result['structured_content']` separately, while keeping the surrounding
adapter result unknown. No payload fields are inferred, no customer return
annotation becomes mandatory, and no new output validator changes user results.

The forward migration `20261023_mcp_tool_descriptors` normalizes known historical
bare input schemas and `input_schema` descriptors to canonical `inputSchema`
descriptors. It preserves existing canonical/unknown descriptors, null-valued
schema defaults, and available output/description metadata. Fresh sync writes
the canonical descriptor directly. This also repairs a pre-existing mismatch
where sync wrote a bare input schema but resolution looked for `inputSchema`.

The candidate is rebased on `origin/main` at `4b56634e0`. Focused verification:

- The capability/MCP/SDK/catalog-sync set passed 130 tests, including shared
  reference/ranking equivalence, the existing reference UUID contract, exact
  hydration, output-type target, live gates, sync-to-resolver persistence and
  migration SQL against PostgreSQL.
- The broader existing executor/chat/worker/MCP-wire set selected 143 tests.
  Three failures exposed obsolete fixtures: two delegated without attaching the
  target agent, and an Automatic composition model still expected model-side
  `search_tools`. The fixtures now carry the actual grant and use sandbox
  discovery/direct bindings while retaining caller, workflow and provenance
  assertions. All 65 affected executor/composition tests passed after repair;
  the other 78 tests in that set already passed. No retries, skips, xfails or
  timeout increases were introduced.
- Sixteen additional context-breakdown and chat-artifact tests passed, bringing
  the distinct focused total to 289.
- API Pyright reported zero errors/warnings and Ruff passed. A bounded independent
  review found no actionable authorization or public-contract regressions.
- Existing MCP wire coverage verified the tool list and `async` argument,
  grouped progressive discovery, exact input schema, direct synchronous list
  payload, asynchronous receipt/status and revocation. Existing worker dispatch
  and Automatic chat tests passed. No new background job or test worker was added.

Commands used the existing worktree image, with
`BIFROST_TEST_IMAGE_TAG=code-mode-proof-1015 BIFROST_SKIP_BUILD=1`:

```bash
./test.sh tests/unit/services/test_capability_catalog.py \
  tests/unit/services/test_capability_search.py \
  tests/unit/services/test_capability_integration.py \
  tests/unit/services/agent_runtime/test_sandbox_sdk.py \
  tests/unit/services/agent_runtime/test_composition.py \
  tests/unit/services/mcp_server/test_gateway.py \
  tests/unit/services/test_agent_executor_mcp.py \
  tests/unit/services/test_autonomous_executor_mcp.py \
  tests/unit/services/test_mcp_client_catalog_sync.py \
  tests/unit/services/execution/test_agent_helpers_mcp.py -v
./test.sh tests/unit/services/agent_runtime/test_tool_policy.py \
  tests/unit/services/test_agent_code_mode_runtime_policy.py \
  tests/unit/services/test_agent_code_mode.py \
  tests/unit/services/test_agent_composition_stream.py \
  tests/unit/services/test_autonomous_agent_executor.py \
  tests/unit/services/test_agent_executor_tools.py \
  tests/unit/services/test_agent_tool_return_contract.py \
  tests/unit/services/agent_runtime/test_composition_discovery_wire.py \
  tests/unit/services/agent_runtime/test_composition_history.py \
  tests/unit/services/test_agent_runtime_authorization.py \
  tests/e2e/api/test_agent_capability_composition.py \
  tests/e2e/api/test_agent_code_mode_spike.py \
  tests/e2e/api/test_agent_code_mode_chat.py \
  tests/e2e/api-integration/test_mcp_gateway.py::TestMCPAgentGateway::test_default_and_agent_scoped_surfaces_are_distinct \
  tests/e2e/api-integration/test_mcp_gateway.py::TestMCPAgentGateway::test_live_discovery_schema_execution_and_revocation \
  -v --durations=0
./test.sh tests/unit/services/test_autonomous_agent_executor.py \
  tests/e2e/api/test_agent_capability_composition.py -v --durations=0
./test.sh tests/unit/services/agent_runtime/test_context_breakdown.py \
  tests/unit/services/test_chat_artifacts.py -v
./test.sh quality api
```

Full backend/frontend suites were not run. No API DTO field or frontend change
requires regenerated types. Benchmark results for this candidate follow only
after immutable source/harness freeze and the new three-arm provider cohort.
The older experiments and their percentages remain historical evidence.

## CI routing correction and corrected benchmark freeze

The first broad CI unit run found four failures (9,961 passed, 53 deselected).
One was a routing regression: an explicitly attached `search_knowledge` system
tool with no configured namespaces entered the specialized knowledge handler.
The shared catalog now requires a real namespace or explicit grant while keeping
its existing `knowledge:search_knowledge` identity and MCP reference. Chat and
background execution retain their original handler choice: specialized search
with namespaces, the system handler for an explicit binding without namespaces.
Three workflow-failure tests lacked the delegated-agent relationship now read by
shared classification; their fixtures now include empty relationships and keep
the execution-receipt and single-dispatch assertions.

The correction passed 226 focused tests with no failures/errors/skips, plus a
bounded independent review. After merging current `origin/main` (`cc5294490`,
executor heartbeat startup), 15 worker/chat/composition/hosting checks passed.
API Pyright again reported zero errors/warnings; Ruff passed. Commands used the
same test-image environment recorded above:

```bash
./test.sh tests/unit/services/test_agent_run_as_tool_input.py \
  tests/unit/services/test_agent_workflow_failure_receipts.py \
  tests/unit/services/test_capability_catalog.py \
  tests/unit/services/test_agent_knowledge_authorization.py \
  tests/unit/services/test_agent_executor_tools.py \
  tests/unit/services/test_agent_executor_mcp.py \
  tests/unit/services/test_autonomous_executor_mcp.py \
  tests/unit/services/mcp_server/test_gateway.py -v --durations=0
./test.sh tests/unit/test_pool_main_hosting.py \
  tests/e2e/api/test_agent_code_mode_spike.py \
  tests/e2e/api/test_agent_code_mode_chat.py \
  tests/e2e/api/test_agent_capability_composition.py -v --durations=0
./test.sh quality api
```

An earlier candidate cohort was stopped after 16 captured attempts when the CI
regression was identified. Its artifacts and response captures remain intact in
the private `consolidation-benchmark` directory and are explicitly incomplete.
One initial connection timeout there was retained, never replaced; absent usage
is not proof of zero provider billing. The corrected candidate receives a fresh
fixed 60-attempt cohort, without mixing results from the interrupted candidate.

The corrected freeze at source commit `d49a3bfbc` uses:

- Candidate source SHA-256: `bda274fdc54dc7826aaa2272b1fdd37a5dca17841b29dd26d7eb1fcf900f4e89`
- Shared source SHA-256: `e966e5729d79ef50eb85c148282c59cc746aeb501b42f2ae4617e08e7d04f9a9`
- Launch manifest SHA-256: `d55cf840f141381a702b90bb36001dfa6f272613d0d2012cc35129dd64dd1ff0`
- Passing offline receipt SHA-256: `676577155de186651cc8422745f45a1d1cc614edfa9c44e998c885bfb798d45f`

The private evidence directory is
`/home/jack/workspace/code-mode-production-benchmark-2026-10-23/consolidation-corrected-benchmark/`.
Nine actual Docker runtime jobs passed with networking disabled. Their receipt
binds the immutable image, runtime trees, executable helpers, protocol, workloads,
and exact artifacts/captures. The earlier partial offline diagnostic remains
separate. All workloads, model settings, budgets, attempt counts and scoring rules
are unchanged from the original locked three-arm plan. Paid launches require that
receipt; the finalizer rejects anything except the exact 60 planned paid jobs.
Final-prose review is recorded separately against artifact hashes.

## Corrected three-arm benchmark: 60 attempts

The corrected fixed matrix completed all 60 scheduled attempts on DeepSeek Flash
HIGH. Execution helpers, workloads, model settings and scoring remained frozen.
Four initial connection failures were retained (candidate complex r6, direct r7
and r9, baseline r8); none was replaced. Each had no captured response or business
call, but provider billing remains unknown. External connectivity diagnostics
were recorded before continuing only the unattempted scheduled jobs. This is
not a retry of failed attempts.

Final reports and hash-bound manual reviews are retained beside the manifest:
`cohort-final-locked-report.json`, `cohort-reviewed-report-v3.json`,
`cohort-final-reviewed-wrapper.json` and
`narrative-review-*.json`. They verify all planned IDs, source/image/helper and
passing offline-receipt hashes, durable provider captures, and artifact hashes.
The standalone wrapper binds the original locked report to the semantic-review
and workload-exclusion disposition, including all unknown-billing jobs.
Every business callback was a closed-world synthetic mock; actual executor calls
were zero. No customer or production action was performed.

For the complex inventory task, the all-attempt totals are:

| Measure (10 attempts per arm) | Direct tools | Previous CodeMode | Consolidated CodeMode |
| --- | ---: | ---: | ---: |
| Structured/action successes | 8 | 9 | 8 |
| Successes after final-prose review | 3 | 7 | 7 |
| Inclusive input tokens | 6,185,716 | 2,656,135 | 2,530,773 |
| Estimated captured-response cost | $0.292605240 | $0.051302538 | $0.051233100 |
| Captured cost per reviewed success | $0.097535080 | $0.007328934 | $0.007319014 |
| Median elapsed time, all attempts | 48.293 s | 40.007 s | 38.523 s |
| Framework corrections | 0 | 15 | 11 |

The consolidated candidate preserves the previous CodeMode result in this sample:
7/10 reviewed successes and nearly identical captured cost. Against direct tools,
its aggregate captured cost is 82.49% lower and inclusive input is 59.09% lower.
Those totals include every attempt, including failed ones, and are lower bounds
where connection-error billing is unknown. They are not success-matched savings
or statistical evidence of quality equivalence. Only one repetition succeeded
under all three arms after prose review; that sample is too small for a general
matched-performance claim.

Quality failures remain visible. Direct responses misstated anonymous or
organization link counts on five otherwise structured passes. Previous CodeMode
had two factual prose errors. The candidate had one wrong link count and one
incomplete private report (required `effective_access_verified` and `changes_made`
flags omitted, plus minor scope/type terminology). The latter remains a failure;
it is not repaired by forcing a new return envelope on arbitrary user code.
The candidate's structured score is 8/10 versus 9/10 for previous CodeMode, even
though both reviewed scores are 7/10. More repetitions and broader tasks are
needed before asserting equivalent quality.

Ordinary chat completed all 15 attempts with no business, discovery or code
calls. Semantic review passed 5/5 in every arm. The previous-CodeMode frozen
oracle passed only 4/5 because it required the literal phrase `no live actions`;
one equivalent response said that no specialist performed any live action and
there were no side effects. The frozen verdict is retained and the separate
semantic review records this lexical false negative. Candidate chat totals were
41,230 input tokens / $0.000848460, versus direct 36,530 / $0.000719436 and previous
CodeMode 41,350 / $0.000760260. Universal Automatic routing therefore has a small
fixed overhead on chat: this sample shows 12.87% more input and 17.94% more
captured cost than direct tools, with median latency 2.318 s versus 2.248 s.
It does not establish an automatic net win on every request.

The original 15 `simple_few_tool` attempts are retained as a scope-conflict stress
test, excluded from genuine few-tool cost and quality claims. Their wrapper asked
for one private note, but the required authoritative ticket context returned the
complex SharePoint assignment. All three arms pursued that conflicting task or
alternate reads, and all 15 failed the frozen strict oracle. This is a fixture
confound, not evidence about ordinary two-call overhead. A separately frozen
neutral-context supplement follows below; the original artifacts and verdicts
are not rewritten or replaced.

Costs use each captured response's timestamp and the published DeepSeek Flash
cache-hit, cache-miss and output rates. Input includes cached tokens; reasoning
is a subset of output and is charged once. Missing explicit cache-miss counts use
inclusive input minus cache hits, fixed and tested before execution. No uncaptured
failed request is assumed free. Latency includes complete attempted-run time;
short connection failures lower all-attempt medians and are listed explicitly.

The corrected source also passed the complete CI backend unit suite: 9,968 passed,
53 deselected, no failures. Required CI lint/types, client units, E2E and CodeQL
checks passed. Full local suites were not repeated; the focused Docker commands
above and CI provide the verification for this candidate.

## Neutral small-task supplement: 15 attempts

A separate supplement completed the original five-repetition, three-arm small-task
matrix with only the conflicting ticket context replaced by a neutral, complete
fictional note request. All arms see the same overlay, including alternate ticket
reads. The full 25-agent/237-tool catalog, task input, operating instructions,
DeepSeek Flash HIGH settings, source trees, image and budgets are unchanged.
This does not replace or repair any of the original 60 attempts.

Private evidence resides in
`/home/jack/workspace/code-mode-production-benchmark-2026-10-23/neutral-small-benchmark/`:

- Manifest `neutral-small-launch-manifest-v4.json`, SHA-256 `899ed3b305471d2c7ce00212f41e99fa02547de8537d1dd3769d7747a51c3cba`.
- Passing receipt `neutral-small-offline-selfcheck-v4/offline-self-check-receipt.json`, SHA-256 `9f432bd2bc6ead4a7f3b670d8e86231a1cc51412de04485e96bf8df9782a5745`.
- `neutral-small-final-locked-report.json`, `neutral-small-reviewed-report.json`
  and three `narrative-review-*.json` files bind all 15 actual artifacts/captures
  and independently reviewed verdicts to their hashes.

Three real Docker runtime checks passed offline with networking disabled before
paid execution. Exact-matrix, neutral-router, wrong-ID recovery, pricing and
continuation tests passed. The pre-launch continuation contract permits an
explicitly acknowledged existing failed artifact to be skipped, never rerun;
new errors stop further launches. Direct r2 then hit an initial 5.067-second
connection timeout with no captured response or business calls. Its billing is
unknown. After nonbillable DNS and unauthenticated host/pinned-container HTTPS
checks passed, the failure was acknowledged in the durable ledger and only the
remaining scheduled attempts ran. No failed attempt was replaced.

| Measure (5 attempts per arm) | Direct tools | Previous CodeMode | Consolidated CodeMode |
| --- | ---: | ---: | ---: |
| Frozen strict two-call successes | 0 | 1 | 1 |
| Independently reviewed semantic successes | 4 | 5 | 5 |
| Inclusive input tokens | 174,520 | 312,351 | 323,294 |
| Estimated captured-response cost | $0.002970048 | $0.005594850 | $0.006490236 |
| Captured cost per semantic success | $0.000742512 | $0.001118970 | $0.001298047 |
| Median elapsed time, all attempts | 6.140 s | 9.991 s | 10.607 s |
| Framework corrections | 0 | 4 | 3 |

The frozen strict oracle requires the specifically named `get_ticket_context`
function. Twelve executed attempts instead used `get_ticket`, whose neutral mock
returns the same complete context, then recorded the exact requested private
note. Independent review accepts those as semantic completions while retaining
every frozen strict failure. The other two executed attempts passed the strict
oracle. All 14 executed attempts had correct ticket/requester/outcome, exact note
strings, a successful terminal receipt, no customer message, no extra business
action and no definite final-prose contradiction. The remaining attempt is the
retained connection failure. These separate judgments are reported explicitly;
the original oracle and artifacts were not changed after launch.

On the four repetitions with semantic success under all three arms, candidate
median within-pair input was 50.30% higher, captured cost 109.03% higher and latency
64.84% higher than direct tools. Against previous CodeMode, the corresponding
increases were 8.08%, 17.20% and 5.60%. This small sample establishes overhead on
this two-business-call task, not a universal gain. Large-result composition
preserves the complex-task savings; ordinary chat and small tasks still pay for
the common Automatic execution surface. Universal Automatic routing is the
requested product behavior, with this tradeoff disclosed. No task-specific
routing fallback or model/provider change was added to hide it.

The candidate and previous CodeMode have the same reviewed complex-success
counts and neutral-task semantic-success counts in these cohorts. This supports
consolidation in the sampled tasks, but does not prove statistical equivalence
or lower cost on every request. The strict completion caveats, prose errors, retained
transport failures and excluded fixture remain part of the shipping evidence.

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
