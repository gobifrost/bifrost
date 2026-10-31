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
  ), '[]'::json)
)
"""

REDIS_LABELS = {"app.kubernetes.io/name": "redis"}
SEAWEED_LABELS = {"app.kubernetes.io/name": "seaweedfs"}


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
    command = (
        'allocated=$(du -sk -- "$1" | awk \'NR == 1 {print $1}\'); '
        'apparent=$(du -sk --apparent-size -- "$1" | awk \'NR == 1 {print $1}\'); '
        'case "$allocated:$apparent" in (*[!0-9:]*|:) exit 64;; esac; '
        'printf "%s %s\\n" "$allocated" "$apparent"'
    )
    output = cluster.kubectl("exec", pod["name"], "-c", container, "--", "sh", "-ec", command, "bench-storage", path,
                             namespace=namespace).strip().split()
    if len(output) != 2 or any(not value.isdigit() for value in output):
        raise RuntimeError(f"{component} du returned an invalid size response")
    allocated_kib, apparent_kib = (int(value) for value in output)
    return {
        "status": "observed", "pod": pod["name"], "pod_uid": pod["uid"], "path": path,
        "allocated_bytes": allocated_kib * 1024, "apparent_bytes": apparent_kib * 1024,
        "basis": "du -sk and du -sk --apparent-size inside the exact benchmark pod",
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
    config = execute("CONFIG", "GET", "maxmemory", "maxmemory-policy").splitlines()
    if len(config) != 4 or config[0] != "maxmemory" or config[2] != "maxmemory-policy" or not config[1].isdigit() or not config[3]:
        raise RuntimeError("Redis CONFIG GET returned an unexpected maxmemory policy shape")
    return {
        "status": "observed", "pod": pod["name"], "pod_uid": pod["uid"], "memory": memory,
        "clients": clients, "stats": stats,
        "configuration": {"maxmemory_bytes": int(config[1]), "maxmemory_policy": config[3]},
    }


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
    postgres = _single_pod(cluster, guard.TENANT_NS, "cnpg.io/cluster=bifrost-db", {"cnpg.io/cluster": "bifrost-db"}, name="Postgres", container="postgres")
    return {
        "database": database, "relations": observed.get("relations"), "connections": observed.get("connections"),
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
        "pgbouncer": _unmeasured("benchmark harness has no safe authenticated PgBouncer administration transport"),
        "object_storage": _object_storage(cluster),
        "container_logs": _unmeasured("no approved Kind-node container-log path allowlist for retained-byte measurement"),
    }
