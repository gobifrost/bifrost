# Agent Platform framework comparison

Reviewed 2026-09-19 against the durable Agent Platform branch. This is a capability comparison, not a comparative performance benchmark.

## Where Bifrost fits

Bifrost combines a durable Agent runtime with tenant authorization, published workflow tools, existing execution workers, shared PlatformJobs, a run debugger, and a reviewed synthetic evaluation workflow. Its advantage is that these controls and evidence live together in the platform. It also owns the maintenance burden of its PostgreSQL lease, checkpoint, wake, and completion protocols.

| Area | Bifrost | Relevant comparison |
| --- | --- | --- |
| Durable execution | PostgreSQL authority, fenced claims, committed checkpoints, released workers during waits, resumable runs, reconciliation and completion outbox | Pydantic AI documents durable integrations with execution backends; LangGraph uses persistent checkpointers and records successful parallel-node writes so they need not rerun after another node fails. |
| Debugging | Run tree, ordered journal, checkpoint/snapshot inspection, contract/lease/wake/completion evidence | LangGraph includes checkpoint-based replay and history forks. Bifrost v1 intentionally does not expose arbitrary historical replay/forking. |
| Evaluation | Frozen suites/cases/candidates; isolated stateful mock tools; baseline/candidate comparisons; reviewed history-derived drafts; explicit production diff | Pydantic Evals provides Python datasets, evaluators and experiments, including deterministic and model-based evaluation and trace evaluation. Bifrost adds the integrated operator workflow and authorization boundary. |
| Cache observability | Persisted input/output and cache read/write counts, per-run and evaluation token-reuse fraction, runtime costs separated from summary costs | OpenChamber exposes OpenCode usage. Its token denominator differs from Pydantic AI; copying its formula would double-count Bifrost cached input. |

References: [Pydantic AI durable execution](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/), [LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers), [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence), [Pydantic Evals](https://pydantic.dev/docs/ai/evals/evals/).

## Caching: existing behavior, newly verified visibility

Caching was present before the durable-runtime implementation: OpenRouter requests already carried session affinity, Anthropic had prompt-cache settings, and AI usage recorded cache reads/writes and cache-aware pricing. This work improves durable usage recovery and exposes runtime-only measurements. The implementation lives in [model settings](../../api/src/services/agent_runtime/model_factory.py), [runtime capabilities](../../api/src/services/agent_runtime/budgets.py), and [evaluation evidence](../../api/src/services/agent_evaluations/evidence.py). It does not introduce OpenChamber or OpenCode into the platform.

The mechanisms that help are a reusable prompt prefix, provider-specific cache controls, and consistent provider routing. Bifrost keeps the run's session ID across attempts and supplies provider cache settings through its model factory. OpenRouter documents `session_id` as an explicit sticky-routing key, including activation before the first observed hit. Routing affinity is not a cache guarantee: model support, prefix changes, expiry, and provider availability still matter. Separate evaluation cases do not receive a promise of shared cache reuse. [OpenRouter prompt caching](https://openrouter.ai/docs/guides/best-practices/prompt-caching).

Pydantic AI's input-token count includes cached input. Bifrost therefore uses `cache_read_tokens / input_tokens`, returning null when no input was observed. OpenChamber's OpenCode utility treats `input` as uncached and divides cache reads by `input + cache.read + cache.write`. Both can describe token reuse under their own provider contracts; they are not interchangeable. [Pydantic AI usage](https://pydantic.dev/docs/ai/api/pydantic-ai/usage/), [OpenChamber token utility](https://github.com/openchamber/openchamber/blob/main/packages/ui/src/stores/utils/tokenUtils.ts).

Bifrost also uses Pydantic AI Harness `WarnOnCacheBusts` with a 1,024-token threshold. The warning is observational: it cannot reliably distinguish prefix changes from expiration, and its high-water marks are process-local. Persisted usage remains the evidence across worker restarts. OpenChamber's optional managed-OpenCode prompt optimizer shortens prompts; its existence is not evidence of a particular cache-hit improvement. [Harness cache warnings](https://pydantic.dev/docs/ai/harness/warn-on-cache-busts/), [OpenChamber prompt optimizer](https://github.com/openchamber/openchamber/blob/main/packages/web/server/lib/system-prompt/DOCUMENTATION.md).

A live OpenRouter `openai/gpt-4.1-mini` repeat-prefix test observed 3,594 input tokens on each request: zero cache reads first, then 3,456 cache reads, or **96.16%** token reuse. Two short synthetic baseline cases each made two model calls and reported zero cache reads. These results prove that eligible requests can hit cache and that zero hits remain visible. They do not establish fleet-wide hit rate, savings, or a performance advantage over another framework.

## Operational limits and evidence

A live run slept for 45 seconds, its worker was restarted, and it automatically resumed under the same run ID on attempt 2, completing with one logical tool invocation and no manual enqueue. That validates the tested restart path. Durable execution still does not imply exactly-once external side effects: uncertain writes require reconciliation or explicit recovery rather than blind replay.

The next useful cache comparison would hold model/provider, prompt and tool schemas, routing, TTL, and workload constant, and measure cold/warm requests plus resumed runs. Changing prompts merely to imitate another harness would confound that measurement. No representative fleet benchmark was performed in this implementation review.

OpenCode was used only as a local coding contributor during development. OpenChamber was read only as a comparison source. Neither is a Bifrost runtime dependency or integration.
