# Benchmark Phase 1 (Baseline) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One command (`./test.sh bench run --profile baseline`) that deploys today's build on Kind, seeds production-shaped workflows, times eight execution classes (workflows and agents) with a fake model or DeepSeek, snapshots API and worker memory after each class, and writes `result.json` + `report.md`.

**Architecture:** A stdlib-only host orchestrator (`scripts/bench/`) drives the existing Kind harness and talks to the cluster only through `kubectl`. Everything that needs Bifrost's Python (HTTP driver, fake model) runs in pods on the Bifrost image, so the host needs no packages. Memory is read from outside the processes (`/proc/<pid>/smaps` via `kubectl exec cat`, kubelet `/stats/summary`), so the harness measures any build, old ones included, with no product change.

**Tech Stack:** Python 3 stdlib (host), Bifrost API image (in-cluster: httpx), Kind via `scripts/kubernetes/local-kind.sh`, kubectl.

**Spec:** `docs/superpowers/specs/2026-10-27-performance-benchmark-suite-design.md` (sections 7.2a, 8, 9 E0/E1, 13 Phase 1).

## Global Constraints

- Topology under test: API, Client, Worker. **No scheduler** (scaled to 0). **One API replica** (production runs one).
- Allocator settings match production: `MALLOC_ARENA_MAX=2` on API and worker. The THP emulation knob (`GLIBC_TUNABLES=glibc.malloc.hugetlb=1`) is a profile field, recorded in the result.
- No product code changes in Phase 1.
- DeepSeek key: read from 1Password at run time, stored only in a Kubernetes Secret in the Kind cluster; never written to the repo, the result, logs, or argv.
- Missing measurement sources fail the run; no substitute measurements.
- Host side is stdlib-only; host tests run with `python3 -m unittest discover -s scripts/bench/tests`.
- Each run cleans up what it created except the cluster (`down` removes the cluster and the image).

## Review Focus

1. A class whose runs fail (agent misconfigured, tool not registered) must fail the class visibly with the error, not report fast "times" from failed runs. → driver test in Task 4: failed runs are counted and excluded from timing; a class with any failure is marked `failed`.
2. DeepSeek key absent while the profile asks for `deepseek` → the run stops before deploying anything, naming the 1Password item. → Task 6.
3. Re-running `run` on an existing cluster must not double-seed or collide on names. → seed is idempotent (Task 4).
4. Memory snapshot taken while a worker Job is mid-exit or the API pod restarted → snapshot records pod UID and restarts; a changed API pod UID mid-run fails the run. → Task 2.
5. Burst concurrency above worker capacity queues work; queue wait must come from `started_at − created_at`, not driver time. → Task 3 stats tests.

---

### Task 1: smaps classifier tests

**Files:** `scripts/bench/smaps.py` (exists), `scripts/bench/tests/test_smaps.py`

- [ ] Tests: a fixture smaps text with one `[heap]` mapping (Rss 1000 kB, AnonHugePages 512 kB), two adjacent 1 MiB anon mappings, one 64 kB anon mapping, one `/usr/lib/x.so` mapping, one untouched anon mapping. Assert regions `heap`, `anon_large`, `anon_small`, `file`, `anon_untouched` totals and `summary.thp_kib == 512`.
- [ ] Run `python3 -m unittest discover -s scripts/bench/tests`; commit.

### Task 2: cluster adapter

**Files:** `scripts/bench/cluster.py`, `scripts/bench/tests/test_cluster.py`

**Produces:**
- `Cluster(state_dir: Path, namespace: str = "bifrost-local")` built from `local-kind.sh` conventions (kubeconfig `${STATE_DIR}/kubeconfig`, context `kind-<cluster>`).
- `kubectl(*args, input: str | None = None, check=True) -> str`
- `apply_profile(profile: dict) -> None`: scale `bifrost-scheduler` to 0, `bifrost-api` to 1, worker Deployment to `profile["worker_replicas"]`; `kubectl set env` on API and worker for `profile["env"]` (always includes `MALLOC_ARENA_MAX=2`); wait for rollouts.
- `api_pod() -> str`, `pod_uid(pod) -> str`
- `smaps(pod: str, container: str) -> str` (`kubectl exec <pod> -c <c> -- cat /proc/1/smaps`)
- `container_stats() -> list[dict]` from `kubectl get --raw /api/v1/nodes/<node>/proxy/stats/summary`, filtered to the namespace: `{pod, container, working_set_bytes, rss_bytes, cpu_usage_nano_cores}`.
- `exec_python(pod, script_path, args: list[str], env_from_secret: bool) -> dict` runs `python - <args>` with the script on stdin, returns the last JSON line.
- [ ] Test: `parse_stats_summary(fixture_json, namespace)` returns only that namespace's containers with the four fields.
- [ ] Implement; commit.

### Task 3: stats and report

**Files:** `scripts/bench/stats.py`, `scripts/bench/report.py`, tests for both.

**Produces:**
- `timings(records: list[dict]) -> dict`: each record has `created_at`, `started_at`, `completed_at` (ISO strings) and `status`. Returns `{"runs", "failed", "queue_ms": {p50,p95,max}, "run_ms": {...}, "e2e_ms": {...}}` over successful records only.
- `render(result: dict) -> str` markdown: environment block (git SHA, image id, profile, backend, THP knob), a class table (runs, failed, queue/run/e2e p50/p95), a memory table (API heap/anon/total and worker working set after each class), the idle floor.
- [ ] Tests: percentile values for a known list; failed records excluded and counted; render includes every class name and the git SHA.
- [ ] Implement; commit.

### Task 4: in-cluster driver

**Files:** `scripts/bench/incluster/driver.py`, `scripts/bench/incluster/bench_workflows.py` (workspace source as a module-level string constant `SOURCE`), `scripts/bench/incluster/seed_source.py` (generator for production-count workflows)

Runs in the `bench-driver` pod (Bifrost image, `sleep infinity`). Modes, each printing one JSON line:
- `setup {"seed": {...}, "backend": "fake"|"deepseek"|null}`: log in as the default user; write and register the bench workflows (idempotent: skip registered names); seed production-count workflows (idempotent by file path); create table `bench_docs` with 200 docs if absent; set configs `bench_cfg_1..5`; if a backend is given, create or update the AI connection (`openai_compatible`; fake: `http://bench-fake-llm:8080/v1`, model `bench-fake`; deepseek: `https://api.deepseek.com`, model `deepseek-chat`, key from env `DEEPSEEK_API_KEY`), a model profile, and assign it to `primary`, `summarization` and `chat_default`; create agents `bench_agent_chat` (no tools), `bench_agent_tools` (tools `bench_tool_lookup`, `bench_tool_count`, `bench_tool_note`). Returns ids.
- `run_class {"class": str, "sequential": int, "burst": int, "timeout_s": int}`: for workflow classes, POST `/api/workflows/execute` (`sync: false`) and poll `GET /api/executions/{id}` until terminal; for agent classes, set `/api/admin/ai/behavior` `agent_execution_mode` (`automatic` for `agent-codemode`, otherwise the non-automatic value read from the API's enum), POST `/api/agent-runs/enqueue`, poll `GET /api/agent-runs/{id}`. Returns raw records `{id, status, created_at, started_at, completed_at, error}`.
- [ ] Bench workflows: `bench_wf_noop`, `bench_wf_sdk` (5 × `config.get`, 1 × `tables.query(limit=20)`, 1 × `tables.insert`), `bench_wf_wait` (`asyncio.sleep(2)`), `bench_wf_cpu` (build ~20 MB of dicts, `json.dumps`/`json.loads`, return counts), `bench_wf_large` (return ~5 MB string), tools `bench_tool_lookup`/`bench_tool_count`/`bench_tool_note` (each one SDK call).
- [ ] Commit.

### Task 5: fake model

**Files:** `scripts/bench/incluster/fake_llm.py`, `scripts/bench/manifests/fake-llm.yaml`, `scripts/bench/manifests/driver.yaml`

OpenAI-compatible `POST /v1/chat/completions` (JSON and SSE streaming), `GET /v1/models`. Deterministic policy from the request alone:
- If the last message is a tool result, or no tools are offered: answer with text `"bench complete"`.
- If `run_code` is offered: one `run_code` call whose code calls every function named in the `run_code` tool description (`def <name>(` matches), then returns their results in a dict.
- Otherwise: one tool call per offered tool whose name starts with `bench_tool` (arguments `{}` filled with schema-required strings `"x"` / ints `1`).
- Usage block with fixed token counts. Optional fixed latency `FAKE_LLM_LATENCY_MS` (default 0).
Deployment runs `python /bench/fake_llm.py` from a ConfigMap on the Bifrost image; Service `bench-fake-llm:8080`.
- [ ] Commit.

### Task 6: orchestrator and command

**Files:** `scripts/bench/run.py`, `scripts/bench/profiles/baseline.json`, `test.sh` (add `bench` passthrough), `scripts/bench/README.md`

`python3 scripts/bench/run.py up | run --profile <name> [--backend fake|deepseek] [--out DIR] | down`
- `up`: `local-kind.sh up` (honours `BIFROST_KIND_SKIP_BUILD`), apply the bench manifests (fake model, driver pod), apply the profile.
- `run`: if backend is `deepseek`, read `op read "op://Personal/DeepSeek/credential"` (item path is a profile field) into a Kubernetes Secret via stdin; fail before anything else if `op` fails. Then: snapshot (fresh) → `setup` → restart API pod → snapshot (`boot_seeded`) → for each class in profile order: `run_class`, wait `settle_s`, snapshot → repeat the class list once more (second pass) → idle `idle_s` → snapshot (`idle`). Write `result.json` (profile, backend, git SHA, image id, per-class timings, per-snapshot memory, API pod UID check) and `report.md` to `--out` (default `/tmp/bifrost-bench-<utc-timestamp>/`).
- `down`: `local-kind.sh down` and remove the bench image.
- `baseline.json`: classes in spec order, `sequential: 10`, `burst: 10`, `settle_s: 20`, `idle_s: 120`, `seed: {"workflow": 1100, "tool": 701, "data_provider": 50}`, `env: {"MALLOC_ARENA_MAX": "2"}`, `thp_emulation: false`, `worker_replicas: 1`, `onepassword_profile: "personal", onepassword_item: "op://Development/DeepSeek/API Key"`.
- [ ] Commit.

### Task 7: first baseline runs

- [ ] `./test.sh bench up`, then `run --profile baseline --backend fake` twice; check the two runs agree within the Phase 1 exit tolerance.
- [ ] `run --profile baseline --backend deepseek` once.
- [ ] Write `docs/benchmarks/2026-10-27-baseline.md` from the reports (no raw data committed); commit.
