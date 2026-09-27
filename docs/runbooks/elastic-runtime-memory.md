# Elastic Runtime Memory Diagnostic

Use `api/scripts/elastic_runtime_memory.py` to measure Bifrost runtime
memory cost in fresh Python processes. It is intended for the Kubernetes Elastic
Runtime spike, where we need worker, scheduler, and PlatformJob import baselines
before designing lean executor pods.

The diagnostic is stdlib-only until a child process runs a selected stage. Each
stage runs in a separate Python interpreter, so a later stage does not inherit
imports from an earlier one.

See [the worker memory reduction results](worker-memory-reduction-results.md) for
measured whole-container changes and the limits of adding import-probe figures.

## Quick Start

From a Bifrost image or a worktree with API dependencies installed:

```bash
python3 api/scripts/elastic_runtime_memory.py
```

Emit machine-readable JSON:

```bash
python3 api/scripts/elastic_runtime_memory.py --format json
```

List stages:

```bash
python3 api/scripts/elastic_runtime_memory.py --list-stages
```

Measure only selected stages:

```bash
python3 api/scripts/elastic_runtime_memory.py \
  --stage python \
  --stage worker-app-import \
  --stage construct-worker-consumers \
  --stage scheduler-import \
  --stage selected-platform-job \
  --stage application-sdk-update-module
```

Measure a specific PlatformJob definition:

```bash
python3 api/scripts/elastic_runtime_memory.py \
  --stage selected-platform-job \
  --platform-job-type application.sdk_update
```

## What It Reports

Each row includes:

- `rss`: process resident set from `/proc/<pid>/status` `VmRSS`.
- `pss`: proportional set size from `/proc/<pid>/smaps_rollup` `Pss`.
- `cgroup_current`: cgroup v2 memory usage from `memory.current`.
- `cgroup_peak`: cgroup v2 memory peak from `memory.peak`.
- `working_set`: a cgroup v2 estimate from `memory.current - inactive_file`,
  matching the cAdvisor/Kubernetes working-set convention.
- `elapsed_ms`: time spent running the stage handler after child startup.

The JSON format also includes the full cgroup v2 `memory.stat` map, cgroup path,
Python version, child PID, and any stage error type. Stage errors deliberately
omit exception messages and child stdout/stderr so import failures cannot leak
secret-bearing settings or environments.

## Stages

| Stage | Meaning |
| --- | --- |
| `python` | Python interpreter after stdlib diagnostic startup. |
| `config` | Bifrost configuration imported and settings loaded. |
| `database` | Database layer imported and SQLAlchemy mappers configured. |
| `rabbitmq` | RabbitMQ transport module imported. |
| `workflow-consumer-import` | Workflow execution consumer class imported. |
| `worker-app-import` | Full worker app module imported. |
| `construct-worker-consumers` | All worker consumer instances constructed without starting IO. |
| `scheduler-import` | PlatformJob scheduler module imported. |
| `platform-registry` | PlatformJob registry imported. |
| `selected-platform-job` | Selected PlatformJob definition resolved. |
| `application-sdk-update-module` | `application.sdk_update` handler module imported directly. |

`construct-worker-consumers` intentionally constructs the existing worker
consumers but does not call `start()`. It should not connect to RabbitMQ, start
the workflow template process, or install dependencies.

`selected-platform-job` resolves the registered definition for the requested job
type. For `application.sdk_update`, this measures import and definition cost
through the current registry, including any eager registry imports.

`application-sdk-update-module` imports the fixed `application.sdk_update`
handler module directly. Use it beside `platform-registry` and
`selected-platform-job` to estimate the benefit of lazy PlatformJob definition
loading. It still does not claim a PlatformJob row, run an SDK build, update an
app, or exercise retries/cancellation. That is deliberate: the spike still needs
a safe remote attempt ownership design before protected jobs are executed
outside the current scheduler runner.

## Running Inside Docker Or Kubernetes

The most useful numbers come from the same image and cgroup shape as the runtime
being evaluated. Examples:

```bash
docker compose exec api \
  python3 api/scripts/elastic_runtime_memory.py --format json
```

For Kubernetes, run the script in the Bifrost image used by the spike, either
with `kubectl exec` into a diagnostic pod or as the pod command:

```bash
kubectl exec -n <namespace> <pod> -- \
  python3 api/scripts/elastic_runtime_memory.py --format json
```

When run on the host, cgroup fields describe the host service cgroup that owns
the script process. That is useful for parser validation but not for container
or pod memory budgeting.

The diagnostic targets cgroup v2 only, matching the Kubernetes design spike. It
fails closed when cgroup v2 memory files are unavailable.

## Interpreting Results

Compare rows by fresh-process stage, not by subtracting cgroup values inside one
long-lived process. RSS and PSS are process-level measurements; cgroup current,
peak, and stat values are container or pod accounting. They answer related but
different questions:

- RSS shows pages resident in this process.
- PSS divides shared pages across processes and is better for attributing import
  cost when libraries are shared.
- Cgroup current and peak are what container memory limits and OOM behavior see.
- Working-set estimates depend on kernel labels and are not identical to
  Kubernetes `kubectl top` output.

For worker bloat attribution, start with these comparisons:

```bash
python3 api/scripts/elastic_runtime_memory.py \
  --stage python \
  --stage config \
  --stage database \
  --stage rabbitmq \
  --stage workflow-consumer-import \
  --stage worker-app-import \
  --stage construct-worker-consumers
```

For scheduler and PlatformJob attribution:

```bash
python3 api/scripts/elastic_runtime_memory.py \
  --stage python \
  --stage scheduler-import \
  --stage platform-registry \
  --stage selected-platform-job \
  --stage application-sdk-update-module \
  --platform-job-type application.sdk_update
```

## Limitations

- The diagnostic does not start RabbitMQ consumers, scheduler loops, workflow
  template processes, or PlatformJob runners.
- It does not measure live execution memory, SDK build memory, queue drain
  behavior, cancellation, retries, Kubernetes scheduling, or image pull time.
- `memory.peak` is a cgroup-level peak and may include earlier work in the same
  container. Use a fresh diagnostic pod when peak attribution matters.
- PSS requires `/proc/<pid>/smaps_rollup`; if the kernel or container policy
  hides it, `pss` is reported as `n/a`.
- Dependency hydration and package-install behavior are not exercised.
