# Native CodeMode for Automatic agents

Automatic chat and background runs now use Pydantic AI Harness's native
`CodeMode`, backed by Monty. The duplicate Bifrost sandbox SDK, callable renderer,
discovery protocol, and dispatcher have been removed. This is the existing
AI 2.55.0 / Harness 0.55.0 / Monty 1.3.0 runtime, not a dependency upgrade.

## Shared execution and discovery

Both executors call `build_automatic_code_mode_capabilities()`. Its native
`ToolSearch` adapts the same authorized-capability ranking used by the MCP
gateway. Composed tools retain native deferred loading; a fixed, uncomposed
toolset is available immediately. A conversation with no tools and no composition
adds neither CodeMode nor discovery overhead.

Pydantic handles callable rendering, catalog updates, code execution, and model
correction. Bifrost supplies a small adapter for stored JSON tool definitions,
Python-safe internal names, result limits, cancellation, and its established
authorization and audit boundaries. Native search, capability loading, and child
delegation retain their framework lifecycle. In particular, `delegate_task` is
not folded into business code execution.

Nested business calls reach the existing toolset and executors. They still check
the current owner, organization, authorization, and input schema. Loading a
capability does not authorize subsequent execution. Workflows and external MCP
tools continue through their existing dispatch paths; there is no new workflow
engine, worker service, or execution queue.

## Return values and user contracts

Inside one `run_code` call, nested results remain complete so Python can filter,
join, and aggregate them. The single result returned to the model retains the
existing 32,000-character cap. Full execution receipts remain in the existing
audit path. Cancellation escapes the sandbox and the nested-result scope resets
before another model tool executes.

The nested transport adapter decodes JSON text once and otherwise preserves text.
It does not unwrap MCP envelopes, hide error fields, or impose a common success
shape on arbitrary workflow results. The original transport value remains in
audit events and outside CodeMode.

An explicitly known non-string JSON return schema can describe the nested value.
Unknown or incompatible schemas remain unknown. An external MCP structured-output
schema documents only successful `structured_content`, not every envelope or
error result. Tool authors do not need to change their return values.

External MCP users keep the existing capability search results, agent/tool
references, execution arguments, and response envelopes. The internal
model-facing Python aliases are presentation names; dispatch and audit map back
to source tool identities. No public DTO, CLI, manifest, or MCP gateway contract
changes in this patch.

## Sandbox and worker compatibility

Monty is a restricted Python interpreter supplied by the pinned dependency. It
does not require Bubblewrap, a shell, a container per snippet, or an operator's
local sandbox installation. It runs in the existing agent process on existing
workers. The code reaches only exposed tool functions; workflow execution remains
responsible for its own existing execution boundaries.

Each code call has a 10-second interpreter duration limit, 128 MiB memory limit,
128 suspension limit, and 16 nested tool-call budget. Up to three native model
corrections are allowed. Eager and speculative execution are disabled. These
limits bound orchestration; this is not a general-purpose shell or Python
environment with filesystem and network access.

## References

- [Pydantic CodeMode](https://pydantic.dev/docs/ai/harness/code-mode/): native
  Python callables, Monty execution, and tool orchestration. The implementation
  was checked against the pinned 2.55.0/0.55.0 source, since current documentation
  can describe newer APIs.
- [Cloudflare Code Mode](https://developers.cloudflare.com/agents/tools/codemode/how-it-works/):
  expose tools as callable functions and do intermediate processing in code.
  Bifrost uses that architecture with its Python runtime; Cloudflare's JavaScript
  worker substrate is not a prerequisite for this integration.

## Verification and benchmark

The frozen comparison ran 23 synthetic cases, three repetitions per arm, in
balanced order: 207 attempts, with no retries or replacement attempts. The arms
were the running Automatic integration, this native candidate, and native direct
tools without CodeMode. It compares complete runtime policies, not an isolated
measurement of one SDK function. The
[machine-readable receipt](../benchmarks/2026-10-24-native-automatic.json)
contains every attempt's artifact hash, outcome, and captured usage.

| All 69 attempts per arm | Current Automatic | Native Automatic | Direct Tools |
|---|---:|---:|---:|
| Requested data and required receipts correct | 66 | 68 | 67 |
| Input tokens captured | 1,653,574 | 981,051 | 3,186,945 |
| Output tokens captured, including reasoning | 71,440 | 38,759 | 51,986 |
| Cost from captured usage, USD | $0.068495274 | $0.040527084 | $0.151541778 |
| Model requests | 446 | 218 | 198 |
| Unsupported prose findings | 3 | 0 | 0 |
| Unauthorized effects observed | 0 | 0 | 0 |
| Requests with unknown billing | 1 | 1 | 0 |

The candidate used 40.7% fewer captured input tokens and 40.8% less captured
cost than current Automatic. Against direct tools, it used 69.2% fewer input
tokens and 73.3% less captured cost. The two requests without responses prevent
an exact full-bill comparison; these percentages describe captured usage only.
One timeout in each CodeMode arm remains in its 69-attempt denominator. The
candidate's sole data/receipt failure was its provider timeout. Current also
failed discovery once and omitted two mandatory private-report fields once.
Direct tools twice stopped at the shared budget boundary before recording the
large inventory report.

Code really reduced the data entering model history: unique model-visible tool
returns fell from 1,625,862 bytes with direct tools to 146,028 bytes with the
candidate (91.0%). Cumulative rereads of those returns fell from 5,620,684 to
801,791 bytes (85.7%). These are UTF-8 content measurements, separate from
provider token billing. Compared with current Automatic, the reductions were
39.7% and 41.2%, respectively.

### Workload differences

| Family, attempts per arm | Current Cost | Native Cost | Direct Cost | Native Input / Direct Input |
|---|---:|---:|---:|---:|
| No-tool chat, 9 | $0.000571 | $0.000559 | $0.000513 | 666 / 666 |
| Few tools, 9 | $0.010187 | $0.002485 | $0.001860 | 31,436 / 12,824 |
| Aggregation, 9 | $0.009401 | $0.007701 | $0.013561 | 92,193 / 83,014 |
| Dependent lookups, 12 | $0.011260 | $0.004394 | $0.003043 | 64,356 / 23,326 |
| Reports and large inventory, 9 | $0.020375 | $0.018954 | $0.128003 | 732,222 / 3,044,357 |
| Boundaries, 12 | $0.009978 | $0.004085 | $0.003194 | 30,341 / 12,461 |
| External MCP, 6 | $0.004752 | $0.001374 | $0.000919 | 17,970 / 6,531 |
| JSON-list/plain-text workflows, 3 | $0.001971 | $0.000976 | $0.000449 | 11,867 / 3,766 |

Captured cost was lower than the running Automatic implementation in each listed
family. It costs more than direct tools in several small-workload families.
The large report workload drives most of the aggregate savings. The no-tool
chat fixtures test the policy's bypass condition; normal Bifrost chat exposes
artifact tools and therefore follows the with-tools path.

Observed cache mix also affects cost. At uniform hypothetical cache pricing,
the candidate remains cheaper than direct tools: 66.5% less with all input
uncached and 35.7% less with all input cached. These are sensitivity analyses,
not additional bills. Captured cost uses the published DeepSeek Flash Saturday
off-peak rates: $0.15/M uncached input, $0.003/M cached input, and $0.60/M output.
Reasoning is already included in output tokens. See
[DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing/).

### Quality limits and adjudication

All preregistered data/receipt, safety, unsupported-prose, captured-cost, and
input-token gates passed. Independent final review resummed all raw provider
usage and checked the manifest, artifacts, frozen inputs, adjudication, and
source integrity without finding a blocker. This is evidence from three synthetic repetitions,
not proof of production quality equivalence, a production failure rate, or a
universal savings percentage. The model was DeepSeek Flash with high thinking;
paid Sonnet quality was not measured in this cohort.

Exact response formatting remains weaker than direct tools. Across 33 JSON
response attempts per arm, whole-message JSON parsed in 0 current, 6 candidate,
and 16 direct attempts. For the six attempts explicitly requesting exactly JSON
in the MCP/workflow cases, the counts were 0, 2, and 5. Correct data in a single
fenced JSON object counts toward data correctness, but does not satisfy a
JSON-only consumer. This migration improves the running Automatic baseline;
it does not establish strict JSON-output parity with direct tools. Tool return
values and public MCP execution contracts are separate from this final-answer
formatting behavior.

Two frozen grader mismatches received independent, hash-bound adjudication
without changing prompts, fixture returns, or the oracle: a candidate's correct
family counts were wrapped in a named object with metadata; a current response
used the correct device ID, explicitly mapped to its display name, where the
hidden oracle selected only the name. Neither prompt prescribed the rejected
representation. Original grader failures and strict-format failures remain in
the retained evidence. A review's nonexistent negative quote was also rejected
after checking the original artifact; it contributes no finding.

The three current unsupported-prose findings comprise one assertion that
unavailable broader knowledge queries had zero results, plus two incorrect
business-call counts. Missing data is not classified as unsupported prose.

### Reproduction and local verification

Retained bundle on the benchmark host:
`/home/jack/workspace/code-mode-native-candidate-2026-10-24/`.
Its frozen `manifest-v5.json` SHA-256 is
`21d3d1b6bdc1f842dda92da2e05ccdb3a1b55a8efd9f7f06adae08b66d61e5b7`;
the full reviewed report SHA-256 is
`6a69b8d41599fa062d9da38d70f46abea0192f014b26f3810d6ec2eafec890ab`.
Raw provider captures and original grading/review artifacts remain in that
bundle; the committed receipt is the compact public result record.

The image is pinned by digest, with read-only runtime snapshots and zero SDK or
transport retries. The private expected-outcomes file is mounted only for
network-disabled evaluation, not for agent execution. Eighty-one offline probes
passed before paid calls. A lost controller adopted one already completed atomic
artifact; two later provider timeouts were retained, and continuations executed
only original unattempted jobs. All 207 jobs have one start and one completion.

The source was rebased onto `a14568d63` before final verification. All changed
runtime files match the benchmark snapshot exactly; the rebase added unrelated
worker-version code. Live boundary tests passed after that rebase.

Focused unit and live boundary checks: 57 passed, including the final rebased
run. Additional authorization, runtime, discovery, and MCP consumers: 108 passed.
`./test.sh quality api` passed after the rebase. Five offline report-aggregation
tests validate missing evidence, hashes, literal findings, narrow adjudication,
byte accounting, and unauthorized effects. Broader backend/client suites were
not run locally; PR CI and the merge queue supply the complete-suite gate.

The focused command was:

```bash
./test.sh tests/unit/services/agent_runtime/test_automatic_code_mode.py \
  tests/unit/services/agent_runtime/test_code_mode_transport.py \
  tests/unit/services/agent_runtime/test_tool_policy.py \
  tests/unit/services/test_capability_integration.py \
  tests/unit/services/test_agent_code_mode.py \
  tests/unit/services/test_agent_tool_return_contract.py \
  tests/unit/services/agent_runtime/test_composition.py \
  tests/unit/services/test_agent_composition_stream.py \
  tests/unit/services/test_agent_code_mode_runtime_policy.py \
  tests/e2e/api/test_agent_code_mode_spike.py \
  tests/e2e/api/test_agent_code_mode_chat.py \
  tests/e2e/api/test_agent_capability_composition.py -v --durations=0

./test.sh tests/unit/services/agent_runtime/test_composition_discovery_wire.py \
  tests/unit/services/agent_runtime/test_composition_history.py \
  tests/unit/services/test_capability_catalog.py \
  tests/unit/services/test_capability_search.py \
  tests/unit/services/test_agent_runtime_authorization.py \
  tests/unit/services/test_agent_runtime.py \
  tests/unit/services/mcp_server/test_gateway.py -v

./test.sh quality api
```
