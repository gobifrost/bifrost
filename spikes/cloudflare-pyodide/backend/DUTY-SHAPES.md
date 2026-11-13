# Duty shapes: long-running waits and data providers

Measured 2026-11-12 on Cloudflare (isolated worker pair), spike executor at `627bc65aa`,
runtime bundle `06aeb2c87`. Complements the short-workflow benchmark suite and the agent run.

## Long-running wait/poll workflows

Shape: workflow polls an authenticated endpoint on an interval, holding one invocation open.

| Polls × interval | Engine duration | Round trip | Terminal | Child subrequests |
|---|---:|---:|---|---|
| 8 × 35 s | 246.1 s | 254.9 s | Success | 10 / 12 |
| 9 × 40 s | 321.2 s | 332.5 s | Success | 11 / 12 |

- Every poll returned 200; poll offsets tracked the requested interval (0, 40.1, 80.2 … 321.1 s).
- No edge timeout at 100 s: the `workers.dev` path held the response to 332 s.
- **The bound is the subrequest budget, not wall time or CPU.** At `subRequests: 12`, a 10th
  poll reaches the cap once module resolution and the entry-module load are counted. Raising
  the executor's subrequest allowance (paid plans allow up to 10k) is the lever, not timeouts.
- `asyncio.sleep` did not consume the 1000 ms CPU budget — a 5.35-minute mostly-idle
  invocation completed without `CpuLimitExceeded`.

### New runtime finding

`time.process_time()` raises in Pyodide:
`RuntimeError: the processor time used is not available or its value cannot be represented`.
Workflow code that measures its own CPU this way fails outright. Neither the workflow nor the
spike may rely on in-runtime CPU measurement; provider CPU metrics remain `null`.

### Fixture caveat, not a product claim

The direct qualification executor admits a declared `timeout_seconds <= 30` for fixture
reasons and does not enforce `workflow_deadline` (`workflow_deadline: null` in the receipt).
Production deadline enforcement is a dispatcher concern and is not proven by this run.

## Data providers

`@data_provider` module executed through the same path:

| Invocation | Engine duration | Round trip | `cached` | User code ran |
|---|---:|---:|---|---|
| miss | 280 ms | 13.2 s | false | yes |
| hit | 99 ms | 9.1 s | true | no |

- The engine takes the data-provider branch (`is_data_provider` from tags), reads/writes the
  server-owned cache through the run credential, and on a hit returns the cached result
  without executing the user function (`execution_context` and `variables` are null).
- Default provider timeout is 300 s (`api/src/services/execution/module_loader.py`), matching
  the Linux shape. Workflows have no equivalent cache layer.

## Consequence for the routing boundary

Data providers and multi-minute polling workflows are viable on Cloudflare. The practical
eligibility rule is therefore **subrequest-shaped, not duration-shaped**: estimate the
workflow's request count and require it to fit the allowance, rather than assuming a short
wall-clock ceiling.
