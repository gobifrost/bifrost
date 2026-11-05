# Agent runs on workers: before and after (2026-10-28)

Agent runs and chat turns (`agent.run`) moved from the API process to workers.
Each run executes in a child forked from a warm agent template, and the child
exits when the run ends
(`docs/superpowers/specs/2026-10-27-agent-runs-on-workers-design.md`). This
compares the same benchmark profile before and after.

**Reproduce:**

```bash
./test.sh bench up
./test.sh bench run --profile baseline --backend fake
./test.sh bench run --profile baseline --backend deepseek   # key via op-personal
```

## Conditions

Same topology, data and method as `2026-10-27-baseline.md`: one API replica at
1 CPU / 1 GiB, `MALLOC_ARENA_MAX=2`, no scheduler, worker Jobs at 2 CPU / 2 GiB
(warm 1, max 2). Each class ran 10 runs alone, then three bursts of 10 at once,
in two passes.

- **Before:** `main` at `1fab1d0db`, rerun with the current harness, which adds
  the two chat classes, database load and worker process memory. That's why
  these numbers differ slightly from the 2026-10-27 baseline.
- **After:** this branch, including the agent template's warm model catalog and
  price snapshot.
- Every one of the 1,600 runs per backend succeeded, before and after.

## Execution times (end-to-end p50, ms)

| Class | Fake: alone | Fake: 10 at once | DeepSeek: alone | DeepSeek: 10 at once |
|---|---:|---:|---:|---:|
| `agent-chat` | 123 → 361 | 681 → 1,300 | 1,398 → 1,811 | 1,857 → 2,408 |
| `agent-tools` | 632 → 942 | 4,000 → 4,433 | 2,831 → 3,614 | 4,756 → 5,225 |
| `agent-codemode` | 670 → 1,002 | 4,274 → 4,508 | 3,092 → 3,959 | 5,746 → 5,992 |
| `chat-first` | 109 → 395 | 569 → 1,442 | 1,072 → 1,367 | 1,685 → 2,340 |
| `chat-followup` | 98 → 411 | 608 → 802 | 1,057 → 1,305 | 1,483 → 1,741 |

Workflow classes were unchanged within run-to-run noise.

Agent runs are 0.25–0.9 s slower. A forked child starts with empty
per-process caches and opens its own database, Redis and model-provider
connections, which the long-lived API process reused across runs. Runs also
share the worker's 2 CPUs with the workflow runs their tools start. Reducing
child start-up cost is a follow-up change, measured on its own.

## CPU per run (ms)

| Class | API before → after | Worker before → after |
|---|---:|---:|
| `agent-chat` (DeepSeek) | 244 → 214 | 25 → 743 |
| `agent-tools` (DeepSeek) | 534 → 417 | 212 → 943 |
| `agent-codemode` (DeepSeek) | 620 → 503 | 248 → 1,054 |
| `chat-first` (DeepSeek) | 328 → 190 | 24 → 596 |
| `chat-followup` (DeepSeek) | 446 → 300 | 42 → 1,117 |

The API still spends 200–500 ms per agent run on summaries (which stay in the
API by design), tool workflow dispatch and SDK calls. The worker's figure
includes the child's start-up and the agent loop that used to run in the API.

## Memory (MiB)

| Point | Before | After |
|---|---:|---:|
| API, fresh | 348 | 317–318 |
| API, settled after all classes (fake / DeepSeek) | 418 / 435 | 419 / 426 |
| Worker Job, agent template retired | 75–86 | 78–91 |
| Worker Job, agent template warm | n/a | 278–290 |
| Agent template, private | n/a | 198–202 |
| Agent run child, peak private | n/a | 62–85 |

- The API no longer imports the agent runtime at start-up (about 30 MiB less
  when fresh). Settled memory moves little because summaries still load the
  model client and catalog in the API.
- Agent memory is released: each child exits with its run. In this version
  the template retired after 5 minutes with no agent run alive; it is now
  always loaded (see "Production follow-up").
- Child peaks are sampled every 0.5 s, so they are a lower bound. The
  `agent.run` memory request is 96 MiB, the measured peak plus margin.

## Database

Transactions per run rose by 7–17 for agent classes (for example
`agent-tools` 124 → 129, `chat-followup` 82 → 99 with DeepSeek), from the
worker claim and child connection set-up. The idle rate was 30–31
transactions/s in all four runs. DeepSeek's after-run showed idle WAL of
4.2 KiB/s against 1.8 before, but the fake-model pair showed 1.9 against 2.1;
the idle database work is addressed separately
(`docs/superpowers/plans/2026-10-28-idle-database-churn.md`).

## Production follow-up: the agent template is always loaded

After deploying, the first agent runs in production waited about 11 s: the
template took 10.2 s to build (15:08:14 to 15:08:24 UTC). Built alone on a
production worker it takes 7.3 s, 7.06 s of it CPU, almost all imports:

| Import (production worker) | Seconds |
|---|---:|
| `pydantic_ai.models.openai` (with the OpenAI SDK's types) | 3.0 |
| `src.jobs.agent_run_child` (database layer, MCP server, file storage) | 1.9 |
| `src.services.agent_executor` | 1.1 |
| Model catalog and price snapshot | 0.6 |

The same imports take 5.1 s locally on one CPU, so this is their real cost,
not a production fault. With a 5-minute idle retire, 52% of the previous
week's 296 production agent runs would have paid it. The template now starts
with the worker and stays loaded, like the workflow template: a worker idles
at about 280–290 MiB instead of about 80 MiB, and no run waits for the
template.

