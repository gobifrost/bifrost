# Workflow resource usage

Status: implemented in PR #803; revised after live review against the History page. The [review capture](../mockups/workflow-resources.png) comes from the isolated NetBird development stack with six sample workflow runs.

## Job of the screen

An administrator can identify workflows putting pressure on workers, compare the runs responsible, and open an execution to investigate. Every run appears once, including runs without AI calls. AI Usage stays the default report and retains its data, filters, and charts.

## Placement and interaction

- **Usage** has the same title-adjacent switch pattern as **History**: **AI Usage** and **Workflow Resources**. The title, switch position, and casing stay consistent between views.
- The Workflow Resources toolbar uses the existing `SearchBox`, `OrganizationSelect`, status `Select`, and `DateRangePicker`. Search applies as the user types. The date picker occupies the right side of the toolbar, as on History, and limits selection to the supported reporting window.
- **Runs** is the default view, sorted by most recent. **By Workflow** defaults to CPU Time. A workflow row filters Runs to that workflow. Clicking a run row opens its History detail; the workflow name remains a keyboard-accessible link. The shared `DataTable`, `RunStatusBadge`, and `PaginationFooter` match History's list behavior.
- Narrow screens keep the filters above the table and allow horizontal table scrolling. The workflow name and organization remain together.

## Columns

| Runs | By Workflow |
| --- | --- |
| Workflow, Status, Started, Duration, CPU Time, Average CPU %, Peak CPU %, Peak Memory, AI Usage | Workflow, Runs, Failed, CPU Time, Elapsed Time, Peak CPU %, Peak Memory, AI Spend |

**Peak CPU % by workflow** is the highest sampled peak among that workflow's runs in the selected window. **Peak Memory** is the highest per-run process memory high-water mark. Historical runs without either measurement show `—`; they do not become zeroes. The By Workflow rollup uses workflow ID and recorded name so same-named workflows remain separate.

## Measurement definitions

| Display | Definition |
| --- | --- |
| CPU Time | User plus system CPU seconds consumed by one execution, or summed across runs in the workflow view. |
| Average CPU % | CPU Time divided by elapsed seconds, expressed as a percentage of one core. 100% means one busy core; multi-core use can exceed 100%. |
| Peak CPU % | Highest CPU-rate sample for a workflow child, taken roughly once per second and at completion. Shorter spikes can be missed. |
| Duration / Elapsed Time | Wall-clock runtime, not processor work. |
| Peak Memory | Workflow child process RSS high-water mark on completed runs. A sampled RSS peak remains available if a child ends unexpectedly. This includes shared runtime memory, so it ranks process pressure rather than isolating workflow code. |
| AI Usage | Calls, input plus output tokens, and cost joined by execution ID. Zero-call runs show zeroes. |

The existing `peak_memory_bytes` field is end-of-run PSS growth despite its name, and `process_rss_bytes` is RSS at completion. The report uses the new `peak_process_rss_bytes` field for Peak Memory. Missing telemetry remains null. Resource fields are platform-admin only. The Runs response loads summary fields and per-execution AI aggregates without loading workflow results, variables, or logs.

## Acceptance checks

1. A run with no AI calls appears once with CPU, elapsed, memory, and zero AI use.
2. Multiple AI calls in one run change its AI totals without multiplying its CPU time or run count.
3. Search, date, organization, status, sorting, and pagination operate on the server's filtered result; the date query remains stable across renders.
4. By Workflow shows Peak CPU % and Peak Memory; selecting a workflow filters Runs, and clicking a run row opens History.
5. The AI Usage report's data and filters remain intact while the navigation follows the History page pattern.
6. Historical peak metrics are blank and the report explains the sample interval and one-core percentage.
