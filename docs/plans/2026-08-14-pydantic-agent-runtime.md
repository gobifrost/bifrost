# Pydantic agent runtime migration

Date: 2026-08-14  
Issue: [#594](https://github.com/gobifrost/bifrost/issues/594)  
Baseline: [`9cdebfc32`](https://github.com/gobifrost/bifrost/tree/9cdebfc320d3bde6670e3524e6a1178b5f7cc9b9)

## Decision

Bifrost will use Pydantic AI plus Pydantic AI Harness as its shared agent execution runtime. Bifrost continues to own authorization, durable runs, tools, MCP dispatch, conversation persistence, WebSocket events, and its public SDK/CLI/REST contracts. The runtime owns the provider-neutral model loop, message/tool sequencing, context compaction, request/token limits, and bounded recovery from malformed model output.

This is a runtime replacement, not a replacement for Bifrost's AI product surfaces. AI Complete, chat, run summarization, tuning, MCP, knowledge search, vector embeddings, and SDK consumers retain their existing Bifrost contracts.

## What the Triage audit established

The provider API is stateless: every model call must send the context needed for that call. Resending the system prompt, tool schemas, and retained conversation history is normal. The former harness made that normal cost unusually expensive by allowing many calls, appending every assistant/tool exchange, warning only on iteration count, enforcing the token budget after a request and its tools had already completed, and giving delegated children independent usage ledgers.

The representative Triage run had nine model calls, 100,181 input tokens, and 4,081 output tokens. Input grew from approximately 9,313 tokens on call one to 13,960 on call nine. A reconstruction from the available prompt, tool definitions, run input, and persisted steps apportioned the 100,181 input tokens approximately as follows:

| Repeated request category | Approximate cumulative input | Share of input |
| --- | ---: | ---: |
| Agent and platform system instructions | 52,800 | 52.7% |
| Tool schemas | 18,100 | 18.1% |
| Initial run input | 12,900 | 12.9% |
| Accumulated assistant calls and tool results | 16,300 | 16.3% |
| **Total** | **100,100** | **100%** |

These are reconstructed category estimates, not provider-tokenized raw payload fields. The source run did not retain privacy-safe per-category request measurements. The new runtime records serialized byte counts for each category and the provider's actual input/output tokens, which lets production reports calculate stable category shares without storing prompt or tool-result contents.

The 22,944-character live prompt plausibly accounts for roughly 5,000–7,000 tokens per request depending on tokenizer. Repeating it nine times therefore explains much of the run, but not all of it. The tool schema and growing tool transcript are material, and the former post-request budget check allowed the last call to cross the configured 100,000-token ceiling.

## What was sent before the migration

For each autonomous iteration, the former executor sent:

- the full agent system prompt plus the autonomous-mode suffix;
- the initial JSON run input and optional output schema;
- every retained assistant response and tool call from the current run;
- every retained tool result from the current run; and
- the complete resolved tool-definition list as request metadata.

It did not send the parent's entire transcript to a delegated child. A child received its own system prompt and tools plus a compact task input containing `task` and `_delegated_from`. The costly delegation defect was accounting: each child started a fresh token/iteration counter and could therefore expand the total subtree beyond the parent's configured budget.

Caller identity remains an authorization input to tool resolution and dispatch; it is not automatically copied into the model prompt. Dates are likewise not injected by the execution harness. Attachments are not a separate always-repeated request category in autonomous execution; when an attachment or device lookup is returned by a tool, its serialized result becomes ordinary retained tool history.

The exact baseline loop is visible in [`autonomous_agent_executor.py`](https://github.com/gobifrost/bifrost/blob/9cdebfc320d3bde6670e3524e6a1178b5f7cc9b9/api/src/services/execution/autonomous_agent_executor.py#L178-L415): request count was checked by the `while` condition, the warning was appended at 80% of iterations, cumulative usage meant input plus output tokens, and token enforcement occurred only after the model response and tool execution. Child-run creation stored the child's limits but did not inherit a usage ledger ([baseline lines 789–830](https://github.com/gobifrost/bifrost/blob/9cdebfc320d3bde6670e3524e6a1178b5f7cc9b9/api/src/services/execution/autonomous_agent_executor.py#L789-L830)). Chat had a separate custom loop and heuristic summarization path ([baseline `agent_executor.py`](https://github.com/gobifrost/bifrost/blob/9cdebfc320d3bde6670e3524e6a1178b5f7cc9b9/api/src/services/agent_executor.py#L330-L652)).

## Implemented runtime

The shared runtime is divided into narrow boundaries:

- `agent_runtime/model_factory.py` selects native OpenAI/OpenAI-compatible, Anthropic, or Google Pydantic models. OpenRouter remains an OpenAI-compatible endpoint and model name.
- `agent_runtime/toolset.py` exposes Bifrost's stored JSON schemas without regenerating them and delegates execution back to Bifrost's existing authorization-aware tool paths.
- `agent_runtime/budgets.py` creates pre-request usage limits, a shared parent/child ledger, proactive wind-down warnings, oversized-tool spillover, deterministic tool-result clearing, and a sliding context window.
- `agent_runtime/observed_model.py` records a privacy-safe request composition breakdown, uses native provider token counting when available, and conservatively estimates the next request only for preflight budget protection.
- `llm/pydantic_client.py` adapts the existing `BaseLLMClient` contract so non-agent consumers migrate provider transport without changing SDK or API contracts.
- `agent_executor.py` and `execution/autonomous_agent_executor.py` now use Pydantic's native loop and event stream instead of maintaining independent model/tool loops.

### Budget semantics

The configured token budget is cumulative input plus output across the root run and every delegated descendant. Pydantic's `RunUsage` object is shared through the delegation tree. A child also receives a local ceiling derived from its own configured allowance, but that ceiling can never exceed the inherited parent ceiling.

Limits are evaluated before a provider request with token counting enabled. When a provider cannot count locally, the observed-model boundary estimates serialized messages and tool schemas at two bytes per token plus structural overhead; this intentionally fails closed for ordinary prose and JSON. The estimate is predictive only: it can trigger wind-down or prevent the next request, but it is never persisted as actual usage. A provider's eventual output cannot be known in advance, so the preflight check counts accumulated provider usage plus the estimated next input; the returned provider input and output counts are then charged before any later request. Request count, the one allowed malformed-tool correction, and delegated model calls all consume the same ledger.

At 70% of either request or total-token budget, the model receives a wind-down instruction. With two requests remaining, the instruction becomes critical. The intended experience is a technician-style handoff: state what was tried, identify remaining blockers, finish notes, and leave resumable progress. If the hard guard still fires, chat returns a context warning and a resumable handoff rather than silently disappearing.

### Context policy

The active-request target is 24,000 tokens. This is a cost-control target, not the provider context-window maximum. The standard capability chain:

1. spills a newly oversized tool result to a temporary result store and leaves a retrieval handle;
2. clamps pathological individual messages;
3. clears older tool results while keeping the three newest result pairs; and
4. retains a recent sliding window plus the first user request.

The system prompt and tool schemas are still present on every provider call because providers need them to follow instructions and call tools. The runtime reduces the avoidable growing-history component; shortening an unusually large agent prompt remains a separate, high-value prompt-design task.

## Compatibility and coding-agent path

The public `BaseLLMClient`, message, stream-chunk, tool, chat, run, and configuration DTOs remain intact. Provider selection adds Google but does not remove OpenAI-compatible endpoints, native OpenAI, or Anthropic. Embeddings and vector storage are untouched. MCP schemas and dispatch remain Bifrost-owned.

The Coding Agent branch can consume the same model factory, shared budget, observed model, and Bifrost toolset. Coding-specific behavior should be expressed as a capability/tool profile—workspace tools, patch application, command execution, approval boundaries, and a longer context target—not as another model loop. That gives Code Builder a mature native coding harness without coupling ordinary support agents to coding dependencies.

## Rollout and regression gates

1. Complete unit and contract verification for runtime, chat events, autonomous tools, delegation, MCP identity, LLM configuration, DTO parity, and contract fingerprints.
2. Run a local OpenRouter smoke test with a cheap model and a secret injected through the work 1Password account; never store the plaintext key in source or shell history.
3. Deploy behind the existing worker release boundary and compare a small Triage cohort with the historical 80-run sample.
4. Gate wider rollout on lower cumulative input per successful run, zero parent-budget escapes, preserved tool success rate, and no increase in incomplete handoffs.
5. Merge the shared runtime into the Coding Agent worktree, then implement its coding capability profile there.

The original local OpenRouter gate completed against `deepseek/deepseek-v4-flash`, but its 3,074 input and 401 output totals were conservative Bifrost estimates rather than provider usage. A follow-up live trace proved that OpenRouter returned exact usage while Pydantic AI's generic usage extractor silently reduced it to zero. Bifrost now requests OpenRouter usage explicitly and preserves those returned fields directly. The patched factory's live canary recorded 9 input and 22 output tokens; the old local input estimate for the same request was 131 tokens. The credential was injected transiently from the work 1Password vault and was not stored in source or runtime configuration.

Required production telemetry per model request:

- root run ID and child run ID;
- absolute request number and cumulative input/output/total tokens;
- provider-reported input/output tokens and duration;
- serialized bytes by system prompt, historical user messages, current user input, assistant history, tool results, other history, and tool schemas;
- a tool-schema SHA-256 fingerprint and schema count;
- compaction tier/action, bytes or tokens removed, and spill handles created;
- budget warning/hard-stop reason and remaining requests/tokens; and
- retry/resume/delegation depth.

No prompt text, tool-result text, API key, or attachment content should be included in these metrics.
