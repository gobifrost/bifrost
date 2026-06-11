# Plan: Slimming Bifrost's Python Runtime Memory Footprint

Date: 2026-06-09 · Status: execution-ready (scoped by Plan agent, refs verified against tree)
Companion: WS-6 in `docs/plans/2026-06-09-platform-50ft-action-plan.md`

**Repo:** `jackmusick/bifrost` · **Scope:** api/worker/scheduler roles, one shared image, slimming idle RSS
**Ground truth (measured):** worker pods idle ~455Mi ×6, api ~400Mi ×2, scheduler 169Mi. Template process inherits ~110MB of waste via multiprocessing spawn re-import of the worker `__main__` closure.

---

## 0. Verified root-cause map (re-checked file:line)

| Finding | Verified location |
|---|---|
| Spawn context + Process start | `api/src/services/execution/template_process.py:440` (`ctx = multiprocessing.get_context("spawn")`), `:443-447` (`ctx.Process(target=_template_main, args=(child_conn,), name="template-process")`) |
| Template intended preload | `template_process.py:119-172` (`_template_main`: bifrost SDK, httpx, pydantic, redis, sqlalchemy, virtual_import, simple_worker `install_requirements`) |
| Worker main closure | `api/src/worker/main.py:16-31` — module-level imports of `src.core.database`, `src.jobs.rabbitmq`, all 6 consumers |
| `Depends` in database.py | `api/src/core/database.py:14`; consumed only by `DbSession` (`:166`) and `OptionalDbSession` (`:189`) |
| fastapi via `UserPrincipal` | `api/src/core/auth.py:13-14` (fastapi, fastapi.security) — `UserPrincipal` (`auth.py:30`) is imported at module level by worker/scheduler-closure modules: `src/repositories/executions.py:20`, `src/core/org_filter.py:16`, `src/services/agent_executor.py:28`, `src/services/mcp_server/tools/execution.py:13` |
| fastapi via pubsub | `api/src/core/pubsub.py:22` (`from fastapi import WebSocket`) — pubsub is imported by `workflow_execution.py:29` and `agent_run.py:13` consumers |
| fastapi via solution guard | `api/src/services/solutions/guard.py:20` (`from fastapi import HTTPException, status`) — loaded at **runtime** in every role via `get_session_factory()` → `database.py:137` |
| anthropic+openai | `api/src/services/llm/factory.py:18,20` import the client modules; `anthropic_client.py:12` / `openai_client.py:12` import the SDKs. Chains into worker via `summarize_worker.py:28` → `run_summarizer.py:35` → `src.services.llm/__init__.py:25` → factory; and `agent_run.py:20` → `autonomous_agent_executor.py:36` |
| openai in scheduler | `scheduler/main.py:29` → `cron_scheduler.py:24` → `src.repositories.events` → **`src/repositories/__init__.py:13`** → `repositories/knowledge.py:17` → `src/services/embeddings/__init__.py:15` → `embeddings/factory.py:23` → `embeddings/openai_client.py:10` |
| mcp→sse_starlette→uvicorn | `api/src/services/mcp_client/client.py:21-22`, `dispatch.py:30`; package `__init__` imports both; pulled by `autonomous_agent_executor.py:37` |
| numpy via pgvector | `api/src/models/orm/knowledge.py:12` (`from pgvector.sqlalchemy import Vector`), column at `:74` (`Vector()`, unconstrained dim); the **only** vector operator used anywhere is `cosine_distance` at `src/repositories/knowledge.py:166` |
| No-fallback project rule | CLAUDE.md — the `multiprocessing.spawn` fallback in `process_pool.py` that leaked ~800MB/pod is the named incident; single canonical mechanism only |
| Dead param | `template_process.py:95` `preload_modules` is never passed by any caller (only caller is `process_pool.py:393` → `TemplateProcess().start()`) — remove it in Fix A per the dead-code rule |

---

## 1. Fix A — template entry module

### Why the template inherits 182MB

The parent worker is launched as `python -m src.worker.main` (verified: `docker-compose.yml:317`, `docker-compose.test.yml:233`, debug/dev via `watchmedo ... -- python -m src.worker.main`). With `python -m`, `sys.modules['__main__'].__spec__.name == "src.worker.main"`. When `ctx.Process(...).start()` runs with the spawn context, `multiprocessing.spawn.get_preparation_data()` records `init_main_from_name = "src.worker.main"`, and the spawn child's `prepare()` step executes `import src.worker.main` as `__mp_main__` **before** unpickling the target. Because `src/worker/main.py:16-31` imports all six consumers at module level, the child pays for the entire worker closure (anthropic, openai, fastapi, mcp, numpy, sqlalchemy — 2,714 modules, ~182MB) and *then* runs `_template_main`'s ~97MB intended preload on top.

**What crosses the spawn boundary today** (verified at `template_process.py:443-447`): the target `_template_main` (pickled *by reference* — module path + qualname, so the child imports only `src.services.execution.template_process`, whose module level is stdlib-only), one `multiprocessing.connection.Connection` (`child_conn`, picklable via the standard reduction), and the process name string. Nothing else. Per-fork pipes are sent later over the control pipe (`:499-505`), not at spawn. **There is no pickling obstacle to any of the options.**

### Option evaluation

- **(a) Minimal `template_entry.py` as spawn target — REJECTED.** The spawn `prepare()` step imports the *parent's main module* regardless of what the target is. The target module (`template_process.py`) is already stdlib-only at module level, so a new entry module changes nothing. This option misdiagnoses the mechanism and would be dead weight.
- **(c) forkserver with controlled preload — REJECTED.** The forkserver's forked children still run `spawn.prepare()` with the same `init_main_from_name`, so the main module gets imported anyway (COW-shared at best, only if you *also* preload it into the server — which defeats the purpose). It adds a third long-lived process, relies on process-global mutable state (`set_forkserver_preload`), and is exactly the kind of second mechanism CLAUDE.md forbids alongside the existing fork-from-template design. The template process *is already* a hand-rolled forkserver.
- **(b) Thin `__main__` for the worker — RECOMMENDED.** Make the module that spawn re-imports cost nothing. Single canonical mechanism, no new process types, no fallback path, and it permanently fixes every *future* spawn use from the worker too.

### Precise code change

**New file `api/src/worker/app.py`** — move *everything* currently in `src/worker/main.py` except the `if __name__ == "__main__"` block: the module docstring content about responsibilities, all imports (`main.py:16-31`), the `logging.basicConfig` + logger-level configuration (`:33-50`), the `Worker` class (`:55-208`), and `async def main()` (`:211-230`). No logic changes — pure move.

**Rewrite `api/src/worker/main.py`** to (exactly this shape):

```python
"""Thin worker entry point.

KEEP THIS MODULE STDLIB-ONLY AT MODULE LEVEL. The execution template
process is started with the multiprocessing "spawn" context, and spawn
re-imports this module (the parent's __main__) into the child during
prepare(). Any module-level import here is paid by the ~97MB template
process. tests/unit/test_import_hygiene.py enforces this.
"""
import asyncio


def run() -> None:
    from src.worker.app import main
    asyncio.run(main())


if __name__ == "__main__":
    run()
```

**In `template_process.py`**: delete the dead `preload_modules` parameter (`:95`, `:104-106` docstring lines, `:167-172` loop) — never passed by any caller; the no-dead-code rule applies.

### COW behavior change

Before: fork children COW-share the template's pages = 97MB intended preload **+** ~182MB inherited worker closure; children that touch nothing in the dead 182MB still hold page-table references and any GC/refcount touch dirties those pages. After: the template's address space is only the intended preload, so children COW-share a smaller, hotter set; per-pod RSS drops by the measured ~110MB of unshared waste, and child fork latency improves slightly (smaller page tables). Behavior of `os.fork()` at `template_process.py:236` is otherwise unchanged — same pipes, same protocol, same one-shot children.

### Gotchas

- The spawn child executes the thin `main.py` top level but **not** the `__main__` guard — that's the entire mechanism. Do not add "defensive" imports back.
- `logging.basicConfig` moves to `app.py`; the template still calls its own `basicConfig` at `template_process.py:109` — unchanged.
- `watchmedo auto-restart ... -- python -m src.worker.main` (debug/dev compose) still works — watchmedo execs the same command.
- No tests import `src.worker.main` (verified), so the move only touches the two files.
- The scheduler and API need no Fix A: only the worker role starts `TemplateProcess` (`process_pool.py` is reached solely from `src/jobs/consumers/workflow_execution.py:57` / `package_install.py:175`).

---

## 2. Fix B — lazy import pass (enumerated)

Each item: **file:line → change → risk note**. Lazy `import` inside a function after first load is a dict lookup — no hot-path concern; none of these are per-message hot paths anyway.

**B1. `src/core/database.py:14` — remove `from fastapi import Depends`.**
Move `DbSession` (`:166`) and `OptionalDbSession` (`:189`) plus the `Depends`/`Annotated` imports to a **new API-only module `api/src/core/db_deps.py`** (contents: the two `Annotated[...]` aliases importing `get_db`/`get_optional_db` from `src.core.database`). `get_db`/`get_optional_db` stay in database.py — they are plain async generators, no fastapi needed. Update every import site; enumerate with:
`rg -ln "DbSession|OptionalDbSession" api/src api/tests` — currently: **29 files in `api/src/routers/`**, `api/src/core/auth.py:16`, and `database.py` itself. Mechanical change: `from src.core.database import DbSession` → `from src.core.db_deps import DbSession` (watch mixed imports like `from src.core.database import DbSession, get_db_context` which must split into two lines).
*Risk:* purely mechanical; pyright will catch any missed site. No re-export shim in database.py (project rule).

**B2. `src/core/auth.py:30` — move `UserPrincipal` out of the fastapi module.**
Create **`api/src/core/principal.py`** holding the `UserPrincipal` dataclass (and only it). `auth.py` then does `from src.core.principal import UserPrincipal` (legitimate use, not a shim). Update the 8 src files + 7 test files importing `UserPrincipal` from auth: `rg -ln "UserPrincipal" api/src api/tests` → `src/repositories/executions.py:20`, `src/core/org_filter.py:16`, `src/services/agent_executor.py:28`, `src/services/mcp_server/tools/execution.py:13`, `src/routers/{websocket,executions,tables,cli}.py`, plus tests.
*Risk:* this is the chain that drags fastapi into the **scheduler** closure (`cron_scheduler` → `src.repositories` pkg init → `repositories.executions` → auth); missing one site keeps the guardrail test red, so stragglers are caught.

**B3. `src/core/pubsub.py:22` — `from fastapi import WebSocket` → type-only.**
Add `from __future__ import annotations` at top (currently absent — verified), move `WebSocket` under `if TYPE_CHECKING:`. All uses are annotations (`connections: dict[str, set[WebSocket]]` dataclass field, method signatures); instances are only ever *received*, never constructed.
*Risk:* the dataclass field annotation must not be evaluated at runtime — `from __future__ import annotations` guarantees that. If any `isinstance(x, WebSocket)` exists (none found), it needs a local import instead.

**B4. `src/services/solutions/guard.py:20` — defer fastapi into the raise sites.**
Move `from fastapi import HTTPException, status` into the three functions that raise (`:41`, `:103`, `:147` regions). This module is imported at **runtime** by `get_session_factory()` (`database.py:137`) in every role, so a module-level fastapi import re-pollutes worker/scheduler even after B1.
*Risk:* exception type unchanged (fastapi is installed in all roles' image); it only loads if a guard violation actually fires — rare by design.

**B5. `src/services/llm/factory.py:18,20` — defer client-module imports.**
Move `from src.services.llm.anthropic_client import AnthropicClient` and `...openai_client import OpenAIClient` from module level into the branches that construct them: `get_llm_client` (`:133-137`) and `create_llm_client` (`:175-179`). This is the single seam — the SDK imports (`anthropic_client.py:12-20`, `openai_client.py:12-13`) stay where they are because nothing else imports those modules at module level (verified: only factory.py; `routers/llm_config.py:392` is already function-local).
*Risk:* none functional. Chosen over TYPE_CHECKING-gating inside the client modules because `anthropic_client.py:222-244` *calls* the TypedDict constructors (`TextBlockParam(...)` etc.) at runtime — refactoring those is churn for zero extra benefit.

**B6. `src/services/embeddings/factory.py:23` — same pattern.**
Move `from src.services.embeddings.openai_client import OpenAIEmbeddingClient` into the function that returns it (`:161` region). This severs the scheduler→openai chain (and the worker's, via `src.repositories.__init__:13`).
*Risk:* `embeddings/reindex.py` does not import the client directly (verified); `embeddings/__init__.py:15` importing factory stays — factory becomes cheap.

**B7. `src/services/mcp_client/client.py:21-22` — defer `mcp` SDK.**
File already has `from __future__ import annotations`. Move `from mcp import ClientSession` / `from mcp.client.streamable_http import streamablehttp_client` into `open_session()` (`:39` region) as local imports; add `ClientSession` under `TYPE_CHECKING` for the `AsyncIterator[ClientSession]` annotation.
*Risk:* none — both names are used only inside `open_session`.

**B8. `src/services/mcp_client/dispatch.py:30` — `CallToolResult` → TYPE_CHECKING.**
File has future annotations; `CallToolResult` appears only in annotations (`:71`, `:115`). Move under `if TYPE_CHECKING:`.
*Risk:* none. After B7+B8 the `mcp`→`sse_starlette`→`uvicorn` subtree leaves the worker import closure entirely (`mcp_client/__init__.py` imports only these two heavy-edged modules — `errors.py`, `auth_resolution.py`, `catalog_sync.py`, `discovery.py` verified clean of `mcp` top-level imports).

**B9. `src/models/orm/knowledge.py:12` — replace pgvector's `Vector` with a minimal local type (kills numpy in every role).**
New file **`api/src/models/orm/vector_type.py`**:

```python
"""Minimal pgvector column type — no numpy dependency.

pgvector.sqlalchemy imports numpy at module level (~35MB), and this
module is in every role's import closure via src.models.orm. Bifrost
only ever binds list[float] and reads back list[float], and only uses
the <=> (cosine distance) operator, so a 40-line UserDefinedType
replicates the exact wire behavior (text in, text out — pgvector's
own SQLAlchemy type does the same string round-trip when no asyncpg
codec is registered, which Bifrost never registers).
"""
from __future__ import annotations
from sqlalchemy.types import Float, UserDefinedType


class Vector(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw) -> str:
        return "VECTOR"

    def bind_processor(self, dialect):
        def process(value):
            if value is None:
                return None
            return "[" + ",".join(repr(float(v)) for v in value) + "]"
        return process

    def result_processor(self, dialect, coltype):
        def process(value):
            if value is None or not isinstance(value, str):
                return value
            inner = value.strip()[1:-1]
            return [float(v) for v in inner.split(",")] if inner else []
        return process

    class comparator_factory(UserDefinedType.Comparator):
        def cosine_distance(self, other):
            return self.op("<=>", return_type=Float)(other)
```

Change `knowledge.py:12` to `from src.models.orm.vector_type import Vector`; column def at `:74` (`Vector()`, unconstrained dim) unchanged. The only operator used anywhere is `cosine_distance` (`repositories/knowledge.py:166`) — verified by `rg "embedding\." api/src`.
*Risk (the real one in this plan):* wire-format parity with asyncpg. Today pgvector's type also binds text and parses text results (no `register_vector` call exists in `api/src` — verified), so behavior should be identical, but this **must** be validated against a live stack: `./test.sh tests/e2e -k knowledge` plus a store→search round-trip. Alembic migrations that import `pgvector.sqlalchemy` (`alembic/versions/20251225_*.py`, `20260506_*.py`) keep doing so — pgvector/numpy stay installed in the image, they just leave the steady-state import closure.

**B10. Closure-stragglers protocol.** After B1–B9, run the guardrail (Section 3). If a forbidden module still appears, locate the chain from inside the test container:
`docker compose -f docker-compose.test.yml --profile test run --rm test-runner python -X importtime -c "import src.worker.app" 2>&1 | grep -E "fastapi|anthropic|openai|mcp|numpy|uvicorn|starlette" | head`
and fix with the same patterns. Known-clean by inspection but worth watching: `src.services.tool_registry` (via `agent_helpers.py:17`), `src.core.metrics`, `src.jobs.rabbitmq`.

---

## 3. Guardrail test — the regression lock

**New file: `api/tests/unit/test_import_hygiene.py`.** Runs in the existing `test-runner` container (`./test.sh unit`). **No DB needed**: each case spawns a fresh interpreter that only *imports* a module — `src.config.get_settings()` is lru-cached and not called at import time in any of these closures, and no module opens a connection at import (verified for worker/scheduler/template closures). Subprocesses are mandatory — the pytest process itself has everything imported already, so in-process `sys.modules` assertions would be meaningless.

```python
"""Import-closure hygiene: forbidden heavyweights must not load at import time.

These tests are the regression lock for the memory-slimming work
(template spawn re-import + lazy-import pass). Each case imports a
role's entry closure in a fresh interpreter and fails if a forbidden
top-level package appears in sys.modules. No DB, no network.
"""
import json
import subprocess
import sys

HEAVY = {"fastapi", "starlette", "uvicorn", "anthropic", "openai", "mcp", "numpy", "pgvector"}
# The spawn-entry and template modules must be stdlib-thin, not merely heavy-free:
THIN_EXTRA = {"sqlalchemy", "pydantic", "redis", "httpx", "aio_pika", "apscheduler", "src.worker.app"}


def closure_roots(module: str) -> set[str]:
    code = (
        f"import json, sys; import {module}; "
        "print(json.dumps(sorted({m.split('.')[0] for m in sys.modules} "
        "| {m for m in sys.modules if m == 'src.worker.app'})))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, cwd="/app",
    )
    assert out.returncode == 0, f"import {module} failed:\n{out.stderr}"
    return set(json.loads(out.stdout))


def test_worker_spawn_entry_is_stdlib_thin():
    # multiprocessing spawn re-imports this module into the template process.
    assert closure_roots("src.worker.main") & (HEAVY | THIN_EXTRA) == set()

def test_template_process_module_is_stdlib_thin():
    assert closure_roots("src.services.execution.template_process") & (HEAVY | THIN_EXTRA) == set()

def test_worker_app_closure_has_no_heavyweights():
    assert closure_roots("src.worker.app") & HEAVY == set()

def test_scheduler_closure_has_no_heavyweights():
    assert closure_roots("src.scheduler.main") & HEAVY == set()
```

Notes for the executor: `cwd="/app"` matches the test-runner container layout; if running on a host checkout instead, derive with `pathlib.Path(__file__).resolve().parents[2]` rather than hardcoding, and verify it works in the container. `pgvector` is in HEAVY because it is the numpy vector. Do **not** add `cryptography`/`sqlalchemy` to the app-closure forbidden set — they are legitimately needed. This file fails today on every case (good: write it first, watch it go red, drive Fixes A+B with it).

---

## 4. Verification on the local debug stack

```bash
# 1. Boot (per-worktree isolated project)
./debug.sh up
./debug.sh status          # note COMPOSE_PROJECT_NAME, URL, login dev@gobifrost.com / password

# 2. Per-process RSS inside the worker container (image has no procps — read /proc):
docker compose -f docker-compose.debug.yml exec worker sh -c '
  for d in /proc/[0-9]*; do
    rss=$(awk "/VmRSS/ {print \$2}" "$d/status" 2>/dev/null)
    [ -n "$rss" ] && printf "%8s KB  %s\n" "$rss" "$(tr "\0" " " < "$d/cmdline" | cut -c1-90)"
  done | sort -rn'

# 3. Container-level totals
docker stats --no-stream
```

The worker container shows (debug stack runs under watchmedo): the `watchmedo` supervisor, `python -m src.worker.main` (consumer parent), and the template — its cmdline contains `from multiprocessing.spawn import spawn_main` (that's how you identify it). Capture this **before** the change on the current main branch, then after.

**Expected numbers** (idle, debug stack; treat ±15% as in-band):

| Process | Before | After A | After A+B |
|---|---|---|---|
| template (`spawn_main` cmdline) | ~250–300MB | **~95–110MB** | ~95–110MB |
| consumer parent (`-m src.worker.main`) | ~180–230MB | unchanged | **~110–150MB** (−anthropic −openai −mcp/uvicorn −fastapi −numpy) |
| worker container total | ~430–470MB | ~330–360MB | **~230–290MB** |
| scheduler container | ~160–180MB | n/a | **~95–125MB** |

If the after-A template is not within ~15MB of the intended ~97MB preload, something is still leaking into `src.worker.main`'s module level — re-run the guardrail.

**Functional smoke** (executions and agent runs must still work — anthropic/openai/mcp now load lazily at first use):

```bash
# Backend e2e through the real worker (workflow execution path):
./test.sh stack up && ./test.sh e2e tests/e2e/engine -v
# Agent run path (loads LLM SDK lazily):
./test.sh tests/e2e/api/test_agents.py tests/e2e/api/test_chat.py -v
# Knowledge store round-trip (validates the custom Vector type on a real pgvector DB):
./test.sh tests/e2e -k knowledge -v
```

Manual confirmation on the debug stack: log in, run any workflow from the UI, then chat with an agent; re-run the RSS snapshot *after* the agent chat to record how much the parent grows when anthropic/openai actually load (expected: +50–80MB on the consumer parent only, on first agent message — accepted trade).

---

## 5. How low can we go — the floor

**Post-A+B steady state per worker pod:** template ~97MB + consumer parent ~110–150MB + one-shot fork children (transient, COW-shared) ≈ **~230–280Mi idle**, down from 455Mi. Ranked further reductions:

| # | Lever | Savings | Effort | Trade-off |
|---|---|---|---|---|
| 1 | **KEDA/HPA on RabbitMQ queue depth** — scale workers on `workflow-executions` + `agent-runs` + summarize queues; `minReplicaCount: 1-2`, scale to 6+ on backlog. | **~1.0–1.8GiB cluster-wide idle** (4–5 fewer × ~250Mi). Biggest lever by an order of magnitude, zero code. | Low (one ScaledObject YAML + RabbitMQ host secret) | Cold-start latency on burst: pod start + template preload + `install_requirements()` pip pass (120s startup budget at `template_process.py:452`). Graceful drain already exists (`BIFROST_DRAIN_DEADLINE_SECONDS`, `worker/main.py:169`). Do this regardless of everything else. |
| 2 | **Trim the template preload** — `bifrost/__init__.py:162-163` already documents "Worker subprocesses never need sqlalchemy", and the forked-child path (`simple_worker.py` → `execution/worker.py`) imports no sqlalchemy at module level (verified). Drop the `sqlalchemy` preload at `template_process.py:141-144`; keep `redis` (children use sync redis) and `pydantic`/`httpx` (bifrost SDK needs them). | ~25–40MB off template + every child | Low, but **verify first**: run a workflow touching every SDK surface; if any child lazily imports sqlalchemy per-execution you've moved cost, not removed it | Slight first-use latency in a child if some rare path does need it (then imports privately, un-shared) |
| 3 | **`gc.freeze()` after preload** — in `_template_main` immediately before the fork loop (`:183`): `gc.collect(); gc.freeze()`. Instagram's classic COW protection: frozen objects leave the GC generations, so collections in children don't dirty shared pages. | ~5–20MB per *active* child; nothing at idle | Trivial | Measure before keeping (no speculative code — if debug-stack measurement shows <5MB, drop it) |
| 4 | **Scheduler right-sizing** — after Fix B the scheduler should idle ~95–125Mi. A bespoke slim entry buys nothing further: its closure is apscheduler+sqlalchemy+redis, all genuinely used. | ~50–70Mi (already counted in Fix B) | — | — |
| 5 | **Split `src.models.orm` so knowledge loads only where used** — **not recommended.** After B9 removes numpy, `knowledge.py` costs ~nothing. Splitting the ORM registry breaks SQLAlchemy mapper configuration (string-referenced relationships fail unless every mapper is registered) and Alembic autogeneration. High risk, ~0 savings post-B9. |
| 6 | **Python version** — already on `python:3.14-slim` (Dockerfile:9), so PEP 683 immortal objects and the 3.12+ COW improvements are already banked. Nothing to do. |
| 7 | **API right-sizing** — api pods keep fastapi by definition; B5/B9 trim ~40–70Mi each (numpy + lazy LLM SDKs until first agent use). Lower the 256Mi request only after observing post-fix steady state with agent traffic, since the SDKs load back in on use. |

**The honest floor.** A CPython 3.14 process that has *actually imported* sqlalchemy(+asyncpg), pydantic v2, redis, httpx, aio_pika and the bifrost SDK idles at **~80–120MB RSS** — interpreter (~12MB) + pydantic-core and sqlalchemy alone are ~50MB of that, and none of it is optional for this architecture. The worker pod's structural minimum is two such processes (consumer parent + template), so **~200–250Mi per worker pod is the realistic floor; ~100–130Mi for the scheduler**. 50MB-class containers are compiled-language territory: a Go/Rust consumer shell (RabbitMQ consume, Redis result push, process supervision) execing Python for execution children would still require the ~97MB Python template for COW — it saves only the parent's ~120MB while forking the codebase into two languages and duplicating the drain/recycle/heartbeat logic in `process_pool.py`. **Not worth it.** Lever #1 (KEDA, 6 idle replicas → 1–2) saves more than a full rewrite would, with zero code.

---

## 6. Task breakdown for executors

Ordered; each sized for one sitting. Branch off main once; tests-first within each task. **Run `cd api && pyright && ruff check .` after every task** — both must be clean before moving on.

**Task 0 — Baseline measurement (no code).**
Boot `./debug.sh up`; capture the Section-4 RSS snapshot + `docker stats --no-stream`; paste into the PR description. *Done:* before-numbers recorded for worker/scheduler/api containers and per-process template/parent split.

**Task 1 — Guardrail test (red).**
Files: `api/tests/unit/test_import_hygiene.py` (new, exactly Section 3). Run `./test.sh tests/unit/test_import_hygiene.py -v`; *done when all four cases FAIL* with forbidden-module lists matching Section 0's map (validates the test sees what we think it sees). Commit with the failures documented in the message; do not mark xfail.

**Task 2 — Fix A: thin worker `__main__`.**
Files: `api/src/worker/app.py` (new), `api/src/worker/main.py` (rewrite per Section 1), `api/src/services/execution/template_process.py` (delete `preload_modules` param + loop).
Test: `test_worker_spawn_entry_is_stdlib_thin` and `test_template_process_module_is_stdlib_thin` go green; `test_worker_app_closure_has_no_heavyweights` still red (expected until Tasks 3–6).
*Done:* those two green; `./test.sh unit` otherwise unchanged; debug stack worker boots and executes a workflow (`./test.sh e2e tests/e2e/engine -v`); template RSS in debug stack ≈ ~97–110MB.

**Task 3 — Fix B fastapi pass (B1–B4).**
Files: `api/src/core/db_deps.py` (new), `api/src/core/database.py`, `api/src/core/principal.py` (new), `api/src/core/auth.py`, `api/src/core/pubsub.py`, `api/src/services/solutions/guard.py`, all import sites from `rg -ln "DbSession|OptionalDbSession|UserPrincipal" api/src api/tests`.
*Done:* pyright clean (catches every missed import site), `./test.sh unit` green, no `fastapi` in `closure_roots("src.worker.app")` output.

**Task 4 — Fix B LLM/embeddings pass (B5–B6).**
Files: `api/src/services/llm/factory.py`, `api/src/services/embeddings/factory.py`.
*Done:* `anthropic`/`openai` gone from worker **and** scheduler closure outputs; `./test.sh tests/e2e/api/test_chat.py -v` passes (agent chat lazily loads the SDK and works).

**Task 5 — Fix B mcp pass (B7–B8).**
Files: `api/src/services/mcp_client/client.py`, `api/src/services/mcp_client/dispatch.py`.
*Done:* `mcp`, `sse_starlette`, `uvicorn` gone from worker closure; narrowest mcp-client e2e available passes.

**Task 6 — Fix B vector type (B9).**
Files: `api/src/models/orm/vector_type.py` (new), `api/src/models/orm/knowledge.py:12`.
Test-first: unit test asserting bind/result round-trip of the processors (pure function test, no DB), then the e2e: `./test.sh tests/e2e -k knowledge -v` against the real pgvector database — this is the task's hard gate.
*Done:* `numpy`/`pgvector` gone from all closure outputs; knowledge store→search round-trip returns correct cosine ordering; **all four guardrail cases green**.

**Task 7 — Straggler sweep + lock.**
Run the full guardrail; if anything is still red, use the B10 `-X importtime` protocol to chase it. Remove any temporary diagnostics.
*Done:* `./test.sh tests/unit/test_import_hygiene.py -v` fully green with the final forbidden sets (no loosening of `HEAVY`/`THIN_EXTRA`).

**Task 8 — Stack verification + after-numbers.**
Full pre-completion sequence, in order:

```bash
cd api && pyright                # 0 errors
cd api && ruff check .           # clean
./test.sh stack up && ./test.sh all   # unit + e2e, mirrors CI
./debug.sh up                    # then Section-4 RSS snapshot + docker stats
# workflow smoke via UI/CLI + agent chat smoke, re-snapshot after agent use
```

*Done:* after-numbers vs Task 0 recorded in the PR (template ~97MB, worker container ~230–290MB, scheduler ~95–125MB); both smokes pass; PR notes the deliberate non-goals (KEDA ScaledObject and template-preload trim filed as follow-up issues per Section 5 #1–#2).

**Task 9 (follow-up, separate PR, zero code) — KEDA ScaledObject for the worker deployment** on RabbitMQ queue depth per Section 5 #1, plus scheduler memory-request review. Keep it out of this PR so the runtime change ships and soaks alone.
