"""kubectl access to the worktree's Kind cluster for the benchmark harness.

Every call is bound to the kubeconfig and context ``scripts/kubernetes/local-kind.sh``
creates for this worktree (no environment override), and ``verify`` proves the
cluster is that local Kind cluster before anything acts on it (``guard.py``).
"""

from __future__ import annotations

import json
import os
import queue
import re
import select
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

import guard

ROOT = Path(__file__).resolve().parents[2]
_MIB = 1024 * 1024
_ROLES = (
    ("bifrost-api-", "api"), ("bifrost-worker-", "worker"), ("bifrost-client-", "client"),
    ("bifrost-db-pooler-", "pooler"), ("bifrost-db-", "postgres"), ("redis-", "redis"), ("bench-", "bench"),
)
_OCI_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_PROXY_ADDRESS = re.compile(r"Starting to serve on 127\.0\.0\.1:(\d+)")
_QUOTA_EVENT_PROBE_TIMEOUT_S = 10.0


class RemoteCleanupError(RuntimeError):
    """The captured in-cluster driver could not be proved destroyed."""

    requires_namespace_teardown = True


class FramedPython:
    """One in-cluster Python command whose JSON event frames can be observed live."""

    def __init__(self, process: subprocess.Popen[bytes], script: Path, writer: threading.Thread,
                 writer_errors: list[BaseException], remote_cancel: Callable[[], None] | None) -> None:
        self.process = process
        self.script = script
        self.writer = writer
        self.writer_errors = writer_errors
        self.remote_cancel = remote_cancel
        self.events: queue.Queue[dict | None] = queue.Queue()
        self.stderr: list[str] = []
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for raw in self.process.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("{"):
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                self.events.put({"event": "protocol_error", "error": f"malformed JSON frame: {line[:200]}"})
                continue
            if isinstance(frame, dict):
                self.events.put(frame)
        self.events.put(None)

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        for raw in self.process.stderr:
            self.stderr.append(raw.decode("utf-8", errors="replace"))

    def _error(self) -> RuntimeError:
        return RuntimeError(f"{self.script.name} framed command failed ({self.process.returncode}): {''.join(self.stderr)[-2000:].strip()}")

    def result(self, timeout_s: float, on_event: Callable[[dict], None]) -> dict:
        """Wait for the final JSON result while delivering all preceding event frames."""
        deadline = time.monotonic() + timeout_s
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"{self.script.name} framed command exceeded {timeout_s}s")
                try:
                    frame = self.events.get(timeout=remaining)
                except queue.Empty as error:
                    raise TimeoutError(f"{self.script.name} framed command exceeded {timeout_s}s") from error
                if frame is None:
                    self.process.wait(timeout=max(0.1, deadline - time.monotonic()))
                    if self.writer_errors:
                        raise RuntimeError(f"{self.script.name} stdin write failed: {self.writer_errors[0]}")
                    raise self._error()
                if "event" in frame:
                    if frame["event"] in {"error", "protocol_error"}:
                        raise RuntimeError(f"{self.script.name} frame error: {frame.get('error', frame['event'])}")
                    on_event(frame)
                    continue
                self.process.wait(timeout=max(0.1, deadline - time.monotonic()))
                if self.process.returncode != 0:
                    raise self._error()
                return frame
        except BaseException:
            self.cancel()
            raise
        finally:
            self._stdout_thread.join(timeout=1)
            self._stderr_thread.join(timeout=1)
            self.writer.join(timeout=1)

    def cancel(self) -> None:
        """Destroy the captured remote driver before reaping local kubectl exec."""
        remote_error: BaseException | None = None
        if self.remote_cancel is not None:
            try:
                self.remote_cancel()
            except BaseException as error:  # retain ownership failure for the caller's invalid result
                remote_error = error
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
        self._stdout_thread.join(timeout=1)
        self._stderr_thread.join(timeout=1)
        self.writer.join(timeout=1)
        if remote_error is not None:
            raise RemoteCleanupError(f"remote framed-command cleanup was not verified: {remote_error}") from remote_error


def _digest(value: str, label: str) -> str:
    """One full OCI digest; never accept a layer-like or abbreviated identifier."""
    if not _OCI_DIGEST.fullmatch(value):
        raise RuntimeError(f"{label} is not a full OCI digest: {value!r}")
    return value


def image_id_digest(image_id: str) -> str:
    """Extract the single immutable digest Kubernetes reports for a container."""
    found = _OCI_DIGEST.findall(image_id)
    if len(found) != 1:
        raise RuntimeError(f"container imageID does not contain one OCI digest: {image_id!r}")
    return found[0]


def resolve_oci_chain(root_id: str, os_name: str, architecture: str, content) -> dict[str, str | None]:
    """Resolve an OCI index or manifest to its config digest in the Kind store."""
    root_id = _digest(root_id, "host image ID")
    document = json.loads(content(root_id))
    if "manifests" in document:
        matches = [
            descriptor for descriptor in document["manifests"]
            if descriptor.get("platform", {}).get("os") == os_name
            and descriptor.get("platform", {}).get("architecture") == architecture
        ]
        if len(matches) != 1:
            raise RuntimeError(f"OCI index {root_id} has {len(matches)} exact {os_name}/{architecture} manifests")
        manifest_id = _digest(matches[0].get("digest", ""), "OCI manifest digest")
        manifest = json.loads(content(manifest_id))
        config_id = _digest(manifest.get("config", {}).get("digest", ""), "OCI config digest")
        return {"index_id": root_id, "manifest_id": manifest_id, "config_id": config_id}
    if "config" in document:
        config_id = _digest(document["config"].get("digest", ""), "OCI config digest")
        return {"index_id": None, "manifest_id": root_id, "config_id": config_id}
    if document.get("rootfs") and document.get("os") == os_name and document.get("architecture") == architecture:
        return {"index_id": None, "manifest_id": None, "config_id": root_id}
    raise RuntimeError(f"OCI content {root_id} is neither an index, manifest, nor matching {os_name}/{architecture} config")


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
  'wal_generated_bytes', (select wal_bytes from pg_stat_wal),
  'wal_stats_reset', (select stats_reset from pg_stat_wal),
  'db_bytes', pg_database_size(current_database()),
  'tables', (
    select json_object_agg(relname, json_build_array(
      n_tup_ins + n_tup_upd + n_tup_del, coalesce(seq_scan, 0) + coalesce(idx_scan, 0)))
    from pg_stat_user_tables
  )
)
"""


def parse_db_counters(raw: str) -> dict:
    """Cumulative Postgres counters, including LSN advance and pg_stat_wal generated bytes.

    PostgreSQL 16 serializes the two byte counters as JSON integer tokens. The
    stats layer deliberately requires exact Python integers, so a future
    fractional representation is rejected there rather than rounded here.
    """
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
                namespace: str | None = None, timeout_s: float | None = None) -> str:
        command = ["kubectl", "--kubeconfig", str(self.kubeconfig), "--context", self.context]
        if namespaced:
            command += ["-n", namespace or self.namespace]
        completed = subprocess.run(
            [*command, *args], input=input, capture_output=True, text=True, timeout=timeout_s,
        )
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

    def _node_command(self, *args: str) -> str:
        """Read from the already guarded Kind node without changing its image store."""
        completed = subprocess.run(["docker", "exec", self.identity.node_name, *args], capture_output=True, text=True)
        if completed.returncode != 0:
            raise RuntimeError(f"Kind node command {' '.join(args)} failed: {completed.stderr.strip()}")
        return completed.stdout

    def _node_platform(self) -> tuple[str, str]:
        nodes = json.loads(self.kubectl("get", "nodes", "-o", "json", namespaced=False))["items"]
        if len(nodes) != 1:
            raise RuntimeError(f"expected one guarded Kind node, found {len(nodes)}")
        info = nodes[0].get("status", {}).get("nodeInfo", {})
        os_name, architecture = info.get("operatingSystem"), info.get("architecture")
        if not isinstance(os_name, str) or not isinstance(architecture, str):
            raise RuntimeError("Kind node does not report an operating system and architecture")
        return os_name, architecture

    def image_provenance(self, image: str, host_id: str) -> dict:
        """Prove a selected tag resolves to the host-selected image config in this Kind node."""
        os_name, architecture = self._node_platform()
        chain = resolve_oci_chain(
            host_id,
            os_name,
            architecture,
            lambda digest: self._node_command("ctr", "-n", "k8s.io", "content", "get", digest),
        )
        inspected = json.loads(self._node_command("crictl", "inspecti", "-o", "json", image))
        cri_id = _digest(inspected.get("status", {}).get("id", ""), "CRI image ID")
        if cri_id != chain["config_id"]:
            raise RuntimeError(f"Kind tag {image} resolves to {cri_id}, expected config {chain['config_id']}")
        return {"tag": image, "host_id": host_id, "node": chain, "cri_config_id": cri_id}

    def _ready_api_pod(self) -> dict:
        pods = json.loads(self.kubectl("get", "pods", "-l", "app.kubernetes.io/name=bifrost-api", "-o", "json"))
        ready = [
            pod for pod in pods["items"]
            if pod["metadata"].get("deletionTimestamp") is None
            and any(c.get("type") == "Ready" and c.get("status") == "True" for c in pod["status"].get("conditions", []))
        ]
        if len(ready) != 1:
            raise RuntimeError(f"expected one ready API pod, found {len(ready)}")
        return ready[0]

    @staticmethod
    def _verified_image(image_id: str, provenance: dict) -> str:
        digest = image_id_digest(image_id)
        chain = provenance["node"]
        if digest not in {value for value in chain.values() if value}:
            raise RuntimeError(f"running container imageID {image_id!r} is outside the verified OCI chain")
        return digest

    def api_image_evidence(self, provenance: dict) -> dict:
        """The ready API container imageID, proved against the pre-deploy OCI chain."""
        pod = self._ready_api_pod()
        statuses = [status for status in pod["status"].get("containerStatuses", []) if status.get("name") == "api"]
        if len(statuses) != 1:
            raise RuntimeError(f"expected one API container status in {pod['metadata']['name']}")
        image_id = statuses[0].get("imageID", "")
        return {
            "pod": pod["metadata"]["name"],
            "pod_uid": pod["metadata"]["uid"],
            "image_id": image_id,
            "digest": self._verified_image(image_id, provenance),
        }

    def worker_image_evidence(self, provenance: dict) -> dict[str, dict]:
        """Ready worker container imageIDs, if workers exist at a snapshot boundary."""
        pods = json.loads(self.kubectl("get", "pods", "-o", "json"))["items"]
        evidence: dict[str, dict] = {}
        for pod in pods:
            name = pod["metadata"]["name"]
            if not name.startswith("bifrost-worker-"):
                continue
            if not any(c.get("type") == "Ready" and c.get("status") == "True" for c in pod["status"].get("conditions", [])):
                continue
            statuses = [status for status in pod["status"].get("containerStatuses", []) if status.get("name") == "worker"]
            if len(statuses) != 1:
                raise RuntimeError(f"expected one worker container status in {name}")
            image_id = statuses[0].get("imageID", "")
            evidence[name] = {"image_id": image_id, "digest": self._verified_image(image_id, provenance)}
        return evidence

    def api_pod(self) -> tuple[str, str]:
        """Name and UID of the single ready API pod."""
        pod = self._ready_api_pod()
        return pod["metadata"]["name"], pod["metadata"]["uid"]

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

    def api_log_source(self) -> dict:
        """Bounded API log tail with immutable pod identity."""
        pod = self._ready_api_pod()
        name, uid = pod["metadata"]["name"], pod["metadata"]["uid"]
        return {
            "pod": name, "pod_uid": uid, "container": "api",
            "logs": self.kubectl("logs", name, "-c", "api", "--tail=1000", check=False),
        }

    def worker_log_sources(self) -> list[dict]:
        """Bounded worker logs with immutable pod identity, including just-finished Jobs."""
        pods = json.loads(self.kubectl("get", "pods", "-o", "json"))["items"]
        sources = []
        for pod in pods:
            metadata = pod.get("metadata", {})
            name, uid = metadata.get("name"), metadata.get("uid")
            if not isinstance(name, str) or not name.startswith("bifrost-worker-") or not isinstance(uid, str):
                continue
            statuses = pod.get("status", {}).get("containerStatuses", [])
            if not any(status.get("name") == "worker" for status in statuses):
                continue
            sources.append({
                "pod": name, "pod_uid": uid, "container": "worker",
                "logs": self.kubectl("logs", name, "-c", "worker", "--tail=1000", check=False),
            })
        return sources


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

    def quota_denial_events(self) -> list[dict]:
        """Return only UID/count evidence for quota-denial Events in the tenant namespace.

        Classification needs the Event message, but benchmark evidence must not
        retain it because it can contain workload details.
        """
        events = json.loads(self.kubectl(
            "get", "events", "-o", "json", timeout_s=_QUOTA_EVENT_PROBE_TIMEOUT_S,
        ))["items"]
        observed = []
        for event in events:
            if not isinstance(event, dict):
                observed.append({"uid": None, "count": None})
                continue
            message = event.get("message")
            if event.get("reason") != "FailedCreate" or not isinstance(message, str) or "exceeded quota" not in message:
                continue
            series = event.get("series")
            if isinstance(series, dict):
                # events.k8s.io/v1 keeps the current aggregate in series.count;
                # do not silently fall back to a deprecated CoreV1 count when
                # a present series is malformed.
                count = series.get("count")
            elif series is None:
                count = event.get("count")
            else:
                count = None
            metadata = event.get("metadata")
            observed.append({"uid": metadata.get("uid") if isinstance(metadata, dict) else None, "count": count})
        return observed

    def driver_sql(self, sql: str, *, timeout_s: float = 3600) -> object:
        """Run one read-only query from the infrastructure driver pod (never inside a tenant pod)."""
        return self.exec_python(
            "bench-driver", Path(__file__).resolve().parent / "incluster" / "loadgen.py",
            ["sql", json.dumps({"sql": sql})], timeout_s=timeout_s,
        )["value"]

    def db_counters(self) -> dict:
        return parse_db_counters(json.dumps(self.driver_sql(DB_COUNTERS_SQL)))

    def exec_python(self, pod: str, script: Path, args: list[str], timeout_s: float = 3600, namespace: str = guard.INFRA_NS) -> dict:
        """Run ``script`` with the pod's Python as ``script MODE ARGS_FILE`` and return its last JSON line.

        ``args`` is ``[mode, json]``. The script and the JSON both travel on
        stdin: a request list can exceed Linux's 128 KiB limit on a single
        command-line argument.
        """
        mode, payload = args
        source = script.read_bytes()
        command = [
            "kubectl", "--kubeconfig", str(self.kubeconfig), "--context", self.context, "-n", namespace,
            "exec", "-i", pod, "--", "sh", "-c",
            'd=$(mktemp -d) && head -c "$1" > "$d/script.py" && cat > "$d/args.json" && cd /app'
            ' && PYTHONPATH=/app python "$d/script.py" "$2" "$d/args.json"; status=$?; rm -rf "$d"; exit $status',
            "bench", str(len(source)), mode,
        ]
        completed = subprocess.run(command, input=source + payload.encode("utf-8"), capture_output=True, timeout=timeout_s)
        stdout = completed.stdout.decode("utf-8", errors="replace")
        lines = [line for line in stdout.splitlines() if line.startswith("{")]
        if completed.returncode != 0 or not lines:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"{script.name} {mode} failed ({completed.returncode}): {stderr[-2000:]}")
        return json.loads(lines[-1])

    def _bench_driver_identity(self) -> tuple[str, str]:
        pod = json.loads(self.kubectl("get", "pod", "bench-driver", "-o", "json", namespace=guard.INFRA_NS))
        metadata = pod.get("metadata", {})
        if metadata.get("namespace") != guard.INFRA_NS or metadata.get("name") != "bench-driver" or \
                metadata.get("labels", {}).get("app.kubernetes.io/name") != "bench-driver":
            raise guard.GuardError("framed benchmark command requires bench-infra/bench-driver")
        uid = metadata.get("uid")
        if not isinstance(uid, str) or not uid:
            raise guard.GuardError("bench-driver has no UID")
        return metadata["name"], uid

    def _delete_bench_driver_uid(self, uid: str) -> None:
        self.verify()
        name, current_uid = self._bench_driver_identity()
        if current_uid != uid:
            raise RuntimeError("bench-driver changed before remote command cleanup")
        self.delete_pod_uid(name, uid, namespace=guard.INFRA_NS)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            raw = self.kubectl("get", "pod", name, "-o", "json", namespace=guard.INFRA_NS, check=False)
            if not raw.strip():
                return
            observed = json.loads(raw).get("metadata", {}).get("uid")
            if observed != uid:
                return
            time.sleep(0.2)
        raise TimeoutError("bench-driver deletion was not observed")

    def start_exec_python(self, pod: str, script: Path, args: list[str], namespace: str = guard.INFRA_NS,
                          cancel_driver: bool = False) -> FramedPython:
        """Start an event-framed in-cluster script without changing blocking callers."""
        mode, payload = args
        source = script.read_bytes()
        command = [
            "kubectl", "--kubeconfig", str(self.kubeconfig), "--context", self.context, "-n", namespace,
            "exec", "-i", pod, "--", "sh", "-c",
            'd=$(mktemp -d) && head -c "$1" > "$d/script.py" && cat > "$d/args.json" && cd /app'
            ' && PYTHONPATH=/app python "$d/script.py" "$2" "$d/args.json"; status=$?; rm -rf "$d"; exit $status',
            "bench", str(len(source)), mode,
        ]
        if cancel_driver:
            if pod != "bench-driver" or namespace != guard.INFRA_NS:
                raise guard.GuardError("remote cancellation is only allowed for bench-infra/bench-driver")
            self.verify()
            _, driver_uid = self._bench_driver_identity()

            def remote_cancel() -> None:
                self._delete_bench_driver_uid(driver_uid)
        else:
            remote_cancel = None
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        writer_errors: list[BaseException] = []

        def write_stdin() -> None:
            assert process.stdin is not None
            try:
                process.stdin.write(source + payload.encode("utf-8"))
                process.stdin.close()
            except BaseException as error:  # result turns this into a bounded command error
                writer_errors.append(error)
                try:
                    process.stdin.close()
                except OSError:
                    pass

        writer = threading.Thread(target=write_stdin, name="bench-framed-stdin", daemon=True)
        writer.start()
        return FramedPython(process, script, writer, writer_errors, remote_cancel)

    def delete_pod_uid(self, name: str, uid: str, namespace: str = guard.TENANT_NS) -> dict:
        """Delete an allowlisted benchmark pod through a guarded loopback API proxy.

        Kubectl's ordinary delete command cannot carry Kubernetes DeleteOptions
        UID preconditions.  The proxy is constrained to this one pod path,
        bound to loopback and always reaped after the request.
        """
        self.verify()
        allowed = {(guard.TENANT_NS, "redis-0"), (guard.INFRA_NS, "bench-driver")}
        if (namespace, name) not in allowed or not uid:
            raise guard.GuardError("pod delete requires a simple name and UID")
        path = f"/api/v1/namespaces/{namespace}/pods/{name}"
        proxy = subprocess.Popen([
            "kubectl", "--kubeconfig", str(self.kubeconfig), "--context", self.context,
            "proxy", "--address=127.0.0.1", "--port=0", f"--accept-paths=^{re.escape(path)}$",
        ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            assert proxy.stdout is not None
            started = time.monotonic() + 10
            port = None
            while time.monotonic() < started:
                readable, _, _ = select.select([proxy.stdout], [], [], max(0.0, started - time.monotonic()))
                if not readable:
                    break
                line = proxy.stdout.readline()
                matched = _PROXY_ADDRESS.search(line)
                if matched:
                    port = matched.group(1)
                    break
                if proxy.poll() is not None:
                    raise RuntimeError(f"kubectl proxy failed: {line.strip()}")
            if port is None:
                raise TimeoutError("kubectl proxy did not report its loopback port")
            options = json.dumps({
                "apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": uid},
            }).encode("utf-8")
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}", data=options, method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                body = error.read().decode("utf-8", errors="replace")[-1000:]
                raise RuntimeError(f"UID-preconditioned delete returned HTTP {error.code}: {body}") from error
        finally:
            if proxy.poll() is None:
                proxy.terminate()
                try:
                    proxy.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proxy.kill()
                    proxy.wait(timeout=5)
