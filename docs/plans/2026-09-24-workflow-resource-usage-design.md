# Workflow resource usage: design for review

Status: proposed structure; no UI or API change is approved by this document.

## Job of the screen

An administrator should be able to identify an expensive workflow, find the runs responsible, and open an execution to investigate it. Every run is represented once, including runs with no AI calls. The existing AI Usage Reports page keeps its current scope and layout.

## Placement and screen structure

Add **Workflow resources** as a new tab on the existing Reports > Usage page, beside **AI usage**. Keep AI usage as the default tab and retain its current data and layout. The existing Diagnostics > Workers view continues to show current worker health.

```text
Reports > Usage     [AI usage] [Workflow resources]  [Last 24 hours ▾]
Find expensive workflows and inspect individual runs.

[All organizations ▾] [Search workflow name________] [Status: All ▾]

[ Runs ] [ By workflow ]

RUNS  (default: highest CPU time first)             [Sort: CPU time ▾]
┌────────────────┬───────────────────────┬─────────┬────────┬──────────┬──────────┬──────────────────────────┐
│ Started        │ Workflow              │ Status  │ Elapsed│ CPU time │ Avg CPU  │ Peak CPU │ Memory │ AI    │
├────────────────┼───────────────────────┼─────────┼────────┼──────────┼──────────┼──────────────────────────┤
│ 22:40 UTC      │ build patch plan      │ Success │ 3m 00s │ 2m 48s   │ 93% core │ —        │ —      │ $0    │
│ 22:15 UTC      │ build patch plan      │ Success │ 6m 32s │ 5m 12s   │ 80% core │ —        │ —      │ $0    │
│ 19:24 UTC      │ scan backup health    │ Failed  │ 1m 42s │ 0m 31s   │ 30% core │ —        │ —      │ $0    │
└────────────────┴───────────────────────┴─────────┴────────┴──────────┴──────────┴──────────────────────────┘
Showing 1–50 of 288 runs                                      [Previous] [Next]

* Illustrative values. Historical runs have no sampled peak CPU or true peak memory value.
```

The **By workflow** tab uses the same filters and shows one row per workflow: run count, failure count, total CPU time, longest elapsed time, highest measured memory, and total AI cost. It starts sorted by total CPU time. Choosing a workflow opens the Runs tab with that workflow filter applied. Choosing a run opens the existing execution details, including logs and error context.

On a narrow screen, filters wrap above a horizontally scrollable table. The workflow name and organization stay together, and each metric retains its column label.

## Definitions and data constraints

| Display | Definition |
| --- | --- |
| CPU time | Per-execution user + system CPU seconds; this is work done, not a CPU percentage. |
| Average CPU | CPU seconds divided by elapsed seconds, displayed as a percentage of one core. 100% means one busy core; multi-core use can exceed 100%. |
| Peak sampled CPU | Highest CPU-rate sample for the workflow child process, taken roughly once per second and at completion. A run without a valid sample shows `—`. |
| Elapsed | Wall time from run start to completion. |
| AI | Calls, input + output tokens, and cost joined by execution ID. Zero-call runs show zeroes. |
| Memory | The existing `peak_memory_bytes` value is end-of-run PSS growth despite its name; it is not a true peak. `process_rss_bytes` is RSS at completion. Historical runs without a measured process memory peak show `—`. |

Capture a new per-run **Peak process RSS** value from the workflow child process. The pool forks a fresh child for each workflow execution, so the child's `ru_maxrss` is its true process high-water mark. Sample current RSS and CPU roughly once per second and at completion; the highest RSS sample remains available if a child times out or crashes before reporting its own high-water mark. The measurement includes shared runtime memory, so it ranks process pressure rather than attributing every byte to workflow code. If no measurement is available, show `—`. Retain the existing memory fields with their current meanings, and explain the sample interval in the report.

Both tabs use server-side filters, sorting, and pagination. The Runs response selects summary fields and per-execution AI aggregates; it does not load workflow results, variables, or logs for a table page. Resource fields remain platform-admin only. Missing metrics render as `—` and sort after measured values.

The By workflow rollup uses the workflow ID and recorded name, so two workflows with the same name remain separate. Historical executions without a workflow ID are grouped by their recorded name.

## Acceptance checks

1. A run with no AI calls appears once with CPU, elapsed, memory, and zero AI use.
2. Several AI calls in one run change its AI totals but do not multiply its CPU time or run count.
3. Sorting by CPU, memory, elapsed time, and AI cost produces stable results across pages.
4. Selecting a workflow in the rollup filters Runs, and opening a run reaches the existing execution details.
5. The current AI Usage Reports page and its filters, totals, and workflow table remain unchanged.
6. Historical memory values are not described as peak values.

## Decision requested

Approved in conversation: a new **Workflow resources** tab on Reports > Usage, with Runs as its default view and By workflow as the ranking/drill-down view. AI usage remains the default page tab.
