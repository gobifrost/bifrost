"""Deploy the benchmark tenant and its infrastructure into the guarded Kind cluster.

The tenant is the ``k8s/`` deployment base plus ``manifests/tenant`` and the
profile's resource patches; ``manifests/infra`` holds everything outside the
envelope. Methodology: docs/plans/2026-10-29-capacity-benchmark-methodology.md.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import envelope
import guard
from cluster import ROOT, Cluster

HERE = Path(__file__).resolve().parent
MANIFESTS = HERE / "manifests"
CNPG_VERSION = "1.30.1"
CNPG_URL = f"https://github.com/cloudnative-pg/cloudnative-pg/releases/download/v{CNPG_VERSION}/cnpg-{CNPG_VERSION}.yaml"
CNPG_SHA256 = "37237f145d8138256ea25ae830f87759255665ff08f8d552fdd8224a5ec032fb"
EGRESS_TARGET = ("1.1.1.1", 443)
BENCH_USER_EMAIL = "bench@gobifrost.com"
PROBE_SCRIPT = (
    "import socket, sys\n"
    "def ok(host, port):\n"
    "    try:\n"
    "        socket.create_connection((host, int(port)), timeout=5).close()\n"
    "        return True\n"
    "    except OSError:\n"
    "        return False\n"
    "print(' '.join(str(ok(*t.split(':'))) for t in sys.argv[1:]))\n"
)


def _secrets(identity: guard.Identity) -> dict[str, str]:
    """Per-cluster random credentials, created once and kept with the cluster state."""
    path = identity.state_dir / "bench-secrets.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    values = {key: secrets.token_urlsafe(24) for key in ("secret_key", "db_password", "superuser_password", "s3_key", "user_password")}
    path.write_text(json.dumps(values), encoding="utf-8")
    path.chmod(0o600)
    return values


def secret_manifests(identity: guard.Identity) -> list[dict]:
    values = _secrets(identity)
    db = f"bifrost:{values['db_password']}@bifrost-db-pooler:5432/bifrost"

    def secret(name: str, namespace: str, data: dict[str, str], kind: str = "Opaque") -> dict:
        return {"apiVersion": "v1", "kind": "Secret", "type": kind,
                "metadata": {"name": name, "namespace": namespace}, "stringData": data}

    return [
        secret("bifrost-secrets", guard.TENANT_NS, {
            "BIFROST_SECRET_KEY": values["secret_key"],
            "BIFROST_DATABASE_URL": f"postgresql+asyncpg://{db}",
            "BIFROST_DATABASE_URL_SYNC": f"postgresql://{db}",
            "BIFROST_S3_SECRET_KEY": values["s3_key"],
            "BIFROST_DEFAULT_USER_EMAIL": BENCH_USER_EMAIL,
            "BIFROST_DEFAULT_USER_PASSWORD": values["user_password"],
        }),
        secret("bifrost-db-app", guard.TENANT_NS, {"username": "bifrost", "password": values["db_password"]},
               "kubernetes.io/basic-auth"),
        secret("bifrost-db-superuser", guard.TENANT_NS, {"username": "postgres", "password": values["superuser_password"]},
               "kubernetes.io/basic-auth"),
        secret("bench-infra", guard.INFRA_NS, {
            "SEAWEEDFS_SECRET_KEY": values["s3_key"],
            "BIFROST_DEFAULT_USER_EMAIL": BENCH_USER_EMAIL,
            "BIFROST_DEFAULT_USER_PASSWORD": values["user_password"],
            "BENCH_DB_URL": f"postgresql://postgres:{values['superuser_password']}@bifrost-db-rw.{guard.TENANT_NS}:5432/bifrost",
        }),
    ]


def fixed_components(profile: dict) -> dict[str, envelope.Resources]:
    """Effective resources of every always-on tenant pod (init containers match their main container)."""
    components = profile["components"]

    def size(name: str) -> envelope.Resources:
        return envelope.Resources(envelope.parse_cpu(components[name]["cpu"]), envelope.parse_memory(components[name]["memory"]))

    return {
        "api": size("api") * components["api"]["replicas"],
        "client": size("client") * components["client"]["replicas"],
        "redis": size("redis"),
        "postgres": size("postgres"),
        "pooler": size("pooler"),
    }


def worker_resources(profile: dict) -> envelope.Resources:
    return envelope.worker_job_resources(profile["worker_policy"]["worker_memory_mib"], profile["worker_cpu"])


def check_profile(profile: dict) -> dict:
    """Prove the profile's maximum occupancy fits its quota before anything is applied."""
    policy = profile["worker_policy"]
    if policy["largest_worker_mib"] != policy["worker_memory_mib"]:
        raise envelope.EnvelopeError("profiles use one worker size: largest_worker_mib must equal worker_memory_mib")
    occupancy = envelope.max_occupancy(fixed_components(profile), worker_resources(profile), policy["max_workers"])
    quota = profile["quota"]
    headroom = envelope.check_fits(occupancy, {
        "requests.cpu": quota["cpu"], "limits.cpu": quota["cpu"],
        "requests.memory": quota["memory"], "limits.memory": quota["memory"],
    })
    return {"max_occupancy": occupancy.format(), "headroom": headroom.format()}


def _resources(cpu: str, memory: str) -> dict:
    return {"requests": {"cpu": cpu, "memory": memory}, "limits": {"cpu": cpu, "memory": memory}}


def tenant_patches(profile: dict, node_ip: str) -> list[dict]:
    components = profile["components"]
    api, client = components["api"], components["client"]
    worker_mib = str(profile["worker_policy"]["worker_memory_mib"])
    config = {
        **profile["env"],
        "BIFROST_POOL_WORKER_MEMORY_REQUEST_MIB": worker_mib,
        "BIFROST_POOL_WORKER_CPU_REQUEST": profile["worker_cpu"],
        "BIFROST_POOL_WORKER_CPU_LIMIT": profile["worker_cpu"],
    }
    quota = profile["quota"]

    def patch(kind: str, name: str, ops: list[dict]) -> dict:
        return {"target": {"kind": kind, "name": name}, "patch": json.dumps(ops)}

    return [
        patch("ResourceQuota", "tenant-envelope", [{"op": "replace", "path": "/spec/hard", "value": {
            "requests.cpu": quota["cpu"], "limits.cpu": quota["cpu"],
            "requests.memory": quota["memory"], "limits.memory": quota["memory"],
            "requests.storage": quota["storage"], "persistentvolumeclaims": str(quota["pvcs"]), "pods": str(quota["pods"]),
        }}]),
        patch("Deployment", "bifrost-api", [
            {"op": "replace", "path": "/spec/replicas", "value": api["replicas"]},
            {"op": "replace", "path": "/spec/template/spec/initContainers/0/resources", "value": _resources(api["cpu"], api["memory"])},
            {"op": "replace", "path": "/spec/template/spec/containers/0/resources", "value": _resources(api["cpu"], api["memory"])},
        ]),
        patch("Deployment", "bifrost-client", [
            {"op": "replace", "path": "/spec/replicas", "value": client["replicas"]},
            {"op": "replace", "path": "/spec/template/spec/containers/0/resources", "value": _resources(client["cpu"], client["memory"])},
        ]),
        patch("StatefulSet", "redis", [
            {"op": "replace", "path": "/spec/template/spec/containers/0/resources",
             "value": _resources(components["redis"]["cpu"], components["redis"]["memory"])},
        ]),
        patch("Cluster", "bifrost-db", [
            {"op": "replace", "path": "/spec/resources", "value": _resources(components["postgres"]["cpu"], components["postgres"]["memory"])},
            {"op": "replace", "path": "/spec/storage/size", "value": components["postgres"]["storage"]},
        ]),
        patch("Pooler", "bifrost-db-pooler", [
            {"op": "replace", "path": "/spec/template/spec/containers/0/resources",
             "value": _resources(components["pooler"]["cpu"], components["pooler"]["memory"])},
        ]),
        patch("ConfigMap", "bifrost-config", [{"op": "add", "path": f"/data/{key}", "value": value} for key, value in config.items()]),
        patch("NetworkPolicy", "bench-egress", [{"op": "add", "path": "/spec/egress/-", "value": {
            "to": [{"ipBlock": {"cidr": f"{node_ip}/32"}}], "ports": [{"protocol": "TCP", "port": 6443}],
        }}]),
    ]


def render(identity: guard.Identity, name: str, kustomization: dict) -> str:
    """Render ``manifests/<name>`` through a generated kustomization in the cluster state directory."""
    directory = identity.state_dir / "render" / name
    directory.mkdir(parents=True, exist_ok=True)
    kustomization = {"resources": [os.path.relpath(MANIFESTS / name, directory)], **kustomization}
    (directory / "kustomization.yaml").write_text(json.dumps(kustomization, indent=2), encoding="utf-8")
    return subprocess.run(
        ["kubectl", "kustomize", "--load-restrictor", "LoadRestrictionsNone", str(directory)],
        capture_output=True, text=True, check=True,
    ).stdout


def images(identity: guard.Identity) -> dict[str, str]:
    tag = f"kind-{identity.worktree_hash}"
    return {"api": f"bifrost-api:{tag}", "client": f"bifrost-client:{tag}"}


def render_tenant(identity: guard.Identity, profile: dict, node_ip: str) -> str:
    tag = f"kind-{identity.worktree_hash}"
    return render(identity, "tenant", {
        "images": [
            {"name": "ghcr.io/gobifrost/bifrost-api", "newName": "bifrost-api", "newTag": tag},
            {"name": "ghcr.io/gobifrost/bifrost-client", "newName": "bifrost-client", "newTag": tag},
        ],
        "patches": tenant_patches(profile, node_ip),
    })


def render_infra(identity: guard.Identity, profile: dict) -> str:
    latency = {"op": "replace", "path": "/spec/template/spec/containers/0/env/0/value", "value": str(profile["fake_llm_latency_ms"])}
    kustomization = {"patches": [{"target": {"kind": "Deployment", "name": "bench-fake-llm"}, "patch": json.dumps([latency])}]}
    return render(identity, "infra", kustomization).replace("BENCH_IMAGE", images(identity)["api"])


def cnpg_manifest(identity: guard.Identity) -> Path:
    """The pinned operator release, downloaded once and checked against its recorded digest."""
    path = identity.state_dir / f"cnpg-{CNPG_VERSION}.yaml"
    if not path.exists():
        with urllib.request.urlopen(CNPG_URL, timeout=60) as response:  # noqa: S310 - pinned https URL
            body = response.read()
        path.write_bytes(body)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != CNPG_SHA256:
        path.unlink()
        raise SystemExit(f"CNPG manifest digest {digest} does not match the pinned {CNPG_SHA256}")
    return path


def install_cnpg(cluster: Cluster, identity: guard.Identity) -> None:
    cluster.kubectl("apply", "--server-side", "-f", str(cnpg_manifest(identity)), namespaced=False)
    cluster.kubectl("rollout", "status", "-n", "cnpg-system", "deployment/cnpg-controller-manager", "--timeout=300s", namespaced=False)


def _probe(cluster: Cluster, namespace: str, targets: list[str]) -> list[bool]:
    """Run a one-shot connect probe in ``namespace`` (inside the quota: requests == limits)."""
    name = f"bench-egress-probe-{secrets.token_hex(3)}"
    pod = {
        "apiVersion": "v1", "kind": "Pod", "metadata": {"name": name, "namespace": namespace},
        "spec": {"restartPolicy": "Never", "automountServiceAccountToken": False, "containers": [{
            "name": "probe", "image": images(cluster.identity)["api"], "imagePullPolicy": "IfNotPresent",
            "command": ["python", "-c", PROBE_SCRIPT, *targets], "resources": _resources("100m", "128Mi"),
        }]},
    }
    cluster.kubectl("apply", "-f", "-", input=json.dumps(pod), namespaced=False)
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            phase = cluster.kubectl("get", "pod", name, "-n", namespace, "-o", "jsonpath={.status.phase}", namespaced=False)
            if phase in ("Succeeded", "Failed"):
                break
            time.sleep(1)
        else:
            raise SystemExit(f"egress probe in {namespace} did not finish")
        words = cluster.kubectl("logs", name, "-n", namespace, namespaced=False).split()
    finally:
        cluster.kubectl("delete", "pod", name, "-n", namespace, "--ignore-not-found", "--wait=false", namespaced=False)
    if len(words) != len(targets):
        raise SystemExit(f"egress probe in {namespace} printed {words!r}")
    return [word == "True" for word in words]


def prove_egress_isolation(cluster: Cluster, node_ip: str) -> dict:
    """Show the external target is reachable without policy and unreachable from the policed namespaces."""
    external = f"{EGRESS_TARGET[0]}:{EGRESS_TARGET[1]}"
    dns = "kube-dns.kube-system.svc.cluster.local:53"
    cluster.kubectl("create", "namespace", "bench-probe", namespaced=False, check=False)
    try:
        (unpoliced,) = _probe(cluster, "bench-probe", [external])
    finally:
        cluster.kubectl("delete", "namespace", "bench-probe", "--wait=false", namespaced=False, check=False)
    result: dict[str, object] = {"unpoliced_external": unpoliced}
    for namespace, extra in ((guard.TENANT_NS, [f"{node_ip}:6443"]), (guard.INFRA_NS, [])):
        reached = _probe(cluster, namespace, [external, dns, *extra])
        guard.check_egress_probe(unpoliced, reached[0], all(reached[1:]))
        result[namespace] = dict(zip(["external", "dns", *extra], reached))
    return result


def node_ip(cluster: Cluster) -> str:
    node = json.loads(cluster.kubectl("get", "nodes", "-o", "json", namespaced=False))["items"][0]
    address = next(a["address"] for a in node["status"]["addresses"] if a["type"] == "InternalIP")
    socket.inet_aton(address)
    return address


FIRST_KINDS = frozenset({"Namespace", "ResourceQuota", "LimitRange", "NetworkPolicy"})


def split_kinds(rendered: str, kinds: frozenset[str]) -> tuple[str, str]:
    """Split a rendered multi-document stream into (documents of ``kinds``, the rest)."""
    chosen: list[str] = []
    rest: list[str] = []
    for document in rendered.split("\n---\n"):
        kind = next((line[6:].strip() for line in document.splitlines() if line.startswith("kind: ")), None)
        (chosen if kind in kinds else rest).append(document)
    return "\n---\n".join(chosen), "\n---\n".join(rest)


def apply(cluster: Cluster, profile: dict) -> dict:
    """Namespaces, quota and egress policy first; prove isolation; then workloads; validate the envelope."""
    identity = cluster.identity
    envelope_check = check_profile(profile)
    ip = node_ip(cluster)
    tenant_first, tenant_rest = split_kinds(render_tenant(identity, profile, ip), FIRST_KINDS)
    infra_first, infra_rest = split_kinds(render_infra(identity, profile), FIRST_KINDS)
    for documents in (infra_first, tenant_first):
        cluster.kubectl("apply", "-f", "-", input=documents, namespaced=False)
    isolation = prove_egress_isolation(cluster, ip)
    for manifest in secret_manifests(identity):
        cluster.kubectl("apply", "-f", "-", input=json.dumps(manifest), namespaced=False)
    configmap = cluster.kubectl(
        "create", "configmap", "bench-scripts", f"--from-file={HERE / 'incluster'}", "-n", guard.INFRA_NS,
        "--dry-run=client", "-o", "yaml", namespaced=False,
    )
    for documents in (configmap, infra_rest, tenant_rest):
        cluster.kubectl("apply", "-f", "-", input=documents, namespaced=False)
    cluster.kubectl("wait", "--for=condition=Ready", "cluster/bifrost-db", "--timeout=600s")
    for kind_name in ("deployment/bifrost-db-pooler", "deployment/bifrost-api", "deployment/bifrost-client"):
        cluster.kubectl("rollout", "status", kind_name, "--timeout=600s")
    cluster.kubectl("rollout", "status", "statefulset/redis", "--timeout=300s")
    cluster.kubectl("wait", "--for=condition=Ready", "pod/bench-driver", "-n", guard.INFRA_NS, "--timeout=300s", namespaced=False)
    return {"envelope": envelope_check, "egress": isolation, "admitted": reconcile_admitted(cluster)}


def reconcile_admitted(cluster: Cluster) -> dict:
    """Every admitted tenant pod's effective request must equal the quota's recorded usage."""
    pods = json.loads(cluster.kubectl("get", "pods", "-o", "json"))["items"]
    quota = json.loads(cluster.kubectl("get", "resourcequota", "tenant-envelope", "-o", "json"))
    result = envelope.reconcile(pods, quota["status"])
    if not result["match"]:
        raise envelope.EnvelopeError(f"admitted pods {result['computed'].format()} disagree with quota usage {result['used'].format()}")
    return {"admitted": result["computed"].format(), "pods": len(pods)}


def build_images(identity: guard.Identity) -> dict[str, str]:
    """Build the client production image; the API image is built by local-kind.sh."""
    tags = images(identity)
    subprocess.run(["docker", "build", "--file", str(ROOT / "client" / "Dockerfile"), "--tag", tags["client"], str(ROOT / "client")], check=True)
    return tags
