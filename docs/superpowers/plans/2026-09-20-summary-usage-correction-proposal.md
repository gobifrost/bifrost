# Summary Usage Correction Proposal

## Problem

`api/src/services/execution/run_summarizer.py` records the summarizer `AIUsage` row only after a response has been parsed and the run summary has been persisted successfully. Empty content, malformed JSON, non-object JSON, and other post-response failures set `summary_status='failed'`, but observed provider usage on the returned response is lost.

This undercounts billed summarizer overhead. Summary rows are intentionally `AIUsage(agent_run_id=<run>, sequence=0)` and roll into the existing source-run cost surface, including `AgentStats.total_cost_7d`. The correction must preserve that attribution and must not move summary calls into quality-operation accounting.

## Decisions

- Record observed summary usage immediately after `llm_client.complete(...)` returns and before inspecting or parsing content.
- Use one accounting block in the response-success path. Do not add per-branch fallback calls.
- Remove the old success-only usage block.
- Never dedupe summary usage by `(agent_run_id, sequence=0)`. A regenerate after a failed summary is a new billable provider call and must add a new `AIUsage` row. Existing `summary_status='completed'` early return already prevents no-call repeats.
- Write an `AIUsage` row only when both `response.input_tokens` and `response.output_tokens` are actual nonnegative `int` values and not `bool`.
- Do not use `or 0`. Missing or partial token counts remain a legacy coverage gap.
- Do not write a row when only `provider_cost` is present. The current `AIUsage` schema cannot honestly represent partial unknown token counts because token columns are non-null integers.
- Preserve actual cache token fields and `provider_cost` when a row is written.
- Keep `agent_run_id`, `organization_id`, and `sequence=0` source attribution unchanged.

## Smallest correction

In `run_summarizer.py`, add a small local validator/helper for observed response usage, for example `_summary_usage_tokens(response) -> tuple[int, int] | None`, that returns tokens only when both input/output counts are nonnegative non-bool integers.

After `response = await llm_client.complete(...)`, before `raw_content = response.content or ""`, open a short fresh DB session and call `record_ai_usage(...)` only when `_summary_usage_tokens(response)` returns counts. Use:

- `agent_run_id=run_id`
- `organization_id=org_id`
- `provider=getattr(llm_client, "provider_name", "unknown")`
- `model=getattr(response, "model", None) or resolved_model`
- exact input/output token counts from the validator
- `cache_read_tokens=response.cache_read_tokens`
- `cache_write_tokens=response.cache_write_tokens`
- `provider_cost=response.provider_cost`
- `sequence=0`

Then continue the existing parse/failure/success flow. The success path should mutate summary fields only; it should not record usage again.

## Known limitations after this fix

- If the provider bills a request but the transport raises before returning an `LLMResponse`, there is still no response usage to persist. This proposal does not add a durable summary attempt ledger.
- If the response omits either token count, no row is written, even when `provider_cost` is present. That is a known coverage gap caused by the current non-null token schema.
- `record_ai_usage(...)` remains legacy best-effort and can still swallow write failures. This correction fixes placement for observed complete usage, not strict runtime accounting.

## Tests

Add focused unit coverage in `api/tests/unit/test_run_summarizer.py`:

1. Malformed JSON with complete usage writes one `sequence=0` summary usage row and marks the run failed.
2. Empty content with complete usage writes one `sequence=0` summary usage row and marks the run failed with the existing actionable error.
3. Successful summary writes exactly one `sequence=0` row for one provider call.
4. Failed summary followed by regenerate/retry with another paid response writes two usage rows, one per provider response.
5. Transport exception before a response writes no usage row.
6. Response with missing/partial/non-int/bool token counts writes no usage row.

Focused verification:

```bash
./test.sh tests/unit/test_run_summarizer.py -v
```

## Files for implementation

- `api/src/services/execution/run_summarizer.py`
- `api/tests/unit/test_run_summarizer.py`


## Recorded judge measurement appendix

Recorded semantic judges now measure only the elapsed provider-call duration around the actual `client.complete(...)` call. The helper attaches nonnegative integer `duration_ms` to the existing observed usage dict before verdict parsing, so malformed verdicts preserve the measured call duration when token usage is otherwise recordable. Provider/config failures that return no response remain unobserved coverage gaps; this does not claim full platform job duration.
