# One owning agent with specialist capabilities

Bifrost chat and autonomous runs use Pydantic AI's existing model/tool/model loop.
The entry agent owns the conversation, evidence, final answer, budget and choice
of when to delegate. Its approved specialist agents also supply deferred
capabilities: the coordinator can load their instructions and call their tools
in the same run. An isolated child run is an explicit choice.

## Standard primitives and Bifrost adapters

| Concern | Framework primitive | Bifrost responsibility |
| --- | --- | --- |
| Iteration and tool results | `Agent`, `AbstractToolset` | Existing `BifrostToolset` and effect executors |
| Progressive instructions and tools | `Capability(defer_loading=True)`, `load_capability` | Adapt the approved agent graph and preserve tool ownership |
| Isolated delegation | Harness `SubAgents`, `delegate_task` | Bridge through the existing persisted child-run lifecycle |
| Budgets and context | Shared `RunUsage`, existing Harness capabilities | Keep parent/child ceilings, compaction and cancellation |
| Conversation continuation | Typed capability-load call/return history | Restore framework parts through existing chat message conversion |
| Effect evidence | Existing tool results and run steps | Keep workflow IDs, partial results and child timeout receipts |

No vector index is required for instructions. Deferred capability descriptions
are small model-visible discovery entries. Loading supplies the existing
specialist instructions and toolset through Pydantic AI. Instructions remain
versioned agent definitions rather than semantic search results. No parallel
catalog, custom reasoning loop, or process per capability is introduced.

## Selection and authority

At admission, `build_agent_composition` traverses active explicit delegation
edges, to the existing maximum depth of five. Each node must be executable by
the original caller and satisfy the existing parent/child organization rule.
The entry agent's tools retain their names. Specialist names are qualified by
source agent identity and original tool identity; equal names never merge
workflow IDs, MCP connections, or knowledge namespaces.

The snapshot is planning information, not an authorization grant. Before a
capability loads, before another request uses loaded instructions, and before
an effect dispatches, Bifrost rechecks the original caller and exact path.
Dispatch re-resolves the source agent's tool and verifies its schema and bound
workflow ID. Revoked edges, replaced bindings and schema changes fail closed.
The existing workflow, system-tool, knowledge and MCP executors perform their
normal authorization and execution checks. Chat artifact tools continue through
their existing conversation/caller checks.

A readable global agent does not grant global execution scope. Discovery cannot
turn an external HR caller into a provider Worker or grant a tenant absent from
the original resource authority. Empty reach-filtered domain reads do not
establish global absence. Those domain contracts require their own tools and
must not be inferred by this adapter.

## Same conversation or isolated child

Loaded specialist tools run in the owning conversation with its model and
budget. Their own source identity still controls execution. Specialist model
profiles apply when the coordinator intentionally chooses isolated delegation.
`delegate_task` uses the existing Bifrost child executor and durable `AgentRun`
record; it does not start a second framework-owned child model loop. Existing
`delegate_to_*` calls remain compatible and use that same lifecycle.

A failed child returns its run ID, status, partial output and existing sanitized
evidence, allowing the owner to provide missing context or verify an uncertain
effect before choosing the next action. This is ordinary model/tool iteration;
there is no automatic business-action retry policy. Loading a capability alone
creates no child run.

Workflow failures preserve the returned execution ID, status, error type,
result and details. Failure phases, completed/uncertain effects and verification
receipts are retained when the operation supplies them. Bifrost does not infer
effects from status, expose workflow variables/logs, or automatically replay an
uncertain write. Instance workflows still own truthful operation-specific
receipts and safe completion guards.

## Existing surfaces and durability

Agents, webhooks, schedules, chat and explicit child runs keep their existing
entry points and execution worker. Agent-scoped and gateway MCP discovery and
execution contracts are unchanged: external coding agents can continue using
the existing catalog and source-qualified tool references. Internal composition
uses the same source tool resolver and effect authorization boundaries; it does
not create another public MCP endpoint.

Chat records native load calls/results through the existing message/event
contract and reconstructs typed parts on a later turn. Autonomous cancellation,
child terminalization and timeout receipts retain the existing paths. This
change does not add autonomous crash-resume or duplicate the separate Agent
Workbench checkpoint/invocation implementation. Workbench integration must use
its existing snapshots and reconciliation boundaries when that work lands.

Logical runs are asynchronous objects within the existing worker; capabilities
and child adapters create no container, shell, filesystem sandbox, scheduler,
or process per session. Concurrent mocked-model sessions can measure this
adapter's memory behavior, but do not establish production RabbitMQ worker
capacity or model-provider throughput.

## Verification and usage

Deterministic framework tests cover deferred loading, direct specialist tools,
optional child execution, follow-up after child failure, chat stream/history
continuation, owner isolation and failure receipts. Database boundary tests
exercise current caller authority, graph/tool revocation, foreign organization
denial and source-bound dispatch through the real executor.

The private comparison uses frozen failures and intercepted business writes;
its catalog mocks are not authorization proof. Provider-reported input, output,
cache and reasoning usage and cost are retained per response. Request byte
breakdowns separate instructions, active/deferred schemas and history. Bytes
and cache-prefix diagnostics are not billed-token attribution. Provider timeouts
and unsupported fixture tools remain explicit limitations in scored results.
