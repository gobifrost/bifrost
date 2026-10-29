"""kubectl access to the worktree's Kind cluster for the benchmark harness.

Binds every call to the kubeconfig and context ``scripts/kubernetes/local-kind.sh``
creates, so the harness never touches the user's active cluster context.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAMESPACE = "bifrost-local"
_MIB = 1024 * 1024


def _worktree_hash() -> str:
    return hashlib.sha256(str(ROOT).encode()).hexdigest()[:12]


def role_for_pod(pod: str) -> str:
    if pod.startswith("bifrost-api-"):
        return "api"
    if pod.startswith("bifrost-worker-"):
        return "worker"
    if pod.startswith("bench-"):
        return "bench"
    return "infra"


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
    def __init__(self, namespace: str = NAMESPACE) -> None:
        worktree_hash = _worktree_hash()
        state_dir = Path(os.environ.get("BIFROST_KIND_STATE_DIR", f"/tmp/bifrost-k8s-{worktree_hash}"))
        cluster_name = os.environ.get("BIFROST_KIND_CLUSTER_NAME", f"bifrost-{worktree_hash}")
        self.kubeconfig = state_dir / "kubeconfig"
        self.context = f"kind-{cluster_name}"
        self.namespace = namespace
        self.image = os.environ.get("BIFROST_K8S_IMAGE", f"bifrost-api:kind-{worktree_hash}")

    def kubectl(self, *args: str, input: str | None = None, check: bool = True, namespaced: bool = True) -> str:
        command = ["kubectl", "--kubeconfig", str(self.kubeconfig), "--context", self.context]
        if namespaced:
            command += ["-n", self.namespace]
        completed = subprocess.run([*command, *args], input=input, capture_output=True, text=True)
        if check and completed.returncode != 0:
            raise RuntimeError(f"kubectl {' '.join(args)} failed: {completed.stderr.strip()}")
        return completed.stdout

    def apply_profile(self, profile: dict) -> None:
        """Shape the stack: no scheduler, one API replica, production limits and allocator env."""
        self.kubectl("scale", "deployment/bifrost-scheduler", "--replicas=0")
        self.kubectl("scale", "deployment/bifrost-worker", f"--replicas={profile['worker_replicas']}")
        self.kubectl("scale", "deployment/bifrost-api", "--replicas=1")
        api_env = {**profile["env"], **profile.get("api_env", {})}
        if profile["thp_emulation"]:
            api_env["GLIBC_TUNABLES"] = "glibc.malloc.hugetlb=1"
        self.kubectl("set", "env", "deployment/bifrost-api", *(f"{k}={v}" for k, v in api_env.items()))
        self.kubectl("set", "env", "deployment/bifrost-worker", *(f"{k}={v}" for k, v in profile["env"].items()))
        resources = profile["api_resources"]
        self.kubectl(
            "set", "resources", "deployment/bifrost-api", "-c", "api",
            f"--limits=cpu={resources['limits']['cpu']},memory={resources['limits']['memory']}",
            f"--requests=cpu={resources['requests']['cpu']},memory={resources['requests']['memory']}",
        )
        self.kubectl("rollout", "status", "deployment/bifrost-api", "--timeout=300s")

    def restart_api(self) -> None:
        self.kubectl("rollout", "restart", "deployment/bifrost-api")
        self.kubectl("rollout", "status", "deployment/bifrost-api", "--timeout=300s")

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

    def container_stats(self) -> list[dict]:
        nodes = json.loads(self.kubectl("get", "nodes", "-o", "json", namespaced=False))["items"]
        rows: list[dict] = []
        for node in nodes:
            name = node["metadata"]["name"]
            raw = self.kubectl("get", "--raw", f"/api/v1/nodes/{name}/proxy/stats/summary", namespaced=False)
            rows += parse_stats_summary(json.loads(raw), self.namespace)
        return rows

    def worker_pods(self) -> list[str]:
        names = self.kubectl("get", "pods", "--field-selector=status.phase=Running", "-o", "name").split()
        return [name.removeprefix("pod/") for name in names if name.startswith("pod/bifrost-worker-")]

    def worker_processes(self, pod: str) -> list[dict]:
        return parse_worker_processes(self.kubectl("exec", pod, "-c", "worker", "--", "sh", "-c", WORKER_PROCESSES_SCRIPT))

    def db_counters(self) -> dict:
        raw = self.kubectl(
            "exec", "postgres-0", "--", "sh", "-c",
            'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "$1"', "sh", DB_COUNTERS_SQL,
        )
        return parse_db_counters(raw)

    def exec_python(self, pod: str, script: Path, args: list[str], timeout_s: int = 3600) -> dict:
        """Run ``script`` with the pod's Python and return its last JSON line."""
        command = [
            "kubectl", "--kubeconfig", str(self.kubeconfig), "--context", self.context, "-n", self.namespace,
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
