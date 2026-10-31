"""Reversible, benchmark-only prerequisites for the authenticated browser run.

The browser observes the same deployed client the product serves.  This module
only supplies the in-cluster TLS termination production cookies require and a
short-lived, cached isolated-build configuration before timing begins.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import guard

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CLIENT_NGINX = ROOT / "client" / "nginx.conf"
TLS_CONFIGMAP = "bench-client-tls-config"
TLS_SECRET = "bench-client-tls"
TLS_HOST = "client.bench-tenant"
TLS_ORIGIN = f"https://{TLS_HOST}"
BUILD_SERVICE_ACCOUNT = "bench-build-runner"
DRAIN_IDLE_SECONDS = 15
BUILD_CPU = "250m"
NPM_CACHE = "/home/bifrost/.npm"
WORKER_SELECTOR = "app.kubernetes.io/name=bifrost-pool-worker"


def tls_nginx_config(source: str | None = None) -> str:
    """Return the product client configuration with only TLS listener changes."""
    config = source if source is not None else CLIENT_NGINX.read_text(encoding="utf-8")
    marker = "    listen 80;"
    if config.count(marker) != 1:
        raise ValueError("client nginx configuration must have exactly one HTTP listener")
    listener = "\n".join((
        "    listen 443 ssl;",
        "    ssl_certificate /etc/bench-tls/tls.crt;",
        "    ssl_certificate_key /etc/bench-tls/tls.key;",
    ))
    return config.replace(marker, listener)


def tls_workload_documents() -> tuple[dict[str, Any], dict[str, Any]]:
    """Patch the existing client Service and Deployment without a parallel proxy."""
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "client", "namespace": guard.TENANT_NS},
        "spec": {
            "ports": [{"name": "https", "port": 443, "targetPort": 443, "protocol": "TCP"}],
            "selector": {"app.kubernetes.io/name": "bifrost-client"},
        },
    }
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "bifrost-client", "namespace": guard.TENANT_NS},
        "spec": {"template": {"spec": {
            "containers": [{
                "name": "client",
                "ports": [{"containerPort": 443, "name": "https"}],
                "livenessProbe": {"httpGet": {"path": "/", "port": 443, "scheme": "HTTPS"}},
                "readinessProbe": {"httpGet": {"path": "/", "port": 443, "scheme": "HTTPS"}},
                "volumeMounts": [
                    {"name": "bench-client-tls-config", "mountPath": "/etc/nginx/conf.d", "readOnly": True},
                    {"name": "bench-client-tls", "mountPath": "/etc/bench-tls", "readOnly": True},
                ],
            }],
            "volumes": [
                {"name": "bench-client-tls-config", "configMap": {"name": TLS_CONFIGMAP}},
                {"name": "bench-client-tls", "secret": {"secretName": TLS_SECRET}},
            ],
        }}},
    }
    return service, deployment


def patch_tls_workload(cluster: Any, service: dict[str, Any], deployment: dict[str, Any]) -> None:
    """Replace only the client fields TLS observation requires.

    The tenant deployment was originally created with client-side apply.  A
    second partial apply would treat omitted previously-managed fields (image,
    resources, lifecycle, and template metadata) as deletions.  JSON Patch
    keeps those product fields intact while changing the listener contract.
    """
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    template = deployment["spec"]["template"]["spec"]
    service_patch = [{"op": "replace", "path": "/spec/ports", "value": service["spec"]["ports"]}]
    deployment_patch = [
        {"op": "replace", "path": "/spec/template/spec/containers/0/ports", "value": container["ports"]},
        {"op": "replace", "path": "/spec/template/spec/containers/0/livenessProbe", "value": container["livenessProbe"]},
        {"op": "replace", "path": "/spec/template/spec/containers/0/readinessProbe", "value": container["readinessProbe"]},
        {"op": "add", "path": "/spec/template/spec/containers/0/volumeMounts", "value": container["volumeMounts"]},
        {"op": "add", "path": "/spec/template/spec/volumes", "value": template["volumes"]},
    ]
    cluster.kubectl("patch", "service", "client", "--type=json", "-p", json.dumps(service_patch))
    cluster.kubectl("patch", "deployment", "bifrost-client", "--type=json", "-p", json.dumps(deployment_patch))


def build_runtime_patch(image: str) -> dict[str, str]:
    """Environment read by the normal isolated PlatformJob builder.

    The temporary image is the same tenant-local image identity workers use;
    no job namespace is changed.  It has a fixture-only npm cache prepared
    before this transition, and the ordinary app builder still runs npm and
    Vite with npm offline.
    """
    if not image or "@" in image or ":" not in image:
        raise ValueError("cached build image must be an explicit local name:tag")
    return {
        "BIFROST_KUBERNETES_JOB_IMAGE": image,
        "BIFROST_KUBERNETES_JOB_SERVICE_ACCOUNT": BUILD_SERVICE_ACCOUNT,
        "BIFROST_KUBERNETES_JOB_CPU_REQUEST": BUILD_CPU,
        "BIFROST_KUBERNETES_JOB_CPU_LIMIT": BUILD_CPU,
        "npm_config_offline": "true",
        "npm_config_cache": NPM_CACHE,
    }


def build_service_account() -> dict[str, Any]:
    """Tokenless identity for the temporary app-build Job in the tenant."""
    return {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {"name": BUILD_SERVICE_ACCOUNT, "namespace": guard.TENANT_NS},
        "automountServiceAccountToken": False,
    }


def prepare_build_runtime(cluster: Any, cached_image: str) -> tuple[dict[str, str | None], dict[str, Any]]:
    """Restart the API onto the temporary offline-build identity before deploy.

    Call only while the browser benchmark has no traffic.  The returned state
    is deliberately in-memory so it can restore ConfigMap keys exactly after
    the build without putting configuration values into a benchmark artifact.
    """
    cluster.verify()
    patch = build_runtime_patch(cached_image)
    config = json.loads(cluster.kubectl("get", "configmap", "bifrost-config", "-o", "json"))
    data = config.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("benchmark ConfigMap has no data")
    previous = {key: data.get(key) for key in patch}
    config["data"] = {**data, **patch}
    _apply(cluster, config)
    _apply(cluster, build_service_account())
    cluster.kubectl("rollout", "restart", "deployment/bifrost-api")
    cluster.kubectl("rollout", "status", "deployment/bifrost-api", "--timeout=300s")
    return previous, {
        "cached_build_image": cached_image,
        "cpu": {"request": BUILD_CPU, "limit": BUILD_CPU},
        "npm": {"offline": True, "cache": NPM_CACHE},
        "namespace": guard.TENANT_NS,
    }


def restore_build_runtime(cluster: Any, previous: dict[str, str | None]) -> None:
    """Restore temporary job-image/cache settings, then restart the API."""
    cluster.verify()
    operations = []
    for key, value in previous.items():
        path = "/data/" + key.replace("~", "~0").replace("/", "~1")
        operations.append({"op": "remove" if value is None else "replace", "path": path, **({} if value is None else {"value": value})})
    cluster.kubectl("patch", "configmap", "bifrost-config", "--type=json", "-p", json.dumps(operations))
    cluster.kubectl("rollout", "restart", "deployment/bifrost-api")
    cluster.kubectl("rollout", "status", "deployment/bifrost-api", "--timeout=300s")


def build_policy_prepare(original: dict[str, Any]) -> dict[str, Any]:
    """Disable warm capacity before the one isolated build, retaining its cap."""
    required = {
        "warm_workers", "max_workers", "worker_memory_mib", "largest_worker_mib",
        "idle_seconds", "isolated_jobs_enabled", "max_isolated_pods",
    }
    if not required <= set(original):
        raise ValueError("Kubernetes policy response is incomplete")
    return {
        "warm_workers": 0,
        "idle_seconds": DRAIN_IDLE_SECONDS,
        "isolated_jobs_enabled": True,
        "max_isolated_pods": 1,
    }


def restore_policy(original: dict[str, Any]) -> dict[str, Any]:
    """Restore every mutable Settings -> Kubernetes policy field exactly."""
    build_policy_prepare(original)
    return {key: original[key] for key in (
        "warm_workers", "max_workers", "worker_memory_mib", "largest_worker_mib",
        "idle_seconds", "isolated_jobs_enabled", "max_isolated_pods",
    )}


def wait_for_worker_count(cluster: Any, expected_ready: int, timeout_s: int = 300) -> dict[str, int]:
    """Wait for the temporary policy change to drain or restore worker capacity.

    Only reusable pool workers carry this selector.  The isolated
    ``application.deploy`` job intentionally does not, so this cannot mistake
    the normal builder itself for a warm worker that needs draining.
    """
    if expected_ready < 0:
        raise ValueError("expected worker count must not be negative")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        pods = json.loads(cluster.kubectl("get", "pods", "-l", WORKER_SELECTOR, "-o", "json"))
        live = [
            pod for pod in pods.get("items", [])
            if pod.get("metadata", {}).get("deletionTimestamp") is None
            and pod.get("status", {}).get("phase") not in {"Succeeded", "Failed"}
        ]
        ready = [
            pod for pod in live
            if any(condition.get("type") == "Ready" and condition.get("status") == "True"
                   for condition in pod.get("status", {}).get("conditions", []))
        ]
        if len(live) == expected_ready and len(ready) == expected_ready:
            return {"live": len(live), "ready": len(ready)}
        time.sleep(1)
    raise TimeoutError(f"worker pool did not reach {expected_ready} ready workers within {timeout_s}s")


def _apply(cluster: Any, document: dict[str, Any]) -> None:
    cluster.kubectl("apply", "-f", "-", input=json.dumps(document), namespaced=False)


def _configmap(cluster: Any, name: str, path: Path) -> None:
    rendered = cluster.kubectl(
        "create", "configmap", name, f"--from-file=default.conf={path}",
        "-n", guard.TENANT_NS, "--dry-run=client", "-o", "json", namespaced=False,
    )
    cluster.kubectl("apply", "-f", "-", input=rendered, namespaced=False)


def _certificate(cluster: Any, directory: Path) -> tuple[Path, Path]:
    cert, key = directory / "tls.crt", directory / "tls.key"
    sans = ",".join((
        f"DNS:{TLS_HOST}", "DNS:client.bench-tenant.svc",
        "DNS:client.bench-tenant.svc.cluster.local",
    ))
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", "1",
         "-subj", f"/CN={TLS_HOST}", "-addext", f"subjectAltName={sans}",
         "-keyout", str(key), "-out", str(cert)],
        check=True, capture_output=True, text=True,
    )
    rendered = cluster.kubectl(
        "create", "secret", "tls", TLS_SECRET, f"--cert={cert}", f"--key={key}",
        "-n", guard.TENANT_NS, "--dry-run=client", "-o", "json", namespaced=False,
    )
    cluster.kubectl("apply", "-f", "-", input=rendered, namespaced=False)
    return cert, key


def prepare_tls(cluster: Any) -> dict[str, Any]:
    """Install a one-day self-signed certificate and roll the real client to HTTPS."""
    cluster.verify()
    config = json.loads(cluster.kubectl("get", "configmap", "bifrost-config", "-o", "json"))
    data = config.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("benchmark ConfigMap has no data")
    config["data"] = {
        **data,
        "BIFROST_PUBLIC_URL": TLS_ORIGIN,
        "BIFROST_CORS_ORIGINS": TLS_ORIGIN,
        "BIFROST_WEBAUTHN_RP_ID": TLS_HOST,
        "BIFROST_WEBAUTHN_ORIGIN": TLS_ORIGIN,
    }
    _apply(cluster, config)
    with tempfile.TemporaryDirectory(prefix="bifrost-bench-client-tls-") as raw:
        directory = Path(raw)
        config = directory / "default.conf"
        config.write_text(tls_nginx_config(), encoding="utf-8")
        cert, _ = _certificate(cluster, directory)
        _configmap(cluster, TLS_CONFIGMAP, config)
        service, deployment = tls_workload_documents()
        patch_tls_workload(cluster, service, deployment)
        certificate_sha256 = hashlib.sha256(cert.read_bytes()).hexdigest()
    cluster.kubectl("rollout", "restart", "deployment/bifrost-api")
    cluster.kubectl("rollout", "status", "deployment/bifrost-api", "--timeout=300s")
    cluster.kubectl("rollout", "status", "deployment/bifrost-client", "--timeout=300s")
    return {
        "origin": TLS_ORIGIN,
        "self_signed": True,
        "browser_ignores_https_errors": True,
        "certificate_sha256": certificate_sha256,
        "nginx_config_sha256": hashlib.sha256(tls_nginx_config().encode()).hexdigest(),
        "client_resource_deviation": "client serves the existing image over TLS port 443 during browser observation",
    }
