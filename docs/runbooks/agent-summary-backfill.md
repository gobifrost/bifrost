# Agent summary backfill

Post-run summarization fills `asked` / `did` / `confidence` / `metadata` on every `AgentRun` via a short LLM call. The backfill tool regenerates summaries for runs where `summary_status != 'completed'` — useful after:

- **Migration** (old runs predate the summarizer and have `summary_status='pending'` with no worker ever having picked them up).
- **Prompt / model change** (the summarization system prompt or chosen model was updated and you want old summaries re-derived against the new config).
- **Transient failures** (a batch of runs has `summary_status='failed'` because the summarization provider was briefly down).

## Who can trigger it

Platform admins only. The endpoint is guarded by `is_superuser || Platform Admin || Platform Owner`.

## How to trigger it

### Platform-wide

Fleet page (`/agents`) → **Backfill summaries** button in the header → confirm the count + estimated cost.

### Per-agent

Agent detail page (`/agents/:id`) → **Backfill pending summaries** in the header → confirm.

### Via API

```bash
curl -X POST https://<host>/api/agent-runs/backfill-summaries \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent_id": null, "statuses": ["pending", "failed"], "limit": 5000, "dry_run": true}'
```

`dry_run: true` returns `{eligible, estimated_cost_usd, cost_basis}` without enqueuing. Remove it to actually start the backfill — the response includes `job_id`.

## Progress tracking

The UI subscribes to WebSocket channel `summary-backfill:{job_id}` for live `succeeded / failed / total / actual_cost_usd` updates. Admins can also poll:

```bash
GET /api/agent-runs/backfill-jobs/{job_id}
GET /api/agent-runs/backfill-jobs?active=true
```

## Cost

Every regenerated summary writes one `AIUsage` row linked to its parent `AgentRun`. `AgentStats.total_cost_7d` sums those rows — so after a backfill the Spend (7d) card on each affected agent moves by roughly `per-summary-cost × N`. The backfill confirmation dialog shows an estimate: average cost of the last 100 completed summaries × eligible count. If no summarizer history exists yet, a flat $0.002/run fallback is used (labelled "flat estimate; no history" in the dialog).

## Concurrency / queue flooding

Each run is staged as a durable dispatch in PostgreSQL (`work_dispatches`, handler `agent.summarize_backfill`). Every executor runs one backfill summary at a time beside one live summary, so a backfill never delays live summaries, and `summarize_run` is idempotent on `summary_status='completed'`, so a 5,000-run backfill is safe from both a correctness and a provider-rate-limit standpoint. To watch the backlog drain:

```sql
SELECT count(*) FROM work_dispatches WHERE handler = 'agent.summarize_backfill';
```

## Kill switch

To stop the UI from showing progress on a still-draining job:

```sql
UPDATE summary_backfill_jobs SET status = 'failed' WHERE id = '<job_id>';
```

This does **not** cancel the queued dispatches — executors will continue draining them. The per-run summarizer outcome is independent of the job row; flipping the job status only affects progress reporting.

## Testing locally

```bash
./test.sh tests/e2e/api/test_backfill_summaries.py -v
```
