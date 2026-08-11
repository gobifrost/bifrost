# WP3 Dedicated Build Plane — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move all server-side npm/vite builds out of the API into a secretless `builder-runner` service driven by a credential-light `builder` coordinator, move deploy jobs off in-process FastAPI `BackgroundTasks` onto RabbitMQ, and stage build artifacts in object storage — so a builder turn can produce real deployed entities and the API never runs npm.

**Architecture:** The DB (`solution_build_jobs`) is the queue of record; RabbitMQ carries only "kick" messages. The API mints per-job capability JWTs at claim time via an internal builder API; the coordinator (no DB/S3/JWT-signing credentials) claims jobs, streams a pre-materialized `input.zip` to the runner over plain HTTP, streams the resulting dist files back to the internal artifact API into `_build_artifacts/{build_job_id}/{app_id}/...`, and reports status. Deploy jobs move to a `solution-deploys` queue consumed by the existing worker (which has DB access); after DB commit, staged artifacts are server-side-copied to `_apps/{app_id}/dist/`.

**Tech Stack:** FastAPI, SQLAlchemy (async), aio_pika (existing `src/jobs/rabbitmq.py`), Redis, aiobotocore S3, Node 20 + Vite in the runner image, Python stdlib HTTP server in the runner.

**Design doc:** `docs/superpowers/specs/2026-07-25-private-solution-builder-design.md` — sections "Build system", "Build queue", "Fixed build contract", "Build artifacts", "Runner hardening", "Cleanup and retention", "Resource controls", "Failure semantics", work package 3.

## Documented deviation from the design doc

The design says "Each queue message carries a short-lived, one-job capability minted by the API." This plan instead mints the capability in the response of an internal `POST /claim` endpoint. Reason: the design also requires per-user fair scheduling and cheap cancellation of queued jobs; both require the *dequeue* moment, not the enqueue moment, to pick the job. With DB-as-queue: cancellation of a queued job is a status flip, coordinator-death recovery is a status sweep, and fairness is a claim-time query. The RabbitMQ message degrades to a wake-up signal, which is idempotent and safe to re-publish. The trust boundary is unchanged: the coordinator still holds no DB/S3/JWT-signing credential; it authenticates to exactly two internal route groups (claim/heartbeat via a shared internal secret, job-scoped routes via the minted capability).

## Global Constraints

- Never run raw `docker compose` in a worktree without `COMPOSE_PROJECT_NAME` (duplicate-stack/test-DB-drop hazard). Use `./test.sh` / `./debug.sh`.
- All tests via `./test.sh` (stack: `./test.sh stack up` once per worktree). JUnit XML: `/tmp/bifrost-<project>/test-results.xml`.
- Datetime convention: `datetime.now(timezone.utc)`, `DateTime(timezone=True)`, ORM defaults use lambdas (enforced by `test_datetime_consistency.py`).
- New tables/columns bearing `solution_id` written outside deploy MUST be in `_OPERATIONAL_SOLUTION_ROW_NAMES` in `api/src/services/solutions/guard.py` or every write 500s.
- No dead code, no unrequested fallbacks. When a code path is removed, remove everything only reachable from it.
- Business logic in `api/src/services/`, routers thin. Pydantic contracts in `api/src/models/contracts/`, ORM in `api/src/models/orm/`.
- Migrations run in the init container: after creating one, `docker compose restart bifrost-init` then `api` (per-worktree names `bifrost-debug-<project>-init-1` / `-api-1`).
- Resource defaults (all overridable via settings/env): concurrent builds instance-wide 1 (`BIFROST_BUILDER_MAX_CONCURRENT_BUILDS`), build timeout 600s, build output 100 MiB, captured log 1 MiB, source archive 50 MiB, expanded source 200 MiB, file count 5000, single file 5 MiB.
- Security invariants 2, 3, 4, 12, 13, 14 from the design doc: generated source never executes in the coordinator; the runner holds no platform/storage credential; the API never launches npm for a server build; a build crash cannot OOM the API; runner state is not reused across users/jobs; internal artifact APIs stream (no whole-payload buffering in API memory).
- Existing v1 esbuild bundler (`api/src/services/app_bundler/`) is OUT of scope — it stays in the API. Only the `SolutionAppBuilder` npm/vite path migrates.
- MCP tools are out of scope for this WP (no new entity mutations surface).

## Key existing code (ground truth)

| Thing | Where |
|---|---|
| npm/vite subprocess (the thing being removed) | `api/src/services/solutions/app_build.py` — `SolutionAppBuilder.compile_dist()` (L60-93), `_materialize()` (L125-156), `_run_vite_build()` (L158-188) |
| Deploy orchestration | `api/src/services/solutions/deploy.py` — `SolutionDeployer.deploy()` (L335-538), `_compile_app_dists()` (L1331-1380), `_upload_compiled_dists()` (L1382-1394), `_delete_stale_app_dist()` (L1396-1404) |
| Deploy/install BackgroundTasks | `api/src/routers/solutions.py` — `_run_deploy_job` (L1291), `_run_install_job` (L1412), `_run_install_from_repo_job` (L1540); enqueued at L1726, L2326, L2187 |
| Git sync (already delegates to deployer, no inline npm) | `api/src/services/solutions/git_sync.py` — `sync()` (L203), `_run_sync_once()` (L276) |
| Build job ORM (exists, unexecuted) | `api/src/models/orm/solution_build_jobs.py` — `SolutionBuildJob`, statuses queued/running/succeeded/failed/cancelled/timeout, reuse index `(source_sha256, app_id, toolchain_version)` |
| RabbitMQ primitives | `api/src/jobs/rabbitmq.py` — `publish_message(queue_name, message, priority)` (L630), `BaseConsumer` (L270) |
| Worker consumer registration | `api/src/worker/app.py` — `_start_consumers()` (L133-140) |
| Scheduler sweep template | `api/src/jobs/schedulers/solution_export_jobs.py` (15s process + hourly cleanup) |
| Redis write lock | `api/src/services/solutions/write_lock.py` — `solution_write_lock(solution_id)` |
| Server-side S3 copy precedent | `api/src/services/app_storage.py` — `sync_preview()` L105-109 `client.copy_object(...)` |
| Safe zip extraction | `api/src/services/builder/fs_tools.py` — `safe_extract_zip()`, `WorkspaceRoot`, `WorkspaceLimits` |
| Revision storage | `api/src/services/builder/revision_storage.py` — `SolutionRevisionStorage` |
| Guard list | `api/src/services/solutions/guard.py` — `_OPERATIONAL_SOLUTION_ROW_NAMES` (L61-77) |
| Builder REST router | `api/src/routers/solution_builder.py` (prefix `/api/builder/solutions`) |
| Builder turn service | turn lifecycle invoked from `run_turn` (`solution_builder.py` L312-356) |

---

### Task 1: Build-plane settings, heartbeat, and availability gate

**Files:**
- Modify: the `Settings` class (find it: `rg -n "max_concurrency" api/src --type py -l` → the pydantic settings module) — add builder settings
- Create: `api/src/services/builder/build_plane.py`
- Test: `api/tests/unit/test_build_plane.py`

**Interfaces:**
- Produces (used by Tasks 2, 4, 5, 6):

```python
# api/src/services/builder/build_plane.py
TOOLCHAIN_VERSION = "node20-vite5-v1"  # bump when the runner image toolchain changes

HEARTBEAT_KEY = "bifrost:builder:heartbeat"
HEARTBEAT_TTL_S = 30

def cancel_key(build_job_id: UUID | str) -> str:  # "bifrost:build_job:{id}:cancel"

async def record_builder_heartbeat() -> None:
    """SET HEARTBEAT_KEY = iso-timestamp, EX=HEARTBEAT_TTL_S."""

async def build_plane_available() -> bool:
    """True iff HEARTBEAT_KEY exists in Redis."""

class BuildPlaneUnavailable(Exception):
    """Raised when a server build is required but no coordinator heartbeat exists."""
```

- Settings fields (names exact; env prefix follows the existing `BIFROST_` convention in Settings):
  - `builder_max_concurrent_builds: int = 1`
  - `builder_build_timeout_s: int = 600`
  - `builder_log_limit_bytes: int = 1_048_576`
  - `builder_output_limit_bytes: int = 104_857_600`
  - `builder_staged_retention_hours: int = 6`
  - `builder_internal_secret: str | None = None` (shared secret for coordinator claim/heartbeat routes; None → claim routes 503)
  - `builder_runner_url: str | None = None` (used only by the coordinator process)
  - `internal_api_url: str = "http://api:8000"` (used only by the coordinator process)

**Steps:**

- [ ] **Step 1: Write failing tests** in `api/tests/unit/test_build_plane.py`: `test_build_plane_available_false_without_heartbeat`, `test_build_plane_available_true_after_heartbeat`, `test_heartbeat_expires` (use the existing unit-test Redis fixture — find how other unit tests get Redis, e.g. `rg -n "redis" api/tests/unit/test_*lock*`; if unit tests use a real Redis from the test stack, use it; TTL-expiry test may set TTL=1 and sleep 1.2s), `test_cancel_key_format`.
- [ ] **Step 2: Run** `./test.sh tests/unit/test_build_plane.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement** `build_plane.py` and the Settings fields.
- [ ] **Step 4: Run** the test again → PASS. Also `./test.sh tests/unit/test_datetime_consistency.py`.
- [ ] **Step 5: Commit** `feat(builder): build-plane settings + heartbeat availability gate`.

---

### Task 2: Staged build artifacts storage + build input materialization

**Files:**
- Create: `api/src/services/builder/staged_artifacts.py`
- Create: `api/src/services/builder/build_input.py`
- Create: `api/shared/builder_package_catalog.json`
- Modify: `api/src/services/solutions/app_build.py` (extract `_materialize` internals for reuse — do NOT delete anything yet)
- Test: `api/tests/unit/test_staged_artifacts.py`, `api/tests/unit/test_build_input.py`

**Interfaces:**
- Produces (used by Tasks 3-6, 9):

```python
# api/src/services/builder/staged_artifacts.py
# S3 layout: _build_artifacts/{build_job_id}/input.zip
#            _build_artifacts/{build_job_id}/{app_id}/<dist rel path>
class StagedBuildArtifactStorage:
    def __init__(self, build_job_id: UUID | str, settings: Settings | None = None): ...
    async def write_input(self, path: Path) -> str:
        """Stream local zip → input.zip key. Returns sha256 of the bytes."""
    async def open_input_stream(self) -> AsyncIterator[bytes]:
        """8 MiB chunks of input.zip (raises FileNotFoundError if absent)."""
    async def write_output(self, app_id: UUID | str, rel_path: str, chunks: AsyncIterator[bytes],
                           max_total_bytes: int) -> tuple[str, int]:
        """Stream one dist file to the staged key. Returns (sha256, size).
        Raises BuildOutputTooLarge when cumulative staged bytes for this job exceed max_total_bytes."""
    async def list_outputs(self, app_id: UUID | str) -> list[str]: ...
    async def copy_outputs_to_app_dist(self, app_id: UUID | str, manifest: list[dict]) -> int:
        """Server-side copy_object each manifest entry to _apps/{app_id}/dist/{path},
        then delete _apps/{app_id}/dist/ keys not in the manifest. Returns file count.
        Follows AppStorageService.sync_preview()'s copy pattern (app_storage.py L105-118)."""
    async def delete_job(self) -> int:
        """Batch-delete every key under _build_artifacts/{build_job_id}/ (delete_objects, like
        SolutionRevisionStorage.delete_all_for_solution)."""

class BuildOutputTooLarge(Exception): ...

# api/src/services/builder/build_input.py
class UnsupportedDependency(Exception):
    def __init__(self, offenders: dict[str, str]): ...  # {package: requested_version}

def load_package_catalog() -> dict[str, str]:
    """Parse api/shared/builder_package_catalog.json → {name: exact_version}."""

def validate_dependencies(dependencies: dict[str, str]) -> None:
    """Raise UnsupportedDependency for any package not in the catalog or with a
    version mismatch. Exact-match only — no semver ranges in the first release."""

def materialize_build_input(dest_dir: Path, app_id: UUID | str,
                            src_files: dict[str, bytes], dependencies: dict[str, str]) -> None:
    """Lay out src/, package.json (catalog-pinned deps + vendored SDK tarball ref),
    the Bifrost-owned vite.config.mjs and index.html, and the SDK tarball —
    refactored out of SolutionAppBuilder._materialize (app_build.py L125-156).
    Strips/overrides any user-supplied vite.config.*, postinstall/lifecycle scripts,
    and .npmrc from src_files."""

def make_input_zip(dest_zip: Path, app_id: UUID | str,
                   src_files: dict[str, bytes], dependencies: dict[str, str]) -> str:
    """materialize into a tempdir, zip it deterministically (sorted names, zeroed
    timestamps so the sha is stable for idempotent reuse), return sha256 of the zip."""
```

- `builder_package_catalog.json`: exact-versioned. Seed it with the union of (a) every dependency in the WP2 scaffold's generated `package.json` (read the scaffold source in `api/src/services/builder/` — find with `rg -n "package.json" api/src/services/builder`), (b) the build toolchain: `vite`, `@vitejs/plugin-react`, `typescript`, `tailwindcss` and companions if the scaffold uses them, (c) the runtime deps `_materialize` writes today (`react`, `react-dom`, `react-router-dom`), (d) whatever the vendored SDK tarball's package.json declares as non-dev dependencies. Copy exact versions from what `_materialize`/the scaffold pin today.

**Steps:**

- [ ] **Step 1: Write failing tests.** `test_build_input.py`: catalog loads; `validate_dependencies` passes for scaffold deps, raises `UnsupportedDependency` for `{"leftpad": "1.0.0"}` and for a catalog package at a wrong version; `make_input_zip` output is deterministic (same inputs → same sha twice), contains `package.json` + Bifrost `vite.config.mjs`, and drops a user-supplied `vite.config.ts` and `package.json` `scripts` block. `test_staged_artifacts.py` (against test-stack S3): input round-trip; `write_output` streams and hashes correctly; `write_output` raises `BuildOutputTooLarge` past the cap; `copy_outputs_to_app_dist` lands files under `_apps/{app_id}/dist/` and removes stale keys; `delete_job` empties the prefix.
- [ ] **Step 2: Run** `./test.sh tests/unit/test_build_input.py tests/unit/test_staged_artifacts.py -v` → FAIL.
- [ ] **Step 3: Implement.** Extract the body of `_materialize` into `build_input.materialize_build_input` and make `SolutionAppBuilder._materialize` a thin call to it (behavior identical — compile_dist must still work after this task).
- [ ] **Step 4: Run** step-1 tests → PASS; then `./test.sh tests/unit -k "app_build or solution"` to confirm no regression in existing deploy/build unit tests.
- [ ] **Step 5: Commit** `feat(builder): staged artifact storage + catalog-validated build input`.

---

### Task 3: Internal builder API with capability tokens

**Files:**
- Create: `api/src/routers/internal_builder.py` (register in `api/src/main.py` beside the other routers)
- Create: `api/src/services/builder/capabilities.py`
- Create: `api/src/services/builder/claim.py`
- Modify: core auth `actor_type` registry (find: `rg -n "actor_type" api/src/core` — the default-deny table built in WP1) — register `build_capability` as an internal-only actor type rejected by all normal route dependencies
- Modify: `api/src/models/orm/solution_build_jobs.py` + new alembic migration — add `claimed_at: DateTime(timezone=True) | None` and `last_progress_at: DateTime(timezone=True) | None`
- Test: `api/tests/unit/test_builder_capabilities.py`, `api/tests/e2e/platform/test_internal_builder_api.py`

**Interfaces:**
- Consumes: `StagedBuildArtifactStorage`, `build_plane.record_builder_heartbeat`, settings from Task 1.
- Produces (the coordinator's entire surface, used by Task 4):

Routes (prefix `/api/internal/builder`; excluded from OpenAPI with `include_in_schema=False`):

| Route | Auth | Behavior |
|---|---|---|
| `POST /claim` | header `X-Bifrost-Builder-Key` == `settings.builder_internal_secret` (503 if unset, 403 on mismatch) | Fair-claim next queued job → `{"job": {...} \| null, "capability": "<jwt>" \| null}` |
| `POST /heartbeat` | same | `record_builder_heartbeat()`; 204 |
| `GET /jobs/{job_id}/input` | `Authorization: Bearer <capability>` | `StreamingResponse` of `input.zip` |
| `PUT /jobs/{job_id}/artifacts/{rel_path:path}` | capability | Stream request body → `write_output`; enforces `builder_output_limit_bytes`; rejects `rel_path` containing `..`, leading `/`, or NUL with 400; returns `{"sha256": ..., "size": ...}` |
| `POST /jobs/{job_id}/status` | capability | Body `BuildJobStatusUpdate`; applies transition; terminal states publish Redis pubsub `bifrost:build_job:{job_id}` |
| `POST /jobs/{job_id}/progress` | capability | Touches `last_progress_at` (liveness for the stale sweep); 204 |

```python
# api/src/services/builder/capabilities.py
def mint_build_capability(job: SolutionBuildJob) -> str:
    """JWT signed with the platform's existing signing secret (reuse the helper the
    normal token path uses — rg -n "def create_access_token" api/src).
    Claims: {"actor_type": "build_capability", "job_id": str, "solution_id": str,
             "sub": str(requested_by), "jti": uuid4-hex, "exp": now+15min}."""

async def require_build_capability(job_id: UUID, authorization: str = Header(...)) -> dict:
    """FastAPI dependency: verify signature, actor_type == "build_capability",
    claims["job_id"] == str(job_id). 403 otherwise."""

# api/src/services/builder/claim.py
async def claim_next_build_job(db: AsyncSession) -> SolutionBuildJob | None:
    """Inside one transaction:
    1. candidates = earliest queued job per requesting user:
       SELECT DISTINCT ON (requested_by) * FROM solution_build_jobs
       WHERE status='queued' ORDER BY requested_by, created_at
       FOR UPDATE SKIP LOCKED
    2. fairness: among candidates pick the one whose requested_by has the fewest
       jobs with claimed_at > now()-10min; tie-break oldest created_at.
    3. set status='running', claimed_at=now, started_at=now, last_progress_at=now.
    Returns the claimed job or None."""

# api/src/models/contracts/ — add to the builder contracts module created in WP2
class BuildJobStatusUpdate(BaseModel):
    status: Literal["succeeded", "failed", "timeout", "cancelled"]
    error: str | None = None
    log_excerpt: str | None = None          # server truncates to builder_log_limit_bytes
    output_manifest: list[BuildOutputEntry] | None = None

class BuildOutputEntry(BaseModel):
    path: str
    sha256: str
    size: int

class ClaimedBuildJob(BaseModel):
    id: UUID
    solution_id: UUID
    app_id: UUID
    timeout_s: int
```

Status-transition rule: only `running → succeeded|failed|timeout|cancelled` is accepted from the capability; anything else → 409. On `succeeded` the server independently verifies the manifest paths all exist in staging (list_outputs) before accepting — a manifest naming un-uploaded files is a 400.

**Steps:**

- [ ] **Step 1: Migration** for `claimed_at`/`last_progress_at` (`cd api && alembic revision -m "build job claim columns"`), then write failing unit tests: capability mint/verify round-trip, wrong-job 403, expired 403, wrong actor_type 403; `claim_next_build_job` fairness (create 3 queued jobs for user A then 1 for user B with later created_at, claim twice → A's first then B's; second scenario: A claimed recently → B wins), empty-queue → None.
- [ ] **Step 2: Run** `./test.sh tests/unit/test_builder_capabilities.py -v` → FAIL.
- [ ] **Step 3: Implement** capabilities, claim, contracts, migration, actor_type registration.
- [ ] **Step 4: Unit tests** → PASS.
- [ ] **Step 5: Write failing e2e tests** in `test_internal_builder_api.py` (use the e2e client fixture pattern from `tests/e2e/platform/test_policies.py`): claim without secret → 403; claim with secret over a seeded queued job returns job + capability; capability streams input; artifact PUT stores and hashes; traversal rel_path → 400; status `succeeded` with manifest naming a missing file → 400, with a real uploaded file → job row updated; a `build_capability` token against a normal route (e.g. `GET /api/solutions`) → 403.
- [ ] **Step 6: Run** `./test.sh tests/e2e/platform/test_internal_builder_api.py -v` → PASS.
- [ ] **Step 7: Quality + commit** `./test.sh quality api`; `feat(builder): internal builder API with per-job capability tokens`.

---

### Task 4: The builder-runner service

**Files:**
- Create: `builder_runner/server.py` (Python 3 stdlib only — no pip installs in the runner image)
- Create: `builder_runner/Dockerfile`
- Create: `builder_runner/vite.config.mjs` — canonical copy of the config `materialize_build_input` embeds (single source: `build_input.py` reads this file; find how the current `_materialize` sources its vite config and keep one copy)
- Modify: `docker-compose.dev.yml`, `docker-compose.test.yml` — add `builder-runner` service
- Test: `api/tests/unit/test_runner_protocol.py` (protocol/validation logic), e2e exercised in Task 5

**Interfaces:**
- Produces (consumed by the coordinator, Task 5):

HTTP protocol (runner listens on `:8300`, plain HTTP, internal network only):

| Route | Behavior |
|---|---|
| `POST /build?timeout_s=600` | Body: input.zip bytes. One job at a time — second concurrent request → 409. Unzips to a fresh `tempfile.mkdtemp()`, runs `npm install --offline --ignore-scripts --no-audit --no-fund` then `npx vite build --base <read from build-meta.json inside the zip>`, streams back a zip of `dist/**` plus `build.json` (`{"ok": true, "duration_ms": N, "log_excerpt": "<tail>"}`). On build failure → 422 with JSON `{"error": "...", "log_excerpt": "<tail, capped 1 MiB>"}`. Always purges the temp dir in `finally`. |
| `POST /cancel` | SIGKILL the current npm/vite process group; 204. |
| `GET /healthz` | `{"busy": bool, "toolchain": "node20-vite5-v1"}` |

- `materialize_build_input` (Task 2) must include a `build-meta.json` at zip root: `{"app_id": "...", "base": "/api/applications/<app_id>/dist/"}` — the runner reads `base` from it (mirrors `compile_dist`'s `--base` argument, app_build.py L176-182). Add that to Task 2's implementation if not already done (Task 2's zip contract includes it; verify).
- Runner hardening in compose (mirror the worker service in `docker-compose.test.yml`): `user: "1000:1000"`, `cap_drop: [ALL]`, `security_opt: [no-new-privileges]`, `read_only: true` with `tmpfs: [/tmp]`, `mem_limit: 2g`, `pids_limit: 512`, **no environment variables beyond `PORT`** — no RabbitMQ/Redis/DB/S3/JWT vars. Dev compose: same shape minus `read_only` if it fights npm (keep tmpfs).
- Dockerfile: `FROM node:20-bookworm-slim`; install `python3` (apt, no pip); copy `builder_runner/server.py` and `api/shared/builder_package_catalog.json` (build context = repo root); warm the npm cache at image build: a small RUN loop `npm cache add <name>@<version>` for every catalog entry; set `npm_config_offline=true`, `npm_config_ignore_scripts=true` as ENV; `USER 1000`; `CMD ["python3", "/srv/server.py"]`.
- Subprocess timeout inside the runner: `timeout_s` query param, enforced with `subprocess.run(..., timeout=...)` per step; on timeout → 422 with `"error": "build timed out"`.

**Steps:**

- [ ] **Step 1: Write failing unit tests** in `api/tests/unit/test_runner_protocol.py` for the pure-logic pieces of `server.py` (import it by path with `importlib`): zip-safety — the runner re-validates entries with the same rules as `safe_extract_zip` (no absolute paths, no `..`, no symlinks; reimplement minimally in `server.py` since the runner can't import api code — test both implementations against the same malicious-zip fixtures), response-zip assembly, log-tail capping.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `server.py` (stdlib: `http.server.ThreadingHTTPServer` with a `threading.Lock` for the one-job gate, `zipfile`, `subprocess`, `tempfile`, `shutil`).
- [ ] **Step 4: Unit tests** → PASS.
- [ ] **Step 5: Dockerfile + compose wiring.** Add the service to both compose files. Update `test.sh` image-build step if it builds images explicitly (check: `rg -n "docker.*build" test.sh debug.sh`). Boot it in the worktree's debug stack (`./debug.sh up`) and verify `curl http://<runner>/healthz` from inside the api container (`docker exec bifrost-debug-<project>-api-1 curl -s http://builder-runner:8300/healthz`).
- [ ] **Step 6: Manual smoke:** from the api container, POST a real scaffold input.zip (generate one in a python one-liner via `make_input_zip`) and confirm a dist zip comes back with `index.html` + hashed assets.
- [ ] **Step 7: Commit** `feat(builder): secretless builder-runner service with fixed toolchain`.

---

### Task 5: The builder coordinator service + build request path (with parity proof)

**Files:**
- Create: `api/src/builder_coordinator/__init__.py`, `api/src/builder_coordinator/main.py`, `api/src/builder_coordinator/coordinator.py`
- Create: `api/src/services/builder/build_requests.py`
- Modify: `docker-compose.dev.yml`, `docker-compose.test.yml` — add `builder` service
- Test: `api/tests/unit/test_coordinator.py`, `api/tests/unit/test_build_requests.py`, `api/tests/e2e/platform/test_build_plane.py`

**Interfaces:**
- Consumes: internal API routes (Task 3), runner protocol (Task 4), `publish_message`/`BaseConsumer` (`src/jobs/rabbitmq.py`), Redis cancel keys and heartbeat (Task 1).
- Produces (used by Tasks 6, 8):

```python
# api/src/builder_coordinator/coordinator.py
class BuilderCoordinator:
    """Consumes 'solution-builds' kick messages (message body: {"kick": true}) with
    prefetch = settings.builder_max_concurrent_builds. On each kick — and every 15s
    as a poll fallback — drains the claim endpoint until it returns job=null.

    Per job:
      1. GET input stream → spool to a NamedTemporaryFile
      2. check Redis cancel flag → if set, POST status cancelled and stop
      3. POST the file to runner /build with timeout_s from the claim, wrapped in
         asyncio.wait_for(timeout_s + 30)
      4. on 200: unzip response safely (reuse safe_extract_zip from
         src.services.builder.fs_tools — the coordinator IS api-code, it just runs
         with a stripped environment), PUT each dist file to the artifact route,
         POST status succeeded with the manifest
      5. on 422: POST status failed with error + log_excerpt
      6. on timeout: POST /cancel to runner, POST status timeout
      7. on connection failure to runner (runner OOM/crash): POST status failed
         with error "runner unavailable"
    Sends POST /jobs/{id}/progress every 30s while a job runs.
    Background task: record heartbeat via POST /heartbeat every 10s.
    Env consumed: BIFROST_RABBITMQ_URL, BIFROST_REDIS_URL, internal_api_url,
    builder_internal_secret, builder_runner_url. It must run with NO
    BIFROST_DATABASE_URL and NO S3 credentials — enforce with a startup assertion
    that fails fast if BIFROST_DATABASE_URL or BIFROST_S3_SECRET_KEY is set."""

# api/src/services/builder/build_requests.py
async def request_app_build(db, *, solution_id: UUID, app_id: UUID,
                            src_files: dict[str, bytes], dependencies: dict[str, str],
                            requested_by: UUID | None,
                            source_revision_id: UUID | None = None) -> SolutionBuildJob:
    """1. validate_dependencies (raises UnsupportedDependency)
    2. make_input_zip → sha256
    3. idempotent reuse: a succeeded job with same (source_sha256, app_id,
       TOOLCHAIN_VERSION) whose staged outputs still exist → return it directly
    4. raise BuildPlaneUnavailable if not await build_plane_available()
    5. create SolutionBuildJob(status='queued', source_sha256=..., toolchain_version=
       TOOLCHAIN_VERSION, dependency_digest=sha256(sorted deps json)), flush
    6. write input.zip to StagedBuildArtifactStorage
    7. publish_message('solution-builds', {"kick": True})"""

async def await_build_jobs(db, jobs: list[SolutionBuildJob], *, poll_s: float = 2.0) -> None:
    """Poll (db.refresh) until all terminal. Raise BuildFailed(job) — carrying
    job.error and job.log_excerpt — on any non-succeeded terminal state.
    Overall deadline: builder_build_timeout_s + 120s grace, then BuildFailed(timeout)."""

class BuildFailed(Exception):
    def __init__(self, job: SolutionBuildJob): ...
```

- Compose `builder` service: api image, command `python -m src.builder_coordinator.main` (dev: wrapped in watchmedo like worker/scheduler), env ONLY: `BIFROST_ENVIRONMENT`, `BIFROST_RABBITMQ_URL`, `BIFROST_REDIS_URL`, `BIFROST_BUILDER_INTERNAL_SECRET`, `BIFROST_BUILDER_RUNNER_URL=http://builder-runner:8300`, `BIFROST_INTERNAL_API_URL=http://api:8000`. Add `BIFROST_BUILDER_INTERNAL_SECRET` to the api service env in both compose files.
- `main.py` follows `src/worker/main.py` + `worker/app.py` shape: signal handlers, graceful drain (finish current job, stop claiming).

**Steps:**

- [ ] **Step 1: Write failing unit tests.** `test_coordinator.py`: with a fake internal API + fake runner (aiohttp test servers or monkeypatched client), a claimed job flows to succeeded with a correct manifest; runner 422 → failed with log excerpt; runner timeout → runner /cancel called + status timeout; cancel flag set before dispatch → status cancelled; startup assertion raises when `BIFROST_DATABASE_URL` is set. `test_build_requests.py`: reuse hit returns the prior job without publishing; `UnsupportedDependency` propagates; no heartbeat → `BuildPlaneUnavailable`; `await_build_jobs` raises `BuildFailed` on failed job.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Unit tests** → PASS.
- [ ] **Step 5: Compose wiring** for the `builder` service (dev + test). `./debug.sh up` and confirm heartbeat: `docker exec bifrost-debug-<project>-api-1 python -c` snippet checking `bifrost:builder:heartbeat` exists in Redis.
- [ ] **Step 6: Write the e2e parity + lifecycle test** `test_build_plane.py`: (a) **parity** — build the scaffold fixture twice, once via `SolutionAppBuilder.compile_dist` (still alive until Task 8) and once via `request_app_build`+`await_build_jobs`, and assert the two dists have the same file-name *shapes* (same set of non-hash-suffixed names, an `index.html` whose script/css references resolve) — byte-identity is not required (vite hashes may differ across environments), structural parity is; (b) **lifecycle** — job rows go queued→running→succeeded with populated manifest and staged files present; (c) **reuse** — second identical request returns the same job id; (d) **unavailable** — with heartbeat key deleted, `request_app_build` raises `BuildPlaneUnavailable`; (e) **failure** — a src file with a syntactically-broken import → `BuildFailed` with non-empty log excerpt, and API stays healthy (a trivial healthcheck request succeeds).
- [ ] **Step 7: Run** `./test.sh e2e tests/e2e/platform/test_build_plane.py` → PASS (note: `./test.sh e2e <path>` may run the whole suite — check `feedback_test_sh_quirks`; if so run the passthrough form `./test.sh tests/e2e/platform/test_build_plane.py`).
- [ ] **Step 8: Quality + commit** `feat(builder): coordinator service + build request path with parity test`.

---

### Task 6: Deploy path consumes the build plane (no more in-API compile)

**Files:**
- Modify: `api/src/services/solutions/deploy.py` — `SolutionDeployer.deploy()` (L335-538), replace `_compile_app_dists` (L1331-1380) and `_upload_compiled_dists` (L1382-1394)
- Modify: `api/src/routers/solutions.py` — surface `UnsupportedDependency` / `BuildPlaneUnavailable` / `BuildFailed` as deploy-job failure states with distinct messages; `BuildPlaneUnavailable` at request-validation time → HTTP 503
- Test: extend `api/tests/e2e/platform/test_build_plane.py`; modify existing deploy unit/e2e tests that assume in-process compile

**Interfaces:**
- Consumes: `request_app_build`, `await_build_jobs`, `StagedBuildArtifactStorage.copy_outputs_to_app_dist`, `BuildFailed`, `BuildPlaneUnavailable`.
- Produces: `SolutionDeployer.deploy()` keeps its exact signature and `DeployResult` shape — callers (routers, git_sync, install paths) are untouched by this task.

New flow inside `deploy()` (replacing the compile phase at L500):

1. Partition app builds exactly as `_compile_app_dists` does today into prebuilt (`prebuilt_dist` present → skip build plane entirely, upload as today) and source-builds.
2. For source-builds: `request_app_build(...)` each (validation errors abort the deploy before any DB write), then `await_build_jobs(...)` — all **before** the DB commit, mirroring today's "build before commit" atomicity (a failed build must leave the DB untouched, same as `SolutionDeployConflict` today).
3. `DeployResult.finalize_s3` (post-commit): for each built app, `copy_outputs_to_app_dist(app_id, job.output_manifest)` then `staged.delete_job()`; prebuilt apps keep the existing `upload_dist` path; stale-app dist deletion unchanged (`_delete_stale_app_dist`).

**Steps:**

- [ ] **Step 1: Write failing e2e tests** (extend `test_build_plane.py`): full deploy of a source-only v2 app lands entities AND a servable dist (`GET /api/applications/{app_id}/dist/index.html` → 200); deploy with heartbeat deleted → 503 build-unavailable and no partial DB state; prebuilt-dist deploy succeeds with the heartbeat deleted (CLI path unaffected); deploy with an uncataloged dependency → clear unsupported-dependency error naming the package; failed build (broken import) → deploy job failed, solution's previous entities/dist unchanged.
- [ ] **Step 2: Run** → FAIL (deploy still compiles in-process, so the 503/unsupported cases fail).
- [ ] **Step 3: Implement** the deploy() rewiring. Delete `_compile_app_dists` and `_upload_compiled_dists`; keep `SolutionAppBuilder.upload_dist/read_dist/list_dist/delete_dist` (still used for prebuilt + serving). `compile_dist` itself survives until Task 8 (the parity test uses it).
- [ ] **Step 4: Run** step-1 tests → PASS, then the full deploy-related e2e subset (`./test.sh tests/e2e/platform/test_solutions*.py` and any git-sync/install e2e — find with `rg -l "deploy" api/tests/e2e/platform`) to catch regressions. Existing tests that exercised in-process compile need the test stack's build plane (builder + builder-runner services) — they should pass unmodified if compose wiring from Tasks 4-5 is complete; fix any that stub `SolutionAppBuilder.compile_dist` to stub the build plane instead.
- [ ] **Step 5: Quality + commit** `feat(builder): route server-side app builds through the build plane`.

---

### Task 7: Deploy jobs onto RabbitMQ (off BackgroundTasks), git sync included

**Files:**
- Create: `api/src/services/solutions/deploy_jobs.py` (bodies of the three `_run_*_job` functions move here, generalized)
- Create: `api/src/services/solutions/deploy_uploads.py` (`DeployUploadStorage` — `_deploy_uploads/{deploy_job_id}/source.zip`, same shape as `SolutionSourceArtifactStorage`: `write_from_path`, `copy_to_path`, `delete`)
- Create: `api/src/jobs/consumers/solution_deploy.py` (`SolutionDeployConsumer(BaseConsumer)`, queue `solution-deploys`)
- Modify: `api/src/worker/app.py` `_start_consumers()` (L133-140) — register the consumer
- Modify: `api/src/routers/solutions.py` — the three endpoints stage input to S3, create the job row, `publish_message('solution-deploys', {"deploy_job_id": str(job.id)})`, return 202 (unchanged response shape); delete the `BackgroundTasks` usage and the in-router `_run_*_job` bodies
- Modify: the `SolutionDeployJob` ORM (find it: `rg -n "class SolutionDeployJob" api/src/models/orm`) + migration — add `kind: str` (`"deploy" | "install" | "install_from_repo" | "git_sync"`) and `params: JSONB | None` (install/from-repo/git-sync parameters: target org, force flag, repo url/ref/subpath, etc.)
- Modify: `api/src/services/solutions/git_sync.py` — `sync()` becomes "create job row + publish"; `_run_sync_once` body moves into `deploy_jobs.py`; update its callers (scheduler `check_solution_updates` and any manual-sync endpoint — find with `rg -n "git_sync|_run_sync_once|\.sync\(" api/src`)
- Create: `api/src/jobs/schedulers/deploy_job_recovery.py` — periodic sweep (register in `scheduler/main.py` beside `cleanup_stuck_executions`, every 5 min): jobs `status='running'` with no progress for > 30 min → mark failed "worker died mid-deploy" (the write lock's 60s TTL has long expired so a retry is safe); jobs `status='queued'` older than 10 min → re-publish the kick (idempotent).
- Test: `api/tests/unit/test_deploy_jobs.py`, extend e2e

**Interfaces:**
- Consumes: `publish_message`, `BaseConsumer`, `solution_write_lock`, `deploy_from_workspace`/`deploy_zip_to_solution_path` (existing, unchanged), `DeployUploadStorage`.
- Produces:

```python
# api/src/services/solutions/deploy_jobs.py
async def run_deploy_job(db: AsyncSession, deploy_job_id: UUID) -> None:
    """Dispatch on job.kind:
    - deploy / install: DeployUploadStorage(job.id).copy_to_path(tmp) → existing
      deploy body (write lock → deploy → commit → finalize_s3) → delete upload
    - install_from_repo: clone_repo_to_dir(params) inside this job (moved from the
      router request handler), then deploy_from_workspace
    - git_sync: former _run_sync_once body
    Sets job status/started_at/completed_at exactly as the old bodies did.
    Idempotency on redelivery: if job.status is terminal, ack and return; if
    'running' and the write lock is held, requeue-with-delay is NOT available in
    BaseConsumer — instead nack(requeue=False) is wrong; simply return after
    logging (the recovery sweep owns abandoned 'running' jobs)."""
```

- Message schema for `solution-deploys`: `{"deploy_job_id": "<uuid>"}` — everything else from the DB row.
- The three router endpoints keep all their **validation** inline (zip syntax checks, slug conflicts, etc. — whatever runs before the background task today stays synchronous so the 4xx behavior is unchanged). Only the long-running body moves.

**Steps:**

- [ ] **Step 1: Write failing unit tests** `test_deploy_jobs.py`: kind dispatch calls the right body (monkeypatched); terminal-status redelivery is a no-op; recovery sweep flips a stale running job to failed and re-kicks an old queued job (assert `publish_message` called — monkeypatch).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (move code, don't rewrite it — `git diff` should show the bodies relocating with staging/params changes only). Migration for `kind`/`params`.
- [ ] **Step 4: Unit tests** → PASS.
- [ ] **Step 5: E2E** — the existing deploy/install/from-repo/git-sync e2e suites now exercise the worker path end-to-end (worker runs in the test stack). Run the full backend e2e: `./test.sh e2e`. Fix fallout — likely spots: tests that awaited BackgroundTasks completion implicitly via the request lifecycle must now poll the job status endpoint (there is an existing job-status polling endpoint for deploys — find with `rg -n "deploy.*job" api/src/routers/solutions.py | head`).
- [ ] **Step 6: Quality + commit** `feat(solutions): deploy jobs survive pod restarts via RabbitMQ`.

---

### Task 8: Remove npm from the API

**Files:**
- Modify: `api/src/services/solutions/app_build.py` — delete `compile_dist`, `build`, `_materialize`, `_run_vite_build`, `_BUILD_STEP_TIMEOUT_S`, the `subprocess`/`tempfile` imports; class keeps only dist read/upload/list/delete
- Modify: `api/tests/e2e/platform/test_build_plane.py` — parity test now compares against a **golden fixture manifest** (record the structural expectations, not compile_dist output)
- Test: `api/tests/unit/test_no_npm_in_api.py`

**Interfaces:**
- Consumes: nothing new. Produces: the guarantee (security invariant 4).

```python
# api/tests/unit/test_no_npm_in_api.py
def test_solution_app_builder_has_no_compile_path():
    from src.services.solutions.app_build import SolutionAppBuilder
    assert not hasattr(SolutionAppBuilder, "compile_dist")
    assert not hasattr(SolutionAppBuilder, "_run_vite_build")
    import src.services.solutions.app_build as mod, inspect
    src_text = inspect.getsource(mod)
    assert "subprocess" not in src_text and "npm" not in src_text

def test_no_vite_or_npm_subprocess_outside_allowed():
    """rg-equivalent scan: every file under api/src/** containing the string
    'npm install' or 'npx vite' must be in the allowlist
    {'services/app_bundler/...'}  # esbuild bundler only; assert the scan of
    api/src/services/solutions/ and api/src/routers/ finds zero hits."""
```

**Steps:**

- [ ] **Step 1: Write the failing assertion tests** above → FAIL (compile_dist exists).
- [ ] **Step 2: Rework the parity e2e** to golden-structural assertions (dist contains `index.html`, ≥1 hashed `.js` asset, every script/css href in index.html resolves against the manifest).
- [ ] **Step 3: Delete** the compile path and everything only reachable from it (project rule). `rg -n "compile_dist|_run_vite_build|_materialize" api/` must return only `build_input.py`'s extracted function and tests of it.
- [ ] **Step 4: Run** `./test.sh tests/unit/test_no_npm_in_api.py -v` → PASS; `./test.sh all` → green.
- [ ] **Step 5: Quality + commit** `feat(builder): the API never runs npm — compile path removed`.

---

### Task 9: Cancellation, stale-job sweep, and staged-artifact cleanup

**Files:**
- Modify: `api/src/routers/solution_builder.py` — add `POST /{solution_id}/builds/{build_job_id}/cancel` (owner-gated via the router's existing `require_builder` + solution-access pattern) and `GET /{solution_id}/builds` (list jobs w/ status, for the UI)
- Create: `api/src/jobs/schedulers/build_jobs_sweep.py` — register in `scheduler/main.py`: every 5 min — (a) queued jobs older than 10 min → re-kick; (b) running jobs with `last_progress_at` older than `builder_build_timeout_s + 120s` → status `failed`, error "coordinator lost"; every hour — (c) delete `_build_artifacts/{job_id}/` for jobs completed more than `builder_staged_retention_hours` ago (query terminal jobs, `StagedBuildArtifactStorage(job.id).delete_job()`)
- Modify: `api/src/services/builder/build_requests.py` — `cancel_build_job(db, job)`: queued → set `cancelled` directly; running → set Redis cancel flag (`cancel_key(job.id)`, EX=timeout) — the coordinator observes it between phases and the runner gets `POST /cancel`
- Test: `api/tests/unit/test_build_jobs_sweep.py`, extend `api/tests/e2e/platform/test_build_plane.py`

**Interfaces:**
- Consumes: `cancel_key` (Task 1), sweep-registration pattern (`solution_export_jobs.py`), `StagedBuildArtifactStorage.delete_job`.
- Produces: `cancel_build_job(db, job: SolutionBuildJob) -> None`; routes above; contracts `BuildJobPublic` (id, app_id, status, error, created_at, started_at, completed_at) added to the builder contracts module.

**Steps:**

- [ ] **Step 1: Failing unit tests:** queued-cancel flips status without Redis; running-cancel sets the flag; sweep (a) re-kicks, (b) fails stale running jobs, (c) deletes staged prefixes only for jobs past retention.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4:** → PASS.
- [ ] **Step 5: E2E:** cancel a queued job through the REST route as the owner → status cancelled and never runs; non-owner → 404 (private access invariant).
- [ ] **Step 6: Regenerate client types** (`cd client && npm run generate:types` — dev stack must be up; use the worktree stack's URL from `./debug.sh status`).
- [ ] **Step 7: Quality + commit** `feat(builder): build cancellation + stale sweep + staged artifact retention`.

---

### Task 10: Builder turn → build → private deploy → live preview

**Files:**
- Modify: the turn-completion service (where a turn commits its revision — find: `rg -n "output_revision_id" api/src/services/builder/`) — after the revision commit, create a `kind="deploy"`-style private deploy from the revision: stage the revision zip as the deploy input (`DeployUploadStorage`), create a `SolutionDeployJob(kind="deploy", ...)` bound to the solution, publish; record `deploy_job_id` on the turn; the deploy path itself requests builds (Task 6), so `build_job_id` on the turn is set from the deploy's build jobs (first app's job id) or left null when no app changed — check the turn ORM contract (`SolutionBuilderTurn.build_job_id/deploy_job_id`, no FKs) and populate both when known
- Modify: `api/src/routers/solution_builder.py` — turn status/detail response includes build+deploy job states and, when the deployed app exists, the app-host launch info the UI needs (reuse the launch-code flow already built on this branch — find: `rg -n "launch" api/src/routers/solution_app_host.py`)
- Modify: `client/src/pages/SolutionBuilder.tsx` (or wherever `const appOrigin: string | null = null` lives — `rg -n "appOrigin" client/src`) — wire the real app origin from the API response; preview pane renders the deployed app through the launch flow once a turn's deploy succeeds
- Modify: client service for the builder (in `client/src/services/`) — new fields/endpoints
- Test: e2e `api/tests/e2e/platform/test_build_plane.py::test_turn_produces_entities` (drive: create private solution → run a scripted turn via the existing test path that fakes the model — find how existing turn e2e tests stub the LLM: `rg -n "turn" api/tests/e2e/platform/ -l` → follow that pattern → assert the deploy job runs, entities exist (app row, table rows if scaffold defines any), dist serves); client vitest for the changed components; regen types
- **Private-deploy correctness:** the deploy must run with the private side-effect suppression + post-condition assertion already built on this branch (status doc "Private deploy side-effect suppression") — the moved deploy body (Task 7) must preserve that flag/path for builder-originated deploys; assert in the e2e that no shared role/schedule/event was created (query for them).

**Interfaces:**
- Consumes: everything above. Produces: the WP3 exit criterion — a turn yields real entities and a renderable preview.

**Steps:**

- [ ] **Step 1: Failing e2e** `test_turn_produces_entities` (backend) → FAIL (turn never deploys today).
- [ ] **Step 2: Implement backend wiring** (turn → staged deploy job → statuses on turn responses).
- [ ] **Step 3: Backend e2e** → PASS.
- [ ] **Step 4: Client wiring** (appOrigin + turn status polling → preview refresh on deploy success). Regen types first (`npm run generate:types`), then `npm run tsc`, `npm run lint`, targeted vitest (`./test.sh client unit`).
- [ ] **Step 5: Live drive** (mandatory, per drive-dont-just-test): `./debug.sh up`, create a private solution in the browser, run a real turn (OpenRouter configured), watch build+deploy jobs complete, see the preview render. Screenshot the working preview.
- [ ] **Step 6: Quality + commit** `feat(builder): turns build, deploy, and render a live preview`.

---

## Final gate (orchestrator-owned, not a task)

- `./test.sh quality api` → 0 errors; `./test.sh all` → green; `./test.sh client unit` → green; `npm run tsc` + `npm run lint` → clean; regen types committed.
- Adversarial spot-checks from the design's operational list: kill the runner mid-build (docker kill) → job fails, API healthcheck green; kill the coordinator mid-build → stale sweep fails the job within its window; `docker exec` into the runner → assert env has no BIFROST_/AWS_/JWT vars.
- Update `docs/superpowers/specs/2026-07-27-private-solution-builder-status.md`: WP3 → built/verified, live-preview gap closed, next = WP4.

## Self-review notes

- Spec coverage: no-npm ✔ (T8), coordinator/runner split + hardening ✔ (T4/T5), queue semantics: concurrency ✔ (prefetch, T5), fairness ✔ (claim, T3), cancellation ✔ (T9), timeout ✔ (T4/T5/T9), cleanup ✔ (T9); staged artifacts + post-commit copy ✔ (T2/T6); idempotent reuse ✔ (T5); caller migration: direct deploy/install/from-repo ✔ (T6/T7), git sync ✔ (T7), builder ✔ (T10); deploy off BackgroundTasks ✔ (T7); 503-unavailable ✔ (T6); prebuilt-CLI path preserved ✔ (T6); WP3 exit (runner OOM only fails the job; API green) ✔ (T5 step 6e + final gate).
- Known deferrals (explicit, not gaps): k8s manifests for builder/builder-runner (repo has k8s/ but prod rollout is release-owned — flag in the status doc); per-user quotas beyond fairness; `shared_data_access` (post-POC by design); failed-revision representation (design's accepted sharp edge).
