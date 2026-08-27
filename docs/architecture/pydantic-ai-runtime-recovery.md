# Pydantic AI runtime and recovery policy

Issue: [#647](https://github.com/gobifrost/bifrost/issues/647)

This note covers the shared AI runtime after the Pydantic AI migration, the
boundaries between native provider transport and Bifrost-owned recovery, and
the terminal recovery paths we keep exposed to operators.

## Policy

Bifrost uses Pydantic AI for provider transport and message/tool execution, but
Bifrost still owns:

- authorization and persistence;
- recovery semantics above the transport layer;
- terminal run state;
- operator-visible summaries and backfills;
- no-fallback policy decisions.

Approved retry policy:

- Pydantic AI native transport is the sole model-request retry layer.
- Model requests retry at most 3 attempts total.
- Retryable statuses are 408, 429, 500, 502, 503, and 504; transport
  timeouts and connection failures are also retryable.
- When a 429 includes a positive `Retry-After`, honor it if the value is
  `<= 60` seconds.
- When `Retry-After` is missing, invalid, zero, or already expired, use bounded
  exponential jitter: the two fallback delays span 2-4 seconds and 4-6
  seconds. A valid value greater than 60 seconds is terminal at the transport
  layer rather than occupying a worker or retrying before the provider permits
  it.
- After the transport budget is exhausted, return the final HTTP response to
  the provider adapter so its status identity (especially 429) is preserved.
  Only genuine timeout and connection exceptions leave the transport as such.
- Streaming calls do not get a separate replay path; if a stream fails, the
  failure is surfaced rather than retried as a stream.
- Raw media POSTs are not model requests. Image/video upload and generation
  calls use their own request handling and do not participate in the model
  retry loop.

The runtime must not invent a second provider selection path or silently switch
to a different provider when the selected provider fails.

## Runtime split

The transport boundary is the Pydantic AI client layer:

- `api/src/services/llm/pydantic_client.py` adapts Bifrost's public LLM
  contract onto Pydantic AI.
- `api/src/services/agent_runtime/model_factory.py` chooses the provider-native
  adapter and preserves OpenRouter identity when the endpoint is OpenRouter.
- `api/src/services/agent_runtime/observed_model.py` records privacy-safe
  request shape and provider-visible usage without storing prompt contents.

Bifrost-owned recovery lives above that layer:

- `api/src/services/execution/agent_executor.py` and
  `api/src/services/execution/autonomous_agent_executor.py` own run budgets,
  one bounded correction for malformed tool output, and terminal run states.
- `api/src/services/execution/run_summarizer.py` owns summary terminalization
  and recovery bookkeeping, not a second outer retry loop.
- `api/src/services/execution/backfill_tracker.py` and the existing
  backfill/status surfaces own durable recovery for summarization work.

## Raw AI surfaces

These are the places where we intentionally talk to providers or provider-like
catalogs directly, so they should be treated as raw AI surfaces rather than
generic agent retries:

| Surface | What can fail | Intended behavior |
| --- | --- | --- |
| Embeddings | empty 200s, transient HTTP errors, bad endpoint config | Use the same bounded transport with SDK retries disabled; split empty batches; do not fall back to a different provider |
| Model discovery / catalogs | provider auth errors, 429s, unsupported model-listing endpoints | Return a clear test result; do not replace the selected provider |
| Pricing discovery | catalog fetch failures, malformed pricing payloads | Best-effort update only; stale pricing is better than broken writes |
| Raw media POSTs | provider-specific HTTP / transport failures on image/video upload or generation posts | Surface the provider failure to the caller; do not auto-switch providers or replay the binary request |
| Agent summarization | transient provider 429 / timeout / connection errors | Let the native transport handle retries; summarize_run records the terminal outcome and recovery path |

The media row is intentionally separate: raw image/video POSTs are not model
requests, so they are outside the native transport retry contract even though
they still talk to the same provider family.

## Idempotency rules

Recovery is idempotent only when the data model says it is:

- `summarize_run` is idempotent once `summary_status == "completed"`.
- Backfill messages are deduplicated through the existing summarization queue
  and the completed-summary guard.
- Agent runs are terminal when they are `completed`, `failed`, `cancelled`, or
  `budget_exceeded`; recovery means a new run or a resumable handoff, not a
  hidden restart of the same terminal row.
- Bifrost permits one bounded retry for malformed tool sequencing in the agent
  runtime, but not an unbounded provider retry loop.
- Current AgentRun cleanup only terminalizes stale runs; it does not replay or
  resurrect the prior execution.

## No provider fallback

Do not turn a provider failure into a different provider selection decision.
That includes:

- falling back from OpenRouter to stock OpenAI when the selected OpenRouter
  route is rate-limited;
- falling back from a custom endpoint to stock OpenAI for embeddings;
- silently swapping catalog sources after a pricing or model-listing failure.

The selected provider or endpoint is part of the contract. If it fails, the
caller sees the failure and the operator decides whether to retry, repair the
configuration, or use a separate recovery path.

## Observability

We want failures to be visible without exposing prompt text or keys:

- `ObservedModel` records request shape, not raw content.
- `AgentRunStep` and `AgentRun.summary_error` make terminal failure states
  inspectable.
- `AIUsage` records the token/cost ledger for completed model calls.
- Summarization recovery publishes updates through the existing run-update
  surfaces so admins can see progress without polling a custom endpoint.

The main diagnostic distinction is this:

- transport failure means the provider or endpoint failed;
- terminal failure means Bifrost has exhausted its bounded recovery and the row
  is now awaiting operator action;
- resumable recovery means the user can rerun the same logical task through the
  dedicated recovery surface.

## Ticket 432431

Ticket `432431` was recovered by ticket-specific Halo lease/watchdog logic for
an idle assigned ticket. It is not a summarization retry incident, and it does
not generalize to the broader AI runtime.

The follow-on AgentRun cleanup only terminalizes stale runs without replaying
them. That is intentionally narrower than the 432431 ticket path and should not
be treated as a generalized recover-and-rerun mechanism.

## Operator runbook

1. If summarization fails transiently, rerun the existing recovery surface
   rather than adding a second retry loop or reprocessing the whole
   conversation manually.
2. If an embedding or catalog call fails, fix the saved provider/endpoint
   configuration and retry the original action.
3. If a run is terminal, use the terminal recovery path for that surface
   instead of reopening the same row.
4. If the same failure repeats, capture the exact provider, endpoint, model,
   and error shape before changing policy.

## Remaining limitations

- The runtime still depends on provider-specific behavior for rate limiting and
  retry headers.
- Direct OpenRouter media-catalog and pricing GETs remain best-effort raw HTTP
  calls and do not yet share the model-request transport.
- Raw media POSTs do not yet retry even an explicit 429. They remain terminal
  until those legacy HTTPX calls have provider-supported idempotency keys or a
  dedicated Pydantic AI transport that cannot replay an accepted submission.
- Some raw AI surfaces still return generic provider errors rather than
  structured error codes.
- Catalog and pricing discovery are best-effort by design, so stale data is
  acceptable when the provider is unavailable.
- This policy does not define a new global provider failover system.

## Related code

- `api/src/services/llm/pydantic_client.py`
- `api/src/services/agent_runtime/model_factory.py`
- `api/src/services/agent_runtime/observed_model.py`
- `api/src/services/execution/agent_executor.py`
- `api/src/services/execution/autonomous_agent_executor.py`
- `api/src/services/execution/run_summarizer.py`
- `api/src/services/embeddings/openai_client.py`
- `api/src/services/provider_catalog_service.py`
- `api/src/services/model_pricing.py`
