"""Descriptive execution counts/rates, separate from capacity acceptance gates.

Event rates count events occurring in a half-open measured window, including
completions of warm-up arrivals. Cohort counts follow arrivals scheduled in
that window through drain. They answer different questions and stay separate.
"""

from __future__ import annotations

import math

import stats

TERMINAL = frozenset({"Success", "Failed", "Timeout", "Stuck", "CompletedWithErrors", "Cancelled",
                      "completed", "failed", "cancelled"})


def summarize(records: list[dict], window: tuple[float, float]) -> dict:
    start, end = window
    seconds = end - start
    if not all(type(value) in (int, float) and math.isfinite(value) for value in window) or seconds <= 0:
        raise ValueError("execution-rate window must have positive duration")
    identifiers = [record["id"] for record in records]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("execution-rate records require unique request IDs")

    def inside(value: float | None) -> bool:
        return value is not None and start <= value < end

    def http_accepted(record: dict) -> bool:
        status = record.get("http_status")
        return type(status) is int and 200 <= status < 300

    def terminal(record: dict) -> bool:
        return record.get("status") in TERMINAL and record.get("completed_s") is not None

    def correct(record: dict) -> bool:
        return terminal(record) and record.get("output_correct") is True and not record.get("duplicate_durable_rows")

    def group(rows: list[dict]) -> dict:
        cohort = [row for row in rows if inside(row["scheduled_s"])]
        counts = {
            "scheduled": sum(inside(row["scheduled_s"]) for row in rows),
            "sent": sum(inside(row.get("sent_s")) for row in rows),
            "http_accepted_responses": sum(http_accepted(row) and inside(row.get("responded_s")) for row in rows),
            "durable_created": sum(row.get("durable_found") is True and inside(row.get("created_s")) for row in rows),
            "durably_terminal": sum(terminal(row) and inside(row["completed_s"]) for row in rows),
            "correct_completed": sum(correct(row) and inside(row["completed_s"]) for row in rows),
            "correct_by_deadline_completed": sum(correct(row) and row["outcome"] == "correct" and inside(row["completed_s"]) for row in rows),
        }
        http_only = sum(http_accepted(row) and not row.get("durable_found") for row in cohort)
        durable_only = sum(not http_accepted(row) and row.get("durable_found") is True for row in cohort)
        both = sum(http_accepted(row) and row.get("durable_found") is True for row in cohort)
        cohort_counts = {
            "scheduled": len(cohort),
            "submitted": sum(row.get("sent_s") is not None for row in cohort),
            "accepted_union": http_only + durable_only + both,
            "http_only": http_only, "durable_only": durable_only, "http_and_durable": both,
            "neither_accepted": len(cohort) - http_only - durable_only - both,
            "durably_terminal": sum(terminal(row) for row in cohort),
            "correct_completed": sum(correct(row) for row in cohort),
            "correct_by_deadline": sum(row["outcome"] == "correct" for row in cohort),
            "correct_late": sum(correct(row) and row.get("deadline_met") is False for row in cohort),
            "failed_terminal": sum(terminal(row) and not correct(row) for row in cohort),
            "unfinished_durable": sum(row.get("durable_found") is True and not terminal(row) for row in cohort),
            "duplicate_request_ids": sum(bool(row.get("duplicate_durable_rows")) for row in cohort),
            "extra_durable_rows": sum(row.get("duplicate_durable_rows", 0) for row in cohort),
        }
        def distribution(values: list[float | None]) -> dict:
            observed = [value for value in values if value is not None and math.isfinite(value)]
            return {"observed": len(observed), "missing": len(values) - len(observed),
                    "p50": stats.percentile(observed, 0.50) if observed else None,
                    "p95": stats.percentile(observed, 0.95) if observed else None,
                    "max": max(observed) if observed else None}

        latency = {
            "admission_all_responses": distribution([row.get("admission_s") for row in cohort]),
            "queue_all_started": distribution([row.get("queue_s") for row in cohort]),
            "send_to_correct_terminal": distribution([
                row["completed_s"] - row["sent_s"] if correct(row) and row.get("sent_s") is not None else None
                for row in cohort
            ]),
        }
        return {"event_counts": counts, "event_rates_per_s": {key: value / seconds for key, value in counts.items()},
                "arrival_cohort": cohort_counts, "observed_latency_s": latency}

    return {
        "window_s": [start, end], "seconds": seconds,
        "scope": "top-level benchmark requests; nested agent tools are not additional offered requests",
        "event_semantics": "events in [start,end), including warm-up arrivals completing inside the window",
        "cohort_semantics": "arrivals scheduled in [start,end), reconciled through drain; not event throughput",
        "total": group(records),
        "by_class": {name: group([row for row in records if row["class"] == name])
                     for name in sorted({row["class"] for row in records})},
        "by_kind": {name: group([row for row in records if row["class"].startswith(prefix)])
                    for name, prefix in (("workflow", "wf-"), ("agent", "agent-"))},
    }


def platform_activity_sql(start: float, end: float) -> str:
    """Count actual platform work, including nested tools, in one DB clock window.

    This is status evidence, not expected-output correctness. Request rates
    above remain the denominator for the externally offered workload.
    """
    if not all(type(value) in (int, float) and math.isfinite(value) for value in (start, end)) or end <= start:
        raise ValueError("platform activity needs a finite positive window")
    lower, upper = f"to_timestamp({start:.9f})", f"to_timestamp({end:.9f})"
    return f"""
select json_build_object(
  'executions', coalesce((
    select json_agg(json_build_object('type', kind, 'status', status, 'count', count))
    from (select coalesce(w.type, 'unresolved') as kind, e.status, count(*)::int as count
          from executions e left join workflows w on w.id = e.workflow_id
          where e.completed_at >= {lower} and e.completed_at < {upper}
          group by coalesce(w.type, 'unresolved'), e.status) counts
  ), '[]'::json),
  'agent_runs', coalesce((
    select json_agg(json_build_object('status', status, 'count', count))
    from (select status, count(*)::int as count from agent_runs
          where completed_at >= {lower} and completed_at < {upper} group by status) counts
  ), '[]'::json),
  'execution_log_rows', (select count(*) from execution_logs where timestamp >= {lower} and timestamp < {upper})
)
"""
