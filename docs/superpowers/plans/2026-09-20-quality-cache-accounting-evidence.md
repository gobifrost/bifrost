# Quality Cache Accounting Evidence

Status: bounded read-only audit for accounting acceptance. No implementation approval is implied.

## Confirmed token and cost semantics

`api/src/services/llm/base.py::LLMResponse` and `LLMStreamChunk` expose `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, and optional `provider_cost`. `api/src/services/llm/pydantic_client.py` copies those fields directly from `response.usage.*` for streaming and non-streaming responses, and `api/src/services/agent_runtime/usage.py::provider_reported_cost` reads only `response.provider_details["cost"]` when the adapter preserved an exact provider cost.

The pinned dependency is `pydantic-ai-slim==2.35.3` in `pyproject.toml` and `requirements.lock`. Inspection of that exact wheel shows `pydantic_ai/usage.py::UsageBase.input_tokens` explicitly documents inclusive parent/child buckets: `input_tokens` includes `cache_read_tokens`, `cache_write_tokens`, and audio input tokens, and extraction normalizes providers where raw input excludes cache buckets. The same class defines `cache_hit_ratio` as `cache_read_tokens / input_tokens`. Provider adapters support this normalization through `RequestUsage.extract(...)`; OpenAI maps response usage through `models/openai.py::_map_usage`, Anthropic notes that `genai-prices` maps `cache_creation_input_tokens`/`cache_read_input_tokens` to cache write/read fields in `models/anthropic.py::_map_usage`, and Google maps `usage_metadata` through `models/google.py::_usage_metadata_as_usage`.

The current cost estimator is consistent with that dependency contract. `api/src/services/ai_usage_service.py::calculate_cost` starts with `regular_input_tokens = input_tokens`, prices up to `cache_read_tokens` and `cache_write_tokens` at cache-specific rates when configured, subtracts those tokens from regular input, then prices the remainder at the normal input rate. `shared/quality_usage.py::_estimate_complete_cost` delegates to the same calculator. The unit coverage in `api/tests/unit/services/test_quality_usage.py` expects a cached-token observation to price without double-counting the cache buckets as regular input.

`AIUsage.provider_cost` is the provider-observed cost when available; `AIUsage.cost` is either that provider cost or the local estimate. This is true in the legacy writer `api/src/services/ai_usage_service.py::record_ai_usage` and the strict quality writer `shared/quality_usage.py::record_quality_usage_observation`. New quality reporting separates those totals as `observed_provider_cost`, `estimated_cost`, `known_cost`, and `missing_cost_call_count` in `api/shared/quality_usage_reporting.py`.

## Cache ratio conclusion

Current reports can safely show raw `input_tokens`, `cache_read_tokens`, and `cache_write_tokens`. They can also compute a **token fraction** cache-hit ratio as `cache_read_tokens / input_tokens` for rows whose usage came through the pinned PydanticAI usage contract, because PydanticAI defines `input_tokens` as inclusive across supported adapters.

That ratio has narrower meaning than a request-level hit rate. It says what fraction of recorded input tokens were read from provider prompt cache; it does not say what fraction of requests had any cache hit, whether cache writes later became reads, or whether a provider/model supports prompt caching. Coverage also remains mixed for historical/legacy rows and non-PydanticAI writers: zero cache fields may mean no cache hits, unsupported cache reporting, or older call sites that never captured cache details. Reports should label the metric as a token fraction and keep raw counts/unknown coverage indicators rather than presenting it as a universal request cache-hit rate.

## Current reporting behavior

The legacy admin report in `api/src/routers/usage_reports.py::get_usage_report` sums `AIUsage.input_tokens`, `AIUsage.output_tokens`, `AIUsage.cost`, and call count. It does not expose cache-read/cache-write tokens, provider-cost versus estimate split, missing-cost rows, or quality usage attempt gaps.

The newer quality report path in `api/src/routers/usage_reports.py::get_usage_breakdown` calls `api/shared/quality_usage_reporting.py::summarize_quality_usage`. It reports raw cache counts, observed versus estimated cost, missing-cost calls, started/unobserved attempts, legacy coverage unknown, provider/model/profile/organization/operation breakdowns, and bounded pages. Tests in `api/tests/unit/services/test_quality_usage_reporting.py` and `api/tests/e2e/api/test_quality_usage_reporting.py` cover provider-cost rows, estimated rows, missing-cost rows, attempts without observations, tenant filtering, explicit org filtering, simulation/designer classification, and summary sequence profile handling.

## Call-site attribution map

Ordinary chat/runtime usage:

- `api/src/services/agent_executor.py::_record_ai_usage` records chat conversation usage through `record_ai_usage` with conversation/message/org/user attribution.
- `api/src/services/execution/autonomous_agent_executor.py` buffers runtime agent usage and flushes it through `record_ai_usage` with `agent_run_id`, sequence, duration, cache fields, and provider cost. A duplicate sequence guard exists for direct durable runtime usage recording.

Agent summarizer:

- `api/src/services/execution/run_summarizer.py` records sequence `0` rows on the source `AgentRun` immediately after a provider response and before parse. It writes only when both token counts are actual nonnegative integers, preserving cache fields and provider cost. The quality report classifies sequence `0` as `agent_summary` and `_runtime_profile_id_expr` only falls back to the agent execution snapshot when `AIUsage.sequence > 0`, so summary spend is not labeled with the agent runtime profile.

Test designer and synthetic simulation:

- Synthetic simulation runtime rows are ordinary `AgentRun` usage rows with `trigger_type='evaluation_synthetic'` and evaluation correlation. `api/shared/quality_usage_reporting.py::_usage_purpose_expr` classifies them as `simulation` and derives operation IDs from valid `correlation["evaluation_execution_id"]`.
- Designer rows are classified as `test_designer` only when the synthetic run has JSON boolean `correlation["evaluation_designer"] == true`; malformed legacy strings remain simulation/unknown. `api/tests/unit/services/test_quality_usage_reporting.py::test_synthetic_simulation_designer_descendants_and_missing_lineage` covers this.

Recorded semantic judge:

- `api/shared/agent_recorded_admission.py` opens a strict `AIUsageAttempt` before the provider call and records observed usage through `record_quality_usage_observation` afterward. It passes cache read/write tokens and duration when available.
- `api/shared/agent_recorded_judge.py::_usage_from_response` rejects missing, bool, or negative token counts and preserves duration on successful usage extraction. Tests in `api/tests/unit/services/agent_evaluations/test_recorded_semantic.py` cover negative-token rejection and duration preservation.

Synthetic semantic judge:

- `api/shared/agent_synthetic_judge.py` uses the strict quality writer for `synthetic_semantic_judge`. Its `usage_from_response` rejects missing/bool/negative input/output counts and cache count negatives, and passes cache fields to `record_quality_usage_observation`. Tests in `api/tests/unit/services/agent_evaluations/test_synthetic_judge.py` cover token rejection and malformed response accounting behavior.

Agent review:

- `api/shared/agent_reviews.py::execute_agent_review_job` begins a quality attempt before the call, records usage before verdict parsing, and marks missing/provider errors as unobserved. `usage_from_response` requires nonnegative integer input/output/cache counts and Decimal provider cost. Tests in `api/tests/unit/services/test_agent_reviews.py` cover cache fields, provider cost, duration, and invalid count rejection.

## Confirmed defects / gaps

1. Legacy admin usage reports omit cache and cost-source details. `api/src/routers/usage_reports.py::get_usage_report` cannot satisfy provider/cache accounting acceptance by itself because it drops cache-read/cache-write tokens and collapses provider-observed and locally estimated costs into `AIUsage.cost`.

2. Cache hit ratio needs precise labeling and coverage treatment. PydanticAI provides a normalized token denominator, so `cache_read_tokens / input_tokens` is valid as a recorded-token fraction for PydanticAI-backed rows. It is not a request hit rate, and historical/legacy rows with zero cache fields may still have unknown cache-reporting coverage.

3. Tuning dry-run and tune-chat overhead is still attached to the production source run as runtime `AIUsage`. `api/src/services/execution/dry_run.py::evaluate_against_prompt` writes sequence `8000` rows to the original `AgentRun`; `api/src/services/execution/tuning_service.py` writes tune-chat rows to `agent_run_id=run.id` without quality operation context. The quality report therefore classifies these as `runtime_agent`, not `test_designer`, `simulation`, or another overhead purpose. This is a confirmed attribution defect if tuning preview/chat spend must be separated from production runtime spend.

4. Legacy runtime writer remains best-effort. `api/src/services/ai_usage_service.py::record_ai_usage` catches and logs all exceptions and does not create an `AIUsageAttempt`, so provider responses with missing/invalid usage or recording failures are not represented as started/unobserved coverage gaps. The strict quality writer fixes this only for call sites that have moved to `AIUsageAttempt`.

No confirmed double-counting defect was found in the new quality cost estimator: cache token buckets are subtracted from PydanticAI-normalized inclusive input tokens before regular input pricing. No evidence was found that current quality reporting rebooks source production runtime as testing spend; it classifies synthetic simulation/designer rows and quality-operation rows separately, and leaves production rows as runtime.
