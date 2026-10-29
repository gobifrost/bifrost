"""kubectl access to the worktree's Kind cluster for the benchmark harness.

Every call is bound to the kubeconfig and context ``scripts/kubernetes/local-kind.sh``
creates for this worktree (no environment override), and ``verify`` proves the
cluster is that local Kind cluster before anything acts on it (``guard.py``).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import guard

ROOT = Path(__file__).resolve().parents[2]
_MIB = 1024 * 1024
_ROLES = (
    ("bifrost-api-", "api"), ("bifrost-worker-", "worker"), ("bifrost-client-", "client"),
    ("bifrost-db-pooler-", "pooler"), ("bifrost-db-", "postgres"), ("redis-", "redis"), ("bench-", "bench"),
)


def role_for_pod(pod: str) -> str:
    return next((role for prefix, role in _ROLES if pod.startswith(prefix)), "infra")


def parse_stats_summary(summary: dict, namespace: str) -> list[dict]:
    """Per-container memory and CPU from a kubelet ``/stats/summary`` document."""
    rows: list[dict] = []
    for pod in summary.get("pods", []):
        ref = pod.get("podRef", {})
        if ref.get("namespace") != namespace:
            continue
        for container in pod.get("containers", []):
            memory = container.get("memory", {})
            rows.append({
                "pod": ref["name"],
                "pod_uid": ref.get("uid"),
                "container": container["name"],
                "role": role_for_pod(ref["name"]),
                "working_set_mib": round(memory.get("workingSetBytes", 0) / _MIB, 1),
                "rss_mib": round(memory.get("rssBytes", 0) / _MIB, 1),
                "cpu_millicores": round(container.get("cpu", {}).get("usageNanoCores", 0) / 1_000_000, 1),
                "cpu_core_seconds": container.get("cpu", {}).get("usageCoreNanoSeconds", 0) / 1e9,
            })
    return rows


DB_COUNTERS_SQL = """
select json_build_object(
  'xact', (select xact_commit + xact_rollback from pg_stat_database where datname = current_database()),
  'rollbacks', (select xact_rollback from pg_stat_database where datname = current_database()),
  'rows_written', (select tup_inserted + tup_updated + tup_deleted from pg_stat_database where datname = current_database()),
  'wal_bytes', pg_wal_lsn_diff(pg_current_wal_lsn(), '0/0'),
  'db_bytes', pg_database_size(current_database()),
  'tables', (
    select json_object_agg(relname, json_build_array(
      n_tup_ins + n_tup_upd + n_tup_del, coalesce(seq_scan, 0) + coalesce(idx_scan, 0)))
    from pg_stat_user_tables
  )
)
"""


def parse_db_counters(raw: str) -> dict:
    """Cumulative Postgres counters: transactions, rows written, WAL bytes, size, per-table writes/scans."""
    counters = json.loads(raw.strip())
    counters["tables"] = counters.get("tables") or {}
    return counters


# One awk and shell builtins only, so sampling barely touches the worker's CPU.
WORKER_PROCESSES_SCRIPT = (
    "awk '/^Private_(Clean|Dirty):/{s[FILENAME]+=$2} END{for (f in s) print \"M\", f, s[f]}' "
    "/proc/[0-9]*/smaps_rollup 2>/dev/null; "
    'for f in /proc/[0-9]*/stat; do read -r line < "$f" 2>/dev/null && echo "S $line"; done'
)


def parse_worker_processes(text: str) -> list[dict]:
    """Private memory and depth below the launcher (pid 1) for each worker process.

    Depth 1 is a template, depth 2 a run child. Processes not descended from
    pid 1 (``kubectl exec`` helpers, including this sampler) are dropped.
    """
    private_kib: dict[int, int] = {}
    parent: dict[int, int] = {}
    for line in text.splitlines():
        if line.startswith("M "):
            _, path, kib = line.split()
            private_kib[int(path.split("/")[2])] = int(kib)
        elif line.startswith("S "):
            pid = int(line[2:].split(" ", 1)[0])
            parent[pid] = int(line[line.rindex(")") + 2:].split()[1])
    rows: list[dict] = []
    for pid in parent:
        depth, current = 0, pid
        while current != 1 and current in parent and depth <= len(parent):
            current, depth = parent[current], depth + 1
        if current != 1:
            continue
        rows.append({"pid": pid, "ppid": parent[pid], "depth": depth, "private_mib": round(private_kib.get(pid, 0) / 1024, 1)})
    return rows


class Cluster:
    def __init__(self) -> None:
        self.identity = guard.expected_identity(ROOT)
        self.kubeconfig = self.identity.kubeconfig
        self.context = self.identity.context
        self.namespace = guard.TENANT_NS
        self.image = f"bifrost-api:kind-{self.identity.worktree_hash}"

    def kubectl(self, *args: str, input: str | None = None, check: bool = True, namespaced: bool = True,
                namespace: str | None = None) -> str:
        command = ["kubectl", "--kubeconfig", str(self.kubeconfig), "--context", self.context]
        if namespaced:
            command += ["-n", namespace or self.namespace]
        completed = subprocess.run([*command, *args], input=input, capture_output=True, text=True)
        if check and completed.returncode != 0:
            raise RuntimeError(f"kubectl {' '.join(args)} failed: {completed.stderr.strip()}")
        return completed.stdout

    def verify(self) -> None:
        """Fail closed unless this is the worktree's own local Kind cluster with its recorded sentinel."""
        if not self.kubeconfig.exists():
            raise guard.GuardError(f"no kubeconfig at {self.kubeconfig}; run ./test.sh bench up")
        guard.check_kubeconfig(json.loads(self.kubectl("config", "view", "--raw", "-o", "json", namespaced=False)), self.identity)
        guard.check_nodes(json.loads(self.kubectl("get", "nodes", "-o", "json", namespaced=False)), self.identity)
        found = subprocess.run(["docker", "inspect", "--format", "{{.State.Running}}", self.identity.node_name],
                               capture_output=True, text=True)
        if found.stdout.strip() != "true":
            raise guard.GuardError(f"Kind node container {self.identity.node_name} is not running locally")
        configmap = self.kubectl("get", "configmap", "bench-sentinel", "-n", "kube-system", "-o", "json", check=False, namespaced=False)
        recorded = self.identity.sentinel_path.read_text(encoding="utf-8").strip() if self.identity.sentinel_path.exists() else None
        guard.check_sentinel(json.loads(configmap) if configmap else None, recorded)

    def write_sentinel(self) -> None:
        """Mark a cluster this harness just created; refuses to adopt one created elsewhere."""
        if self.identity.sentinel_path.exists():
            return
        if self.kubectl("get", "configmap", "bench-sentinel", "-n", "kube-system", "--ignore-not-found", "-o", "name", namespaced=False).strip():
            raise guard.GuardError("cluster already carries a sentinel this worktree did not record")
        cluster_id = guard.new_sentinel_id()
        self.kubectl("apply", "-f", "-", input=json.dumps(guard.sentinel_configmap(cluster_id)), namespaced=False)
        self.identity.sentinel_path.write_text(cluster_id, encoding="utf-8")
        self.identity.sentinel_path.chmod(0o600)

    def restart_api(self) -> None:
        self.kubectl("rollout", "restart", "deployment/bifrost-api")
        self.kubectl("rollout", "status", "deployment/bifrost-api", "--timeout=900s")

    def api_pod(self) -> tuple[str, str]:
        """Name and UID of the single ready API pod."""
        pods = json.loads(self.kubectl("get", "pods", "-l", "app.kubernetes.io/name=bifrost-api", "-o", "json"))
        ready = [
            pod for pod in pods["items"]
            if pod["metadata"].get("deletionTimestamp") is None
            and any(c.get("type") == "Ready" and c.get("status") == "True" for c in pod["status"].get("conditions", []))
        ]
        if len(ready) != 1:
            raise RuntimeError(f"expected one ready API pod, found {len(ready)}")
        return ready[0]["metadata"]["name"], ready[0]["metadata"]["uid"]

    def smaps(self, pod: str, container: str) -> str:
        return self.kubectl("exec", pod, "-c", container, "--", "cat", "/proc/1/smaps")

    def container_stats(self, namespace: str | None = None) -> list[dict]:
        nodes = json.loads(self.kubectl("get", "nodes", "-o", "json", namespaced=False))["items"]
        rows: list[dict] = []
        for node in nodes:
            name = node["metadata"]["name"]
            raw = self.kubectl("get", "--raw", f"/api/v1/nodes/{name}/proxy/stats/summary", namespaced=False)
            rows += parse_stats_summary(json.loads(raw), namespace or self.namespace)
        return rows

    def worker_pods(self) -> list[str]:
        names = self.kubectl("get", "pods", "--field-selector=status.phase=Running", "-o", "name").split()
        return [name.removeprefix("pod/") for name in names if name.startswith("pod/bifrost-worker-")]

    def worker_processes(self, pod: str) -> list[dict]:
        return parse_worker_processes(self.kubectl("exec", pod, "-c", "worker", "--", "sh", "-c", WORKER_PROCESSES_SCRIPT))

    def bench_pods(self) -> list[tuple[str, str, str]]:
        """(namespace, name, uid) of every tenant and infrastructure pod."""
        found = []
        for namespace in (guard.TENANT_NS, guard.INFRA_NS):
            for pod in json.loads(self.kubectl("get", "pods", "-o", "json", namespace=namespace))["items"]:
                found.append((namespace, pod["metadata"]["name"], pod["metadata"]["uid"]))
        return found

    def external_nodes(self) -> list[str]:
        """Other Kind node containers on this host (their load is external contention)."""
        names = subprocess.run(["docker", "ps", "--filter", "label=io.x-k8s.kind.role", "--format", "{{.Names}}"],
                               capture_output=True, text=True, check=True).stdout.split()
        return sorted(name for name in names if name != self.identity.node_name)

    def tenant_faults(self) -> dict[str, int]:
        """Cumulative tenant fault counters: container restarts, OOM kills, evictions, quota-denied pod creations."""
        pods = json.loads(self.kubectl("get", "pods", "-o", "json"))["items"]
        statuses = [c for pod in pods for c in pod.get("status", {}).get("containerStatuses", [])]
        events = json.loads(self.kubectl("get", "events", "-o", "json"))["items"]
        return {
            "restarts": sum(c.get("restartCount", 0) for c in statuses),
            "oom_killed": sum(1 for c in statuses if c.get("lastState", {}).get("terminated", {}).get("reason") == "OOMKilled"),
            "evicted": sum(1 for pod in pods if pod.get("status", {}).get("reason") == "Evicted"),
            "quota_denials": sum(e.get("count") or 1 for e in events if e.get("reason") == "FailedCreate" and "exceeded quota" in e.get("message", "")),
        }

    def driver_sql(self, sql: str) -> object:
        """Run one read-only query from the infrastructure driver pod (never inside a tenant pod)."""
        return self.exec_python("bench-driver", Path(__file__).resolve().parent / "incluster" / "loadgen.py", ["sql", json.dumps({"sql": sql})])["value"]

    def db_counters(self) -> dict:
        return parse_db_counters(json.dumps(self.driver_sql(DB_COUNTERS_SQL)))

    def exec_python(self, pod: str, script: Path, args: list[str], timeout_s: int = 3600, namespace: str = guard.INFRA_NS) -> dict:
        """Run ``script`` with the pod's Python and return its last JSON line."""
        command = [
            "kubectl", "--kubeconfig", str(self.kubeconfig), "--context", self.context, "-n", namespace,
            "exec", "-i", pod, "--", "sh", "-c", 'cd /app && exec python - "$@"', "bench", *args,
        ]
        completed = subprocess.run(
            command, input=script.read_text(encoding="utf-8"), capture_output=True, text=True, timeout=timeout_s,
        )
        lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
        if completed.returncode != 0 or not lines:
            raise RuntimeError(
                f"{script.name} {args[:1]} failed ({completed.returncode}): {completed.stderr.strip()[-2000:]}"
            )
        return json.loads(lines[-1])
