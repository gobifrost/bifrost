# Plan: Hardening Bifrost's Workflow-Execution Sandbox

Date: 2026-06-09 · Status: execution-ready (scoped by Plan agent, refs verified against main @ 155670ab)
Companion: empirical prior art in `docs/superpowers/specs/2026-04-27-chat-v2-sandbox-bwrap-findings.md`
Workstream: WS-15 in `docs/plans/2026-06-09-platform-50ft-action-plan.md`

**Owner's goal (verbatim):** *"the execution portion should absolutely not be accessing anything but the API endpoints."* Plus the known container-posture gap.

Three phases, each independently shippable as its own PR. Every phase ends with a live debug-stack drive — breaking the execution path is the worst outcome.

---

## 0. Verified architecture & root-cause map (re-checked file:line)

### 0.1 How a customer workflow actually executes (the fork chain)

| Step | Verified location |
|---|---|
| RabbitMQ consumer builds context dict, writes it to Redis, routes to pool | `api/src/jobs/consumers/workflow_execution.py:673` (context_data), `:704` (`route_execution`) |
| Pool writes context to Redis + forks a one-shot child from the template | `api/src/services/execution/process_pool.py:689` (`_write_context_to_redis`), `:709` (`_fork_process` → `template.fork(...)`) |
| Template is a long-lived process started with **spawn**, forks children with **`os.fork()`** | `template_process.py:443` (`get_context("spawn")`), `:236` (`os.fork()`) |
| Child runtime loop: reads `execution_id` from a pipe, runs `_execute_sync` | `template_process.py:317-387`, `simple_worker.py:292` (`_execute_sync`) → `:321` (`_execute_async`) |
| **Child reads execution context directly from Redis** | `simple_worker.py:410` (`_read_context_from_redis`) → `redis.from_url(settings.redis_url)` at `:425` |
| Child runs the engine | `simple_worker.py:355` (`from src.services.execution.worker import _run_execution`), `worker.py:118` |
| Engine refreshes the **engine credentials file** as a failsafe per execution | `worker.py:128` (`from src.core.security import authenticate_engine`), `:133` |

### 0.2 What the child inherits and uses (the security findings)

| Finding | Verified location / detail |
|---|---|
| **Child env = full worker env.** `os.fork()` children inherit the parent's entire `os.environ`. The worker service env block exposes every credential. | `docker-compose.yml:280-302` — `BIFROST_SECRET_KEY`, `BIFROST_DATABASE_URL`/`_SYNC` (Postgres), `BIFROST_RABBITMQ_URL`, `BIFROST_REDIS_URL`, `BIFROST_S3_ACCESS_KEY`/`_SECRET_KEY`/`_ENDPOINT_URL`. K8s injects the same via `envFrom: secretRef: bifrost-secrets` (`k8s/worker/deployment.yaml:32-34`, `k8s/secret.yaml`). |
| **`BIFROST_SECRET_KEY` is the crown jewel.** It signs JWTs and is the Fernet key for credential encryption. A child with it can mint a superuser token offline and decrypt integration secrets. | `config.py:122`; used by `create_access_token` in `authenticate_engine` (`security.py:417`). |
| **The engine talks to infra directly, NOT only the API.** The child's `_run_execution` → `engine.execute` writes logs to **Redis Streams** and reads/writes the **write-buffer in Redis**. In-process `src.*` imports, not HTTP. | Log stream: `bifrost/_logging.py:72` (`_get_sync_redis` → `settings.redis_url`), `:122` (`xadd`). Write buffer: `bifrost/_write_buffer.py:119` (`_get_redis`), set up at `engine.py:1068-1075`. Postgres flush runs in the **consumer**, not the child: `workflow_execution.py:189-190`. |
| **Customer SDK calls DO go through API HTTP.** `bifrost.tables`, `.integrations`, `.config`, `.organizations` etc. all use `get_client()` → httpx to `/api/...`. The child authenticates via the **credentials file** (`~/.bifrost/credentials.json`), not env injection. | `bifrost/tables.py:39`; `bifrost/integrations.py:87`; `bifrost/client.py:646` (`get_client`: injected → file). `authenticate_engine` writes the file (`security.py:405`, `save_credentials` at `:449`). |
| **Two distinct trust planes inside one child:** (a) *customer code* → bifrost SDK → **HTTP API only** (already the owner's goal for that layer); (b) *the engine that hosts the customer code* → **direct Redis + the SECRET_KEY**. The leak is plane (b). | Synthesis of the rows above. |
| **What the child genuinely needs Redis for, directly:** read execution context (`simple_worker.py:425`), stream logs (`_logging.py`), buffer SDK writes (`_write_buffer.py`), read workspace module code from the Redis module cache (`worker.py:180` `get_module_sync`, `module_cache_sync.py`). **This is the hard dependency that blocks naive env-scrubbing.** | enumerated above |
| **What the child needs S3 for:** module cache **fallback** when Redis misses (`module_cache_sync.py:53-71` reads `BIFROST_S3_*` from env), and `requirements_cache.get_requirements_sync` S3 fallback (`requirements_cache.py:84`). | verified |
| **What the child needs Postgres for, directly:** *nothing at steady state* — results return over the pipe; the **consumer** flushes to Postgres (`workflow_execution.py:190`, `get_db_context` at `:550`). Child inherits `BIFROST_DATABASE_URL` but the forked-child code path opens no DB session (no `get_db`/`AsyncSession` in `simple_worker.py`/`worker.py`). **Easy win for Phase 2.** | verified |
| **`pip install` runs once in the template parent, not per-child.** `install_requirements()` is called in `_template_main` at pool startup; children inherit the resulting filesystem via COW. Needs **PyPI egress** + writable user-site. | `template_process.py:152`, `simple_worker.py:93` (`_pip_install`), `:156` (`site.getusersitepackages()`). |

### 0.3 Current container posture (the root gap, corrected)

| Finding | Verified location |
|---|---|
| **Image has no `USER`; entrypoint switches to `bifrost` (uid 1000) via `gosu`** — only the leaf process drops; image runs root until entrypoint. | `api/Dockerfile:93`, `entrypoint.sh:8-25` (`gosu bifrost`). |
| **K8s worker already sets `runAsUser: 1000`** (pod-level) — prod worker is **not root at runtime**, but lacks: `runAsNonRoot`, `allowPrivilegeEscalation: false`, `capabilities: drop [ALL]`, `seccompProfile`, `readOnlyRootFilesystem`, `automountServiceAccountToken: false`. | `k8s/worker/deployment.yaml:61-64`. |
| **compose worker has no `user:`** — dev/compose parity gap vs k8s. | `docker-compose.yml:317`. |
| **Dead env `BIFROST_USE_THREAD_WORKERS: "true"`** in debug/dev compose not consumed anywhere (`extra="ignore"` swallows it). Cleanup, not load-bearing. | `docker-compose.debug.yml:236`, `docker-compose.dev.yml:140`. |
| **bwrap host/container matrix already empirically derived** — reuse, don't re-derive. Keys: `seccomp=Unconfined` (or custom profile) required; `--ro-bind /proc /proc` not `--proc /proc`; no fresh PID ns inside Docker without elevated mounts. | `docs/superpowers/specs/2026-04-27-chat-v2-sandbox-bwrap-findings.md`. |

### 0.4 The owner's goal, decomposed

1. **Customer code → already API-only** (bifrost SDK = HTTP). ✅ no change.
2. **The engine hosting customer code → currently direct Redis + SECRET_KEY.** The gap. Phase 2 scrubs everything the child doesn't use and removes SECRET_KEY via parent-side token minting; full API-routing of the engine's Redis use is sized honestly as a follow-up.
3. **The container → under-hardened.** Phase 1 fixes cheaply.

**Non-goals:** gVisor/Kata; full network-deny for workflow egress (integrations legitimately call external APIs); anything touching `bifrost solution start` local dev (out of scope — runs in the developer's own process).

---

## Phase 1 — Container posture (cheap, k8s/docker only, no Python changes)

**Goal:** worker + children run non-root, no caps, no SA token, read-only root FS, with dev/compose parity.

### 1.1 What breaks without root, and the fix for each

| Needs write/root today | Why | Fix |
|---|---|---|
| `entrypoint.sh` chown of `/workspace`, `/tmp/bifrost`, `/coverage` | root fixes volume perms then gosu drops | With `runAsUser:1000` + `fsGroup:1000` the root branch is skipped (entrypoint handles non-root at `:26-29`). No code change; verify mounts below are group-writable. |
| pip user-site (`site.getusersitepackages()` → `~/.local/...`) | `install_requirements` at template startup | uid 1000 HOME=`/home/bifrost` (`useradd -m`, Dockerfile:82). Must stay writable → `emptyDir` at `/home/bifrost`. |
| `~/.bifrost/credentials.json` (`authenticate_engine`) | every execution refreshes engine token | covered by the `/home/bifrost` emptyDir. |
| `/tmp/bifrost`, `/tmp` (pip tempfiles `simple_worker.py:136`, engine temp `config.py:215`) | tempfiles | `emptyDir`/tmpfs at `/tmp`. |
| pip cache (`~/.cache/pip`) | downloads | covered by HOME emptyDir; set `PIP_NO_CACHE_DIR=1` anyway (pip runs rarely; cache bloats). |
| `/tmp/git` (`git_repo_manager.py:35`) | **NOT in the worker** — `GitRepoManager` only constructed by `github_sync.py:278` (API/scheduler). | No worker impact. Flag: if read-only FS is ever applied to API/scheduler pods, `/tmp/git` needs a writable mount there. |
| Node `node_modules` (app compiler) | baked at build, runs in API | read-only fine. |

**Conclusion:** `readOnlyRootFilesystem: true` is achievable for the worker with writable `emptyDir` at `/home/bifrost` and `/tmp`, plus `PIP_NO_CACHE_DIR=1`.

### 1.2 Enumerated changes

**C1.1 — `k8s/worker/deployment.yaml` container securityContext:**
```yaml
          securityContext:
            allowPrivilegeEscalation: false
            runAsNonRoot: true
            capabilities:
              drop: ["ALL"]
            seccompProfile:
              type: RuntimeDefault
            readOnlyRootFilesystem: true
```
Keep pod-level `runAsUser/runAsGroup/fsGroup: 1000`. Note: `RuntimeDefault` is correct **for Phase 1**; Phase 3 (bwrap) replaces it with a custom profile — don't pre-relax here.

**C1.2 — pod spec: `automountServiceAccountToken: false`.** Worker makes no k8s API calls (verified) — the token is pure attack surface.

**C1.3 — writable mounts:**
```yaml
          volumeMounts:
            - { name: home,  mountPath: /home/bifrost }
            - { name: tmp,   mountPath: /tmp }
      volumes:
        - { name: home, emptyDir: {} }
        - { name: tmp,  emptyDir: {} }
```
Add `PIP_NO_CACHE_DIR: "1"` (configmap or worker env). Confirm `HOME=/home/bifrost` is exported at runtime; if not, add it to worker env.

**C1.4 — compose parity (`docker-compose.yml` worker):**
```yaml
    user: "1000:1000"
    read_only: true
    tmpfs: [/tmp, /home/bifrost]
    security_opt: ["no-new-privileges:true"]
    cap_drop: ["ALL"]
    environment:
      PIP_NO_CACHE_DIR: "1"
      HOME: /home/bifrost
```
Caveat: `user:` bypasses the gosu chown branch; worker has no host-bind volumes needing root chown (verified, `docker-compose.yml:303`).

**C1.5 — debug/test compose parity.** Same `user`/`cap_drop`/`no-new-privileges` in `docker-compose.debug.yml` + `docker-compose.test.yml`, but **keep `read_only` off in debug** (hot-reload writes). Capability parity, not FS parity. Remove dead `BIFROST_USE_THREAD_WORKERS` while here.

### 1.3 Guardrail test (Phase 1)

**New `api/tests/e2e/security/test_worker_posture.py`** (under `./test.sh`): `id -u` ≠ 0; `CapEff` near-empty in `/proc/1/status`; write-to-`/` fails (gated on a prod-shape env marker so debug stack is exempt); k8s SA-token absence asserted in a documented k8s smoke (n/a in compose).

### 1.4 Debug-stack drive (Phase 1 done-gate)

```bash
./debug.sh up
docker compose -f docker-compose.debug.yml exec worker id -u                       # != 0
docker compose -f docker-compose.debug.yml exec worker sh -c 'grep CapEff /proc/1/status'
./test.sh stack up
./test.sh e2e tests/e2e/engine -v                  # workflow + SDK table read/write
./test.sh tests/e2e -k "integration" -v            # integration HTTP call
./test.sh tests/e2e -k "requirements or package" -v  # pip-dependency workflow under non-root HOME
./test.sh tests/e2e/api/test_chat.py -v            # agent run
```
**Done when** all four execution drives pass under a non-root, cap-dropped worker and pip still writes the user-site.

---

## Phase 2 — Env scrubbing (children get a minimal whitelisted env)

**Goal:** the forked child must NOT see `BIFROST_SECRET_KEY`, `BIFROST_DATABASE_URL(_SYNC)`, `BIFROST_RABBITMQ_URL`, `BIFROST_S3_*`. It keeps: `BIFROST_API_URL`, the per-execution credentials file, execution metadata, and — honest dependency — a (narrowed) Redis URL.

### 2.1 The honest dependency analysis

The child uses Redis directly for four things (§0.2); removing `BIFROST_REDIS_URL` outright breaks the engine. Options:

- **Option A (recommended, scoped): scrub everything except a narrowed Redis URL.**
  - **SECRET_KEY:** child needs it only for `authenticate_engine` → `create_access_token`. **Refactor:** mint the engine token **parent-side** (consumer/template, which legitimately holds the key) and hand the *token* to the child via `context_data` / pipe; child writes it to the credentials file and skips `authenticate_engine`. Removes the crown jewel from the child entirely. (`worker.py:128-133`, `security.py:405`, `workflow_execution.py:673`.) **Size M.**
  - **DATABASE_URL(_SYNC):** child opens no DB session (verified). **Env scrub only. Size S.**
  - **RABBITMQ_URL:** child never touches RabbitMQ. **Env scrub only. Size S.**
  - **S3_*:** only the module-cache/requirements *fallback* (Redis-miss path). MUST-VERIFY T2 decides: if the child-side fallback never fires in practice, scrub; if it can, add a narrow API module-fetch endpoint. **Size M (conditional).**
  - **REDIS_URL:** stays, but as a **scoped Redis ACL user** limited to `bifrost:exec:*` + log-stream prefixes rather than the admin URL (or same URL now, ACL as filed follow-up). **Size M (or S + follow-up).**
- **Option B (the "pure" goal): route the engine's Redis usage through the API too** (logs → `POST /api/executions/{id}/logs`; write-buffer → API; context → pipe). Achieves literal API-only for the child. **Size L** (touches `_logging.py`, `_write_buffer.py`, `_sync.py`, `simple_worker.py`, consumer, 2-3 routers). **Recommendation: Option A now, Option B as follow-up** — A removes SECRET_KEY and every non-Redis credential: ~90% of the risk for ~30% of the effort, one canonical Redis path remains (credential-narrowed, no fallback).

### 2.2 Scrub mechanism (single canonical, no-fallback)

Children come from `os.fork()` (`template_process.py:236`) — no per-fork env. Pick ONE:
- **M1 (recommended): scrub once at the template**, in `_template_main` after `install_requirements()` (which needs S3) — delete forbidden keys from `os.environ` right before `pipe.send({"status":"ready"})` (~`:180`). All forks inherit the scrubbed env.
  - *Caveat:* `get_settings()` is `lru_cache`d. If the template has cached Settings with secrets, the scrub must also clear the cache — or move to fork-entry (top of `_run_forked_child`, `:293`) before any `get_settings()` call. **MUST-VERIFY T1 decides the point.**
- **M2:** scrub at fork-entry each time. Use only if T1 forces it.

**Engine-token hand-down:** parent calls `create_access_token` (30-day engine token already the design, `security.py:434`), puts it in `context_data["engine_token"]`; child writes it via `save_credentials(...)` and the SECRET_KEY-dependent `authenticate_engine` call is **deleted from the child path** (no-dead-code: `authenticate_engine` survives only where SECRET_KEY legitimately lives).

### 2.3 Guardrail test (Phase 2) — the core regression lock

**New `api/tests/e2e/security/test_child_env_isolation.py`**: a real workflow introspects its own process and asserts `BIFROST_SECRET_KEY` / `BIFROST_DATABASE_URL(_SYNC)` / `BIFROST_RABBITMQ_URL` are absent from its env, and a direct Postgres connection cannot be formed. **Positive controls in the same suite:** SDK table read/write works, an integration call works, a pip-dependency workflow runs, an agent run completes. Written first (red), driven green.

### 2.4 Debug-stack drive (Phase 2 done-gate)

```bash
./debug.sh up
docker compose -f docker-compose.debug.yml exec worker sh -c \
  'cat /proc/$(pgrep -f spawn_main | head -1)/environ | tr "\0" "\n" | grep BIFROST_'
# Expect: API_URL + REDIS_URL present; SECRET_KEY/DATABASE_URL/RABBITMQ_URL/S3 absent.
./test.sh e2e tests/e2e/security/test_child_env_isolation.py -v
./test.sh e2e tests/e2e/engine -v
./test.sh tests/e2e -k "integration" -v
./test.sh tests/e2e -k "requirements or package" -v
./test.sh tests/e2e/api/test_chat.py -v
```

---

## Phase 3 — Namespace isolation (per-execution bwrap)

### 3.1 The net-isolation tradeoff (explicit recommendation)

Workflows legitimately need outbound HTTP (integrations) and the Bifrost API. Therefore:
- **Filesystem isolation: YES** — read-only binds, single writable `/work`, `--ro-bind /proc /proc` (per findings; `--proc /proc` is blocked in Docker).
- **PID isolation: PARTIAL** — fresh PID ns is blocked inside Docker without elevated mounts (findings doc); accept the documented v1 tradeoff (info disclosure, not privilege).
- **Network: NO per-process deny.** `--unshare-net` would require a userspace proxy in every child to reach the API and integrations. Instead: shared net namespace + **k8s NetworkPolicy** as the canonical egress control (allow API service, DNS, PyPI, integration egress range / egress proxy). bwrap owns fs/pid; NetworkPolicy owns net. No overlap, no fallback.

Ship bwrap with `--unshare-ipc --unshare-uts --unshare-cgroup`, ro-bind FS + writable `/work`, `--ro-bind /proc /proc`, `--die-with-parent`; **no `--unshare-net`**.

### 3.2 Platform prerequisites (reuse findings doc)

Custom `bifrost-sandbox-seccomp.json` (allowing `unshare(CLONE_NEWUSER)` + bwrap syscalls) instead of blanket Unconfined — replaces Phase 1's `RuntimeDefault`. Host sysctls per the findings doc's per-platform matrix (Ubuntu 24.04 apparmor userns restriction; Bottlerocket max_user_namespaces). **Preflight** at template startup: `unshare -U /bin/true`; on failure, **fail loudly with platform remediation — never silently fall back to unsandboxed execution.** Add `bwrap` to the runtime image (`api/Dockerfile:40` apt block).

### 3.3 Wrap boundary

Recommend: the engine (trusted) runs unsandboxed in the fork and keeps its pipe/Redis I/O; it spawns the **customer-function portion** as a bwrapped subprocess with `/work` as the only writable path and the credentials file bind-mounted read-only. MUST-VERIFY T3 prototypes this seam.

### 3.4 Guardrail test + drive (Phase 3 done-gate)

**New `api/tests/e2e/security/test_sandbox_fs_isolation.py`**: workflow reading outside `/work` (e.g. `/etc/shadow`) fails; writing `/work` succeeds; integration egress still works. Preflight test: worker refuses to dispatch with a clear error if `unshare -U` fails. Same four execution drives as Phases 1–2.

---

## Task breakdown for executors

Branch off main once. Tests-first within each task. `cd api && pyright && ruff check .` clean after every code task. Each phase = its own PR.

### MUST-VERIFY first (gate the design)

- **T1 — Settings lru_cache vs env scrub.** Does the template cache Settings (with secrets) before fork? Determines scrub point M1 vs M1+cache-clear vs M2.
- **T2 — Child-side S3 fallback reality.** Does `get_module_sync`/`get_requirements_sync` S3 fallback ever fire in a forked child with warm Redis? Decides scrub-vs-API-endpoint for `BIFROST_S3_*`.
- **T3 — bwrap wrap boundary.** Prototype `unshare -U` + a trivial bwrapped Python inside the worker container; confirm wrapping only the customer call while the engine keeps Redis/pipes. Confirms the Phase-3 seccomp requirement vs Phase-1 RuntimeDefault.
- **T4 — Engine-token hand-down.** Drive one table read end-to-end with SECRET_KEY removed from the child env and a parent-minted token.

### Phase 1 (PR 1)
1.1 posture guardrail test (red) → 1.2 k8s hardening (C1.1–C1.3) → 1.3 compose parity (C1.4–C1.5, drop dead `BIFROST_USE_THREAD_WORKERS`) → 1.4 the §1.4 drive.

### Phase 2 (PR 2)
2.1 env-isolation guardrail test (red) → 2.2 engine-token hand-down (per T4) → 2.3 env scrub at the single point (per T1; SECRET_KEY, DB ×2, RabbitMQ, S3 per T2) → 2.4 conditional S3 module-fetch endpoint (per T2) → 2.5 Redis ACL narrowing (or file as follow-up) → 2.6 the §2.4 drive.

### Phase 3 (PR 3)
3.1 bwrap in image + custom seccomp profile → 3.2 loud preflight (no fallback) → 3.3 bwrap wrap of customer code (per T3) → 3.4 `k8s/worker/networkpolicy.yaml` → 3.5 fs-isolation guardrail + per-platform deployment doc → 3.6 the §3.4 drive.

---

## Critical files

- `api/src/services/execution/template_process.py` — spawn template, fork, the env-scrub point (P2), bwrap seam (P3)
- `api/src/services/execution/simple_worker.py` — child runtime: Redis context read, pip, `_execute_sync`
- `api/src/services/execution/worker.py` — `authenticate_engine` call replaced by handed-down token (P2)
- `api/src/jobs/consumers/workflow_execution.py` — `context_data`; engine-token field (P2)
- `k8s/worker/deployment.yaml` — P1 securityContext/automount/mounts; P3 seccomp + NetworkPolicy companion
