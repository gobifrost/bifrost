# Performance benchmark

Repeatable execution-time and memory measurements for a Bifrost build, on the
worktree's Kind cluster running the production image. Design:
`docs/superpowers/specs/2026-10-27-performance-benchmark-suite-design.md`.

```bash
./test.sh bench up                                   # Kind + production image + fake model + driver pod
./test.sh bench run --profile smoke                  # quick end-to-end check (~10 min)
./test.sh bench run --profile baseline               # baseline, fake model (deterministic)
./test.sh bench run --profile baseline --backend deepseek   # real model; key from 1Password
./test.sh bench down                                 # delete the cluster and the image
```

Each run writes `result.json` and `report.md` to
`/tmp/bifrost-bench-<utc>-<profile>-<backend>/` (or `--out DIR`). Compare runs
by diffing their reports or loading their `result.json`; the result records
the git SHA, image ID, profile and backend.

## What a run does

1. Shapes the stack from the profile: no scheduler, one API replica, the
   API's production limits (1 CPU, 1 GiB), production allocator env
   (`MALLOC_ARENA_MAX=2`), worker policy (API-launched worker Jobs).
2. Seeds idempotently: the bench workflows and tools, production-count
   workflows (`seed`), the `bench_docs` table, the model backend, and agents.
3. Restarts the API so it boots on the seeded data, then times every class in
   `classes` twice (sequential runs, then a concurrent burst), taking a memory
   snapshot after each class, then idles and snapshots again.

Times come from the platform's own timestamps (`created_at`, `started_at`,
`completed_at` on executions and agent runs), not from driver clocks. Memory
comes from outside the processes: the API's `/proc/1/smaps` (classified by
`smaps.py`) and the kubelet's per-container stats. No product code is needed,
so any build can be measured.

## Classes

| Class | What it measures |
|---|---|
| `wf-noop` | Platform overhead: dispatch, claim, result |
| `wf-sdk` | 5 config reads, 1 table query, 1 table insert through the API |
| `wf-wait` | A 2 s simulated external wait |
| `wf-cpu` | ~20 MB JSON build and parse |
| `wf-large` | ~5 MB result |
| `agent-chat` | One model turn, no tools |
| `agent-tools` | Direct mode: three workflow tools |
| `agent-codemode` | Automatic mode: the same tools through CodeMode |

## Model backends

- `fake` (default): `incluster/fake_llm.py`, a deterministic OpenAI-compatible
  model. Agent times measure the platform, not a model.
- `deepseek`: real DeepSeek. The key is read with the `op-personal` wrapper
  (profile fields `onepassword_profile` and `onepassword_item`, default
  `op://Development/DeepSeek/API Key`) straight into a Kubernetes Secret in
  the Kind cluster. It never touches disk, argv or the result.
- `none`: skip agent classes.

## Notes

- The production node uses transparent huge pages `always`; most workstations
  use `madvise`. Set `"thp_emulation": true` in a profile to ask glibc for
  huge pages on the API heap. It is recorded in the result.
- Host code is stdlib only. Tests: `python3 -m unittest discover -s scripts/bench/tests`.
