"""Read-only storage and connection evidence for observational capacity work.

``collect`` is deliberately independent of soak orchestration so callers can
capture the same bounded frame before a run, after a run, or periodically.
Every successful metric has a stated measurement basis.  A capability that the
benchmark cannot safely observe is represented as ``unmeasured`` with a reason;
an unexpected command or query failure propagates and invalidates the frame.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import time
from pathlib import Path

import guard


HERE = Path(__file__).resolve().parent
METRICS = HERE / "incluster" / "storage_metrics.py"

POSTGRES_SQL = """
select json_build_object(
  'database', json_build_object(
    'name', current_database(),
    'bytes', pg_database_size(current_database()),
    'wal_retained_bytes', (select coalesce(sum(size), 0) from pg_ls_waldir()),
    'data_directory', current_setting('data_directory'),
    'settings', (select json_object_agg(name, setting order by name)
                 from pg_settings
                 where name = any(array['archive_mode', 'checkpoint_timeout', 'max_wal_size', 'min_wal_size']))
  ),
  'relations', coalesce((
    select json_agg(json_build_object(
      'schema', n.nspname, 'name', c.relname, 'kind', c.relkind,
      'total_bytes', pg_total_relation_size(c.oid),
      'heap_bytes', pg_relation_size(c.oid),
      'index_bytes', pg_indexes_size(c.oid),
      'toast_bytes', case when c.reltoastrelid = 0 then 0 else pg_total_relation_size(c.reltoastrelid) end,
      'live_tuples_estimate', s.n_live_tup, 'dead_tuples_estimate', s.n_dead_tup,
      'last_vacuum', s.last_vacuum, 'last_autovacuum', s.last_autovacuum,
      'last_analyze', s.last_analyze, 'last_autoanalyze', s.last_autoanalyze
    ) order by pg_total_relation_size(c.oid) desc, n.nspname, c.relname)
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    left join pg_stat_user_tables s on s.relid = c.oid
    where c.relkind in ('r', 'm', 'p') and n.nspname not in ('pg_catalog', 'information_schema')
  ), '[]'::json),
  'connections', coalesce((
    select json_agg(json_build_object(
      'application_name', application_name, 'state', coalesce(state, 'unknown'),
      'wait_event_type', wait_event_type, 'wait_event', wait_event, 'connections', connections
    ) order by application_name, coalesce(state, 'unknown'), wait_event_type nulls first, wait_event nulls first)
    from (
      select coalesce(application_name, '') as application_name, state, wait_event_type, wait_event, count(*)::int as connections
      from pg_stat_activity where datname = current_database()
      group by coalesce(application_name, ''), state, wait_event_type, wait_event
    ) grouped
  ), '[]'::json),
  'cardinality', json_build_object(
    'workflows_by_type', (select json_object_agg(type, count) from
      (select type, count(*) from workflows group by type) counts),
    'tables', (select count(*) from tables),
    'documents', (select count(*) from documents),
    'execution_rows', (select count(*) from executions),
    'execution_log_rows', (select count(*) from execution_logs),
    'agent_run_rows', (select count(*) from agent_runs),
    'agent_step_rows', (select count(*) from agent_run_steps),
    'ai_usage_rows', (select count(*) from ai_usage),
    'audit_rows', (select count(*) from audit_logs)
  )
)
"""

REDIS_LABELS = {"app.kubernetes.io/name": "redis"}
SEAWEED_LABELS = {"app.kubernetes.io/name": "seaweedfs"}
POOLER_LABELS = {"cnpg.io/poolerName": "bifrost-db-pooler"}
_PROMETHEUS_LINE = re.compile(r"^(cnpg_pgbouncer_(?:lists|pools|stats)_[a-z_]+)(?:\{[^}]*\})?\s+([^\s]+)$")
_POOL_GAUGES = (
    "cl_active", "cl_active_cancel_req", "cl_cancel_req", "cl_waiting", "cl_waiting_cancel_req",
    "sv_active", "sv_active_cancel", "sv_idle", "sv_login", "sv_tested", "sv_used", "sv_wait_cancels",
)
_LIST_GAUGES = ("free_clients", "free_servers", "login_clients", "pools", "used_clients", "used_servers")
_STAT_COUNTERS = ("total_wait_time", "total_xact_count", "total_query_count")
_REQUIRED_POOLER_METRICS = frozenset({
    "cnpg_pgbouncer_pools_cl_active", "cnpg_pgbouncer_pools_cl_waiting",
    "cnpg_pgbouncer_pools_sv_active", "cnpg_pgbouncer_pools_sv_idle",
    "cnpg_pgbouncer_pools_maxwait", "cnpg_pgbouncer_lists_used_clients",
    "cnpg_pgbouncer_lists_used_servers", "cnpg_pgbouncer_stats_total_wait_time",
})


def _unmeasured(reason: str) -> dict[str, str]:
    return {"status": "unmeasured", "reason": reason}


def _require_object(value: object, name: str) -> dict:
    if not isinstance(value, dict):
        raise RuntimeError(f"storage metric {name} is not an object")
    return value


def _require_nonnegative_int(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise RuntimeError(f"storage metric {name} is not a nonnegative integer")
    return value


def _single_pod(cluster, namespace: str, selector: str, labels: dict[str, str], *, name: str, container: str) -> dict:
    listed = _require_object(json.loads(cluster.kubectl("get", "pods", "-l", selector, "-o", "json", namespace=namespace)), name)
    pods = listed.get("items")
    if not isinstance(pods, list) or len(pods) != 1:
        raise RuntimeError(f"expected exactly one {name} benchmark pod, found {len(pods) if isinstance(pods, list) else 'invalid'}")
    pod = _require_object(pods[0], name)
    metadata = _require_object(pod.get("metadata"), f"{name}.metadata")
    if metadata.get("namespace") != namespace or metadata.get("deletionTimestamp") is not None:
        raise RuntimeError(f"{name} benchmark pod is outside its namespace or terminating")
    found_labels = _require_object(metadata.get("labels"), f"{name}.labels")
    if any(found_labels.get(key) != expected for key, expected in labels.items()):
        raise RuntimeError(f"{name} selector returned a pod without expected benchmark labels")
    statuses = _require_object(pod.get("status"), f"{name}.status").get("containerStatuses")
    if not isinstance(statuses, list) or not any(isinstance(status, dict) and status.get("name") == container and status.get("ready") for status in statuses):
        raise RuntimeError(f"{name} benchmark pod has no ready {container} container")
    pod_name = metadata.get("name")
    if not isinstance(pod_name, str) or not pod_name:
        raise RuntimeError(f"{name} benchmark pod has no name")
    return {"name": pod_name, "uid": metadata.get("uid")}


def _du(cluster, namespace: str, pod: dict, container: str, path: str, component: str) -> dict:
    """Allocated and apparent bytes for one data directory, never a host-wide scan."""
    if not path.startswith("/") or "\n" in path or "\x00" in path:
        raise RuntimeError(f"{component} data directory is unsafe")
    def bytes_for(*arguments: str, unit: int, metric: str) -> int:
        # One kubectl exec per du invocation: a shell pipeline can otherwise
        # return awk's success after du itself failed.
        output = cluster.kubectl("exec", pod["name"], "-c", container, "--", *arguments, path,
                                 namespace=namespace).strip().splitlines()
        if len(output) != 1:
            raise RuntimeError(f"{component} {metric} du returned an invalid line count")
        fields = output[0].split(maxsplit=1)
        if len(fields) != 2 or not fields[0].isdigit():
            raise RuntimeError(f"{component} {metric} du returned an invalid size response")
        return int(fields[0]) * unit

    # The benchmark's currently cached SeaweedFS image carries BusyBox 1.37,
    # whose explicit ``-b`` option is apparent bytes.  ``-k`` is allocated
    # KiB. Postgres supports these same invocations, keeping measurements
    # comparable without a runtime fallback branch.
    allocated_bytes = bytes_for("du", "-sk", unit=1024, metric="allocated")
    apparent_bytes = bytes_for("du", "-bs", unit=1, metric="apparent")
    return {
        "status": "observed", "pod": pod["name"], "pod_uid": pod["uid"], "path": path,
        "allocated_bytes": allocated_bytes, "apparent_bytes": apparent_bytes,
        "basis": "separate exact-pod du -sk allocated KiB and du -bs apparent bytes",
    }


def _container_logs(cluster) -> dict:
    """Count only benchmark pod log files on the verified single Kind node.

    Kubernetes stores current and rotated container files below ``/var/log/pods``.
    The command prints sizes only; no log content or file paths leave the node.
    """
    command = (
        "find /var/log/pods -type f \\( "
        "-path '/var/log/pods/bench-tenant_*/*/*.log*' -o "
        "-path '/var/log/pods/bench-infra_*/*/*.log*' \\) -printf '%s\\n'"
    )
    completed = subprocess.run(
        ["docker", "exec", cluster.identity.node_name, "sh", "-ec", command],
        capture_output=True, text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Kind node benchmark log measurement failed: {completed.stderr.strip()[-1000:]}")
    sizes = completed.stdout.splitlines()
    if any(not size.isdigit() for size in sizes):
        raise RuntimeError("Kind node benchmark log measurement returned a non-byte size")
    return {
        "status": "observed", "retained_bytes": sum(int(size) for size in sizes), "files": len(sizes),
        "scope": "current and rotated /var/log/pods files for bench-tenant_* and bench-infra_* only",
        "basis": "verified Kind node find -printf size; no log contents or file paths retained",
    }


def _info(text: str, allowed: tuple[str, ...], section: str) -> dict[str, int | float]:
    values: dict[str, int | float] = {}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if not separator or key not in allowed:
            continue
        try:
            number: int | float = int(value) if value.lstrip("-").isdigit() else float(value)
        except ValueError as error:
            raise RuntimeError(f"Redis INFO {section} has a nonnumeric {key}") from error
        if isinstance(number, float) and not math.isfinite(number):
            raise RuntimeError(f"Redis INFO {section} has a non-finite {key}")
        values[key] = number
    missing = set(allowed) - set(values)
    if missing:
        raise RuntimeError(f"Redis INFO {section} omitted required counters: {sorted(missing)}")
    return {key: values[key] for key in allowed}


def _redis(cluster) -> dict:
    pod = _single_pod(cluster, guard.TENANT_NS, "app.kubernetes.io/name=redis", REDIS_LABELS, name="Redis", container="redis")
    def execute(*args: str) -> str:
        return cluster.kubectl("exec", pod["name"], "-c", "redis", "--", "redis-cli", "--raw", *args).strip()

    memory = _info(execute("INFO", "memory"), ("used_memory", "used_memory_rss", "mem_fragmentation_ratio"), "memory")
    clients = _info(execute("INFO", "clients"), ("connected_clients", "blocked_clients"), "clients")
    stats = _info(execute("INFO", "stats"), ("total_connections_received", "total_commands_processed", "instantaneous_ops_per_sec", "keyspace_hits", "keyspace_misses", "evicted_keys", "expired_keys"), "stats")
    lines = execute("CONFIG", "GET", "maxmemory", "maxmemory-policy").splitlines()
    config = dict(zip(lines[::2], lines[1::2]))
    if len(lines) != 4 or set(config) != {"maxmemory", "maxmemory-policy"} or not config["maxmemory"].isdigit() or not config["maxmemory-policy"]:
        raise RuntimeError("Redis CONFIG GET returned an unexpected maxmemory policy shape")
    return {
        "status": "observed", "pod": pod["name"], "pod_uid": pod["uid"], "memory": memory,
        "clients": clients, "stats": stats,
        "configuration": {"maxmemory_bytes": int(config["maxmemory"]), "maxmemory_policy": config["maxmemory-policy"]},
    }


def _parse_pgbouncer_metrics(text: str) -> dict:
    """Aggregate the Pooler's safe exporter metrics while dropping all labels.

    The exporter includes database/user labels.  They are intentionally never
    retained, because benchmark artifacts need aggregate connection pressure,
    not account or database identities.
    """
    values: dict[str, list[float]] = {}
    invalid: list[str] = []
    for line in text.splitlines():
        matched = _PROMETHEUS_LINE.fullmatch(line)
        if matched is None:
            continue
        name, raw_value = matched.groups()
        try:
            value = float(raw_value)
        except ValueError:
            invalid.append(name)
            continue
        if not math.isfinite(value):
            invalid.append(name)
            continue
        values.setdefault(name, []).append(value)
    present = set(values)
    missing = sorted(_REQUIRED_POOLER_METRICS - present)
    if missing or invalid:
        details = []
        if missing:
            details.append(f"required exporter metrics missing: {missing}")
        if invalid:
            details.append(f"exporter metrics had invalid numeric values: {sorted(set(invalid))}")
        return _unmeasured("; ".join(details))

    def summed(prefix: str, names: tuple[str, ...]) -> dict[str, int | float]:
        result: dict[str, int | float] = {}
        for name in names:
            observed = values.get(f"{prefix}{name}")
            if observed is not None:
                total = math.fsum(observed)
                result[name] = int(total) if total.is_integer() else total
        return result

    waits = values["cnpg_pgbouncer_pools_maxwait"]
    return {
        "status": "observed",
        "lists": summed("cnpg_pgbouncer_lists_", _LIST_GAUGES),
        "pools": summed("cnpg_pgbouncer_pools_", _POOL_GAUGES),
        "max_wait_seconds": max(waits),
        "stats": summed("cnpg_pgbouncer_stats_", _STAT_COUNTERS),
        "labels": "stripped; values aggregate every exporter pool/database/user series",
        "application_pool_checkout": _unmeasured(
            "Pooler exporter does not expose normal application-pool checkout state",
        ),
    }


def _pgbouncer(cluster) -> dict:
    pooler = _single_pod(
        cluster, guard.TENANT_NS, "cnpg.io/poolerName=bifrost-db-pooler", POOLER_LABELS,
        name="PgBouncer", container="pgbouncer",
    )
    metrics = cluster.kubectl(
        "get", "--raw", f"/api/v1/namespaces/{guard.TENANT_NS}/pods/{pooler['name']}:9127/proxy/metrics",
        namespaced=False,
    )
    result = _parse_pgbouncer_metrics(metrics)
    result["pod"] = pooler["name"]
    result["pod_uid"] = pooler["uid"]
    result["basis"] = "CloudNativePG PgBouncer exporter endpoint 9127; Prometheus labels stripped before retention"
    result.setdefault("application_pool_checkout", _unmeasured(
        "Pooler exporter does not expose normal application-pool checkout state",
    ))
    return result


def _backup_retention(cluster) -> dict:
    resource = _require_object(json.loads(cluster.kubectl("get", "cluster", "bifrost-db", "-o", "json")), "CloudNativePG cluster")
    spec = _require_object(resource.get("spec"), "CloudNativePG cluster spec")
    backup = spec.get("backup")
    if backup is None:
        return {"status": "declared", "configured": False, "retention_policy": None,
                "reason": "CloudNativePG Cluster spec has no backup section"}
    backup = _require_object(backup, "CloudNativePG backup spec")
    retention = backup.get("retentionPolicy")
    if retention is not None and not isinstance(retention, str):
        raise RuntimeError("CloudNativePG backup retentionPolicy is not a string")
    return {"status": "declared", "configured": True, "retention_policy": retention,
            "barman_object_store_configured": isinstance(backup.get("barmanObjectStore"), dict)}


def _postgres(cluster) -> dict:
    observed = _require_object(cluster.driver_sql(POSTGRES_SQL), "Postgres query")
    database = _require_object(observed.get("database"), "Postgres database")
    _require_nonnegative_int(database.get("bytes"), "Postgres database bytes")
    _require_nonnegative_int(database.get("wal_retained_bytes"), "Postgres retained WAL bytes")
    data_directory = database.pop("data_directory", None)
    if not isinstance(data_directory, str):
        raise RuntimeError("Postgres did not report an absolute data directory")
    postgres = _single_pod(cluster, guard.TENANT_NS, "cnpg.io/cluster=bifrost-db,cnpg.io/podRole=instance",
                           {"cnpg.io/cluster": "bifrost-db", "cnpg.io/podRole": "instance"}, name="Postgres", container="postgres")
    return {
        "database": database, "relations": observed.get("relations"), "connections": observed.get("connections"),
        "cardinality": observed.get("cardinality"),
        "backup_retention": _backup_retention(cluster),
        "physical_volume": _du(cluster, guard.TENANT_NS, postgres, "postgres", data_directory, "Postgres"),
    }


def _object_storage(cluster) -> dict:
    api_pod, _api_uid = cluster.api_pod()
    logical = _require_object(cluster.exec_python(
        api_pod, METRICS, ["logical-objects", "{}"], timeout_s=90, namespace=guard.TENANT_NS,
    ), "logical S3 payload")
    seaweed = _single_pod(cluster, guard.INFRA_NS, "app.kubernetes.io/name=seaweedfs", SEAWEED_LABELS,
                          name="SeaweedFS", container="seaweedfs")
    return {
        "logical_current_payload": logical,
        "physical_volume": _du(cluster, guard.INFRA_NS, seaweed, "seaweedfs", "/data", "SeaweedFS"),
    }


def _policies(cluster) -> dict:
    pod, _uid = cluster.api_pod()
    policy = _require_object(cluster.exec_python(pod, METRICS, ["retention-policy", "{}"],
                                               timeout_s=90, namespace=guard.TENANT_NS), "retention policy")
    days = _require_object(policy.get("run_retention"), "run retention").get("days", "missing")
    if days is not None and (type(days) is not int or days < 30):
        raise RuntimeError("canonical run retention policy returned invalid days")
    return policy


def _node_environment(cluster) -> dict:
    """Bounded safe kubelet configuration/counters; no IPs, paths or log content."""
    node = cluster.identity.node_name
    summary = _require_object(json.loads(cluster.kubectl("get", "--raw", f"/api/v1/nodes/{node}/proxy/stats/summary",
                                                       namespaced=False)), "kubelet summary")
    config = _require_object(json.loads(cluster.kubectl("get", "--raw", f"/api/v1/nodes/{node}/proxy/configz",
                                                      namespaced=False)), "kubelet config")
    kubelet = _require_object(config.get("kubeletconfig"), "kubelet configz")
    pods = []
    for pod in summary.get("pods", []):
        reference = pod.get("podRef", {})
        if reference.get("namespace") not in (guard.TENANT_NS, guard.INFRA_NS):
            continue
        network = pod.get("network")
        if not isinstance(network, dict):
            pods.append({"namespace": reference["namespace"], "pod": reference.get("name"), "uid": reference.get("uid"),
                         **_unmeasured("kubelet omitted pod network counters")})
            continue
        keys = ("rxBytes", "txBytes", "rxErrors", "txErrors")
        counters = {key: _require_nonnegative_int(network[key], f"pod network {key}") for key in keys if key in network}
        missing = sorted(set(keys) - counters.keys())
        pods.append({"namespace": reference["namespace"], "pod": reference.get("name"), "uid": reference.get("uid"),
                     "status": "partial" if missing else "observed", "missing_counters": missing,
                     "at": network.get("time"), **counters})
    return {"container_log_rotation": {key: kubelet.get(key) for key in ("containerLogMaxSize", "containerLogMaxFiles")},
            "network": {"basis": "kubelet pod interface counters including inter-pod traffic; not billable public egress",
                        "counter_identity": "pod UID; never subtract across replacement or counter regression", "pods": pods}}


def collect(cluster) -> dict:
    """One fail-closed, bounded storage/connection evidence frame.

    The caller supplies the worktree-bound ``Cluster``.  Collection never
    mutates the tenant and invokes the normal cluster identity verification
    before pod execs.
    """
    cluster.verify()
    return {
        "schema_version": 1,
        "collected_at": time.time(),
        "scope": {"tenant_namespace": guard.TENANT_NS, "infra_namespace": guard.INFRA_NS},
        "postgres": _postgres(cluster),
        "redis": _redis(cluster),
        "pgbouncer": _pgbouncer(cluster),
        "object_storage": _object_storage(cluster),
        "container_logs": _container_logs(cluster),
        "policies": _policies(cluster),
        "node_environment": _node_environment(cluster),
    }
