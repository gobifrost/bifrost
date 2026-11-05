# Kubernetes settings: implementation plan

Implements `2026-10-24-kubernetes-settings.md` (approved). Tasks run in order
on branch `kubernetes-settings`; each ends green on its own tests and
`./test.sh quality api`. Where this plan and the design disagree, the design
wins.

## Decisions made while planning

- **Storage.** Keep the one `SystemConfig` row (`kubernetes` /
  `remote_execution`). Its `value_json` gains the policy fields and an
  `imported_from_env` map (`{field: {"variable": ..., "at": ...}}`). Every
  write merges into the stored payload under
  `pg_advisory_xact_lock(hashtext('kubernetes/remote_execution'))`, as
  `ai_behavior_service.update_behavior` does. Today `_save_state` replaces
  the whole payload; that must not survive.
- **One reader.** `load_kubernetes_policy(db) -> KubernetesPolicy` (a frozen
  dataclass: warm_workers, max_workers, worker_memory_mib,
  largest_worker_mib, idle_seconds, isolated_jobs_enabled,
  max_isolated_pods, enabled_job_types, max_concurrency). Stored value, else
  the default. Controllers call it each pass.
- **Policy fields leave `Settings`.** `pool_min_workers`, `pool_max_workers`,
  `pool_worker_memory_limit_mib`, `pool_worker_max_memory_mib`,
  `pool_idle_seconds`, `platform_build_backend`, `kubernetes_build_max_jobs`,
  `kubernetes_build_job_types`, `kubernetes_build_memory_request_mib` and
  `kubernetes_build_memory_limit_mib` are deleted from `config.py`. The
  import reads them from `os.environ` (case-insensitive, empty = unset), so
  nothing else can read them.
- **Import.** `import_kubernetes_policy_from_env(db, environ)` takes
  `pg_advisory_xact_lock(hashtext('bifrost:kubernetes-settings:import'))`,
  then for each field absent from the payload and set in env, stores the
  parsed value with provenance. An unparsable value is skipped with a
  warning. Toggles: absent `enabled_job_types` with
  `BIFROST_KUBERNETES_BUILD_JOB_TYPES` set stores defaults ∩ that list.
  `BIFROST_PLATFORM_BUILD_BACKEND=kubernetes` imports isolation on, `local`
  imports off. Runs in `ControlPlane.start` before `start_kubernetes_loops`
  and in `PoolWorker.run` before its `start_kubernetes_loops` (static
  executors). An admin edit clears that field's provenance.
- **Renamed identity variables.** `kubernetes_job_namespace`, `_image`,
  `_configmap`, `_secret`, `_service_account`, `_cpu_request`, `_cpu_limit`,
  `_pending_timeout_seconds`, each with
  `validation_alias=AliasChoices("BIFROST_KUBERNETES_JOB_X",
  "BIFROST_KUBERNETES_BUILD_X")`.
- **Unread report.** `unread_environment_variables(environ, *,
  broker_drained)` returns the sorted names that are set (non-empty) and
  not read: the imported policy variables, the removed ones, v1.4.1's
  `BIFROST_MAX_CONCURRENCY`, `BIFROST_MAX_WORKERS`,
  `BIFROST_MAX_SERVICE_WORKERS`, and `BIFROST_RABBITMQ_URL` once the drain
  is recorded. `renamed_environment_variables(environ)` returns old names
  in use with their new names. `app_lifespan` logs one warning for each
  list (API only). The Kubernetes Settings response carries the unread list
  for the notice.
- **Broker drain record.** `drain_legacy_broker` writes `SystemConfig`
  (`runtime` / `legacy_broker_drained`, `{"drained_at": ...}`) after a drain
  that reached the broker and finished, and returns immediately when the
  record exists. Runbook: a rollback to a release that queues to RabbitMQ
  needs the row deleted.
- **Routes.** `GET /api/admin/kubernetes/execution` returns the policy,
  provenance, job types and `unread_variables`. New
  `PUT /api/admin/kubernetes/policy` takes a partial `KubernetesPolicyUpdate`
  (validated: warm ≤ max, worker memory ≤ largest worker, ranges).
  `PUT /api/admin/kubernetes/execution` stays for per-type toggles and
  concurrency. `KubernetesStatus` loses `backend`; `configured` comes from a
  per-process cached `may_manage_jobs`. Regenerate the access list,
  operation inventory, OpenAPI digest and client types.
- **Detection notice.** Delete the code and its client explainer; a
  migration deletes the stored `kubernetes` / `detection_notice` rows.

## Task 1: policy core (backend)

Storage merge, `KubernetesPolicy`, import, aliases, unread and renamed
reports, startup warnings, broker drain record, routes and contracts,
`may_manage_jobs` cache, detection notice removal (code + migration),
`decide_execution_backend` reads `isolated_jobs_enabled` and the toggles
only (no allowlist, no fallback). Delete `kubernetes_remote_job_types`,
`DEFAULT_KUBERNETES_JOB_TYPES`, `allowed_by_deployment`,
`is_kubernetes_configured`.

Tests: each field imports; a stored value is never overwritten; a narrowed
allowlist stays narrowed; two concurrent imports store once; provenance
clears on edit; a toggle save keeps policy fields; unread and renamed
reports (empty values ignored; RabbitMQ only after the record); aliases;
drain record written and honoured; policy validation; status gate.

## Task 2: controllers and workers read Settings

`WorkerPoolController` loads the policy at the start of each pass and uses
it for warm, max, worker memory and largest worker (`_limits`,
`launch_for_demand`, `replace_outdated`, the published launchable budget).
`build_worker_job_manifest` takes the standard limit as an argument and no
longer injects `BIFROST_POOL_MIN_WORKERS` or `BIFROST_POOL_IDLE_SECONDS`.
`PoolWorker` reads warm workers inside `begin_idle_drain`'s lock and idle
time from the policy (refreshed at most every 10 s).

Tests: a Max workers change applies on the next pass; lowering Warm
workers drains a running idle worker; the manifest carries no policy env.

## Task 3: isolated jobs

- The build loop always starts where the API may manage Jobs. It resolves
  `resolve_worker_job_spec` at loop start; `build_job_manifest(claim, spec,
  ...)` uses that spec; `validate_build_settings` is deleted; the claim
  stamps the client's namespace.
- Each pass reads the policy. Off: reconcile placed rows, claim nothing,
  and move queued kubernetes rows with no Job name to local.
  `remote_limit` = Max isolated pods.
- Sizing through the worker rule (shared helper with the pool controller):
  standard pod request `pool_worker_memory_request_mib` / limit Worker
  memory; a requirement above the standard budget gets
  `sized_job_limit_bytes(...)` with request = limit. The "Waiting for
  Kubernetes capacity" wait on `remote_memory_bytes` is removed.
  `fail_platform_jobs_that_never_fit` gains a kubernetes query against the
  largest pod budget (independent of `largest_budget`).
- Every isolated type gets a memory profile key; an OOM-killed pod learns
  at least its limit plus the profile margin.

Tests: standard, sized and too-large pods; OOM learning for each type; off
stops admitting, finishes running pods, moves queued rows; no identity
variables copies the API pod.

## Task 4: Settings → Kubernetes page

Scaling card (Warm workers, Max workers, Worker memory, Largest worker,
Idle time), Isolated Jobs card (on/off, Max isolated pods, the per-type
toggles and concurrency), provenance ("Imported from BIFROST_POOL_MAX_WORKERS
on Oct 24, 2026"), and the "No longer used; remove from your environment"
notice. Section shown when `configured`. Vitest for the service and page.

## Task 5: shipped files, docs, drills

Remove the retired variables from `deploy/kubernetes/builds/build-config.yaml`
(identity renamed to `JOB_*`), `k8s/local/build-jobs.yaml`,
`k8s/local/config.yaml` (idle time moves to the drill setup) and
`docker-compose.debug.yml`; update the manifest tests, runbooks
(`kubernetes-build-jobs.md`, `kubernetes-worker-deployment.md`,
`k8s/local/README.md`) and architecture docs. Kind drills: a sized isolated
build past 2 GiB; Max workers from Settings without a restart; isolation
off finishes running pods and moves queued ones; the existing pool-drills.
