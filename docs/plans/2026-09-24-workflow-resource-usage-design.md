# Workflow resource usage: design for review

Status: proposed structure; no UI or API change is approved by this document.

## Job of the screen

An administrator should be able to identify an expensive workflow, find the runs responsible, and open an execution to investigate it. Every run is represented once, including runs with no AI calls. The existing AI Usage Reports page keeps its current scope and layout.

## Placement and screen structure

Add **Workflow resources** under Reports. Historical usage belongs beside other reports; the existing Diagnostics > Workers view continues to show current worker health.

```text
Workflow resources                                  [Last 24 hours ▾]
Find expensive workflows and inspect individual runs.

[All organizations ▾] [Search workflow name________] [Status: All ▾]

[ Runs ] [ By workflow ]

RUNS  (default: highest CPU time first)             [Sort: CPU time ▾]
┌────────────────┬───────────────────────┬─────────┬────────┬──────────┬──────────┬──────────────────────────┐
│ Started        │ Workflow              │ Status  │ Elapsed│ CPU time │ Memory   │ AI                       │
├────────────────┼───────────────────────┼─────────┼────────┼──────────┼──────────┼──────────────────────────┤
│ 22:40 UTC      │ build patch plan      │ Success │ 3m 00s │ 2m 48s   │ 113 MiB* │ $0 · 0 calls · 0 tokens  │
│ 22:15 UTC      │ build patch plan      │ Success │ 6m 32s │ 5m 12s   │ 118 MiB* │ $0 · 0 calls · 0 tokens  │
│ 19:24 UTC      │ scan backup health    │ Failed  │ 1m 42s │ 0m 31s   │  54 MiB* │ $0 · 0 calls · 0 tokens  │
└────────────────┴───────────────────────┴─────────┴────────┴──────────┴──────────┴──────────────────────────┘
Showing 1–50 of 288 runs                                      [Previous] [Next]

* Illustrative values. Memory label depends on the telemetry correction below.
```

The **By workflow** tab uses the same filters and shows one row per workflow: run count, failure count, total CPU time, longest elapsed time, highest measured memory, and total AI cost. It starts sorted by total CPU time. Choosing a workflow opens the Runs tab with that workflow filter applied. Choosing a run opens the existing execution details, including logs and error context.

On a narrow screen, each run becomes a card with the same metrics in reading order: workflow and status, started time and organization, elapsed/CPU/memory, then AI. Sort and filters stay above the cards.

## Definitions and data constraints

| Display | Definition |
| --- | --- |
| CPU time | Per-execution user + system CPU seconds; this is work done, not a CPU percentage. |
| Elapsed | Wall time from run start to completion. |
| AI | Calls, input + output tokens, and cost joined by execution ID. Zero-call runs show zeroes. |
| Memory | The existing `peak_memory_bytes` value is end-of-run PSS growth despite its name; it is not a true peak. `process_rss_bytes` is RSS at completion. Before a **Peak process RSS** column is introduced, label the existing values accurately. Historical runs without a true peak must show `—` for peak rather than a fabricated value. |

Capture a new per-run **Peak process RSS** value from the one-shot execution process's high-water RSS and retain the existing memory fields with their current meanings. Peak process RSS includes shared/runtime memory, so its purpose is ranking process pressure rather than attributing every byte to workflow code. For executions without an isolated one-shot process, leave this value unavailable. The report should say this in the column help text.

Both tabs use server-side filters, sorting, and pagination. The Runs response selects summary fields and per-execution AI aggregates; it does not load workflow results, variables, or logs for a table page. Resource fields remain platform-admin only. Missing metrics render as `—` and sort after measured values.

## Acceptance checks

1. A run with no AI calls appears once with CPU, elapsed, memory, and zero AI use.
2. Several AI calls in one run change its AI totals but do not multiply its CPU time or run count.
3. Sorting by CPU, memory, elapsed time, and AI cost produces stable results across pages.
4. Selecting a workflow in the rollup filters Runs, and opening a run reaches the existing execution details.
5. The current AI Usage Reports page and its filters, totals, and workflow table remain unchanged.
6. Historical memory values are not described as peak values.

## Decision requested

Approve the separate **Workflow resources** report with Runs as the default view and By workflow as the ranking/drill-down view, or choose a different placement or default view before UI implementation.
