"""Fail-closed identity, destination, and egress validation for the bench."""

from __future__ import annotations

import hashlib
import ipaddress
import secrets
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

TENANT_NS = "bench-tenant"
INFRA_NS = "bench-infra"


class GuardError(Exception):
    """A benchmark guard condition was not met."""


@dataclass(frozen=True)
class Identity:
    worktree_hash: str
    cluster_name: str
    context: str
    state_dir: Path
    kubeconfig: Path
    node_name: str
    sentinel_path: Path


def expected_identity(root: Path) -> Identity:
    """Return the non-overridable Kind identity for ``root``."""
    worktree_hash = hashlib.sha256(str(root).encode()).hexdigest()[:12]
    cluster_name = f"bifrost-{worktree_hash}"
    state_dir = Path(f"/tmp/bifrost-k8s-{worktree_hash}")
    return Identity(
        worktree_hash=worktree_hash,
        cluster_name=cluster_name,
        context=f"kind-{cluster_name}",
        state_dir=state_dir,
        kubeconfig=state_dir / "kubeconfig",
        node_name=f"{cluster_name}-control-plane",
        sentinel_path=state_dir / "sentinel-id",
    )


def check_creation(docker_host: str | None, cluster_name: str, identity: Identity) -> None:
    """Require local Docker and this worktree's cluster name."""
    if docker_host not in (None, "") and not docker_host.startswith("unix://"):
        raise GuardError("Docker host must be a local unix:// socket")
    if cluster_name != identity.cluster_name:
        raise GuardError(f"cluster name must be {identity.cluster_name}")


def check_kubeconfig(config: dict, identity: Identity) -> None:
    """Require the expected local Kind context in a raw kubeconfig document."""
    contexts = {item.get("name"): item.get("context", {}) for item in config.get("contexts", [])}
    context = contexts.get(identity.context)
    if context is None:
        raise GuardError(f"kubeconfig is missing context {identity.context}")
    clusters = {item.get("name"): item.get("cluster", {}) for item in config.get("clusters", [])}
    cluster = clusters.get(context.get("cluster"))
    if cluster is None:
        raise GuardError(f"context {identity.context} has no cluster")
    parsed = urlparse(str(cluster.get("server", "")))
    if parsed.scheme != "https" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise GuardError(f"context {identity.context} must use a local https server")
    current = config.get("current-context")
    if current and current != identity.context:
        raise GuardError(f"current context must be {identity.context}")


def check_nodes(nodes: dict, identity: Identity) -> None:
    """Require the one expected local Kind control-plane node."""
    items = nodes.get("items", [])
    if len(items) != 1:
        raise GuardError(f"expected exactly one node, found {len(items)}")
    node = items[0]
    name = node.get("metadata", {}).get("name")
    if name != identity.node_name:
        raise GuardError(f"node name must be {identity.node_name}")
    provider_id = node.get("spec", {}).get("providerID", "")
    expected_suffix = f"/{identity.node_name}"
    if not provider_id.startswith("kind://docker/") or not provider_id.endswith(expected_suffix):
        raise GuardError(f"node {identity.node_name} must use its local Kind providerID")


def check_sentinel(configmap: dict | None, recorded_id: str | None) -> None:
    """Require the cluster sentinel to agree with the recorded random ID."""
    cluster_id = (configmap or {}).get("data", {}).get("cluster-id")
    if not recorded_id or not cluster_id:
        raise GuardError("benchmark sentinel is missing")
    if cluster_id != recorded_id:
        raise GuardError("benchmark sentinel does not match the recorded cluster ID")


def new_sentinel_id() -> str:
    """Return a random sentinel ID for a newly created benchmark cluster."""
    return secrets.token_hex(16)


def sentinel_configmap(cluster_id: str) -> dict:
    """Build the sentinel ConfigMap manifest."""
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "bench-sentinel", "namespace": "kube-system"},
        "data": {"cluster-id": cluster_id},
    }


def check_fault_target(pod: dict, namespace: str, required_labels: dict[str, str]) -> None:
    """Require a fault target to be the labelled tenant pod."""
    metadata = pod.get("metadata", {})
    if namespace != TENANT_NS or metadata.get("namespace") != TENANT_NS:
        raise GuardError(f"fault target must be in {TENANT_NS}")
    labels = metadata.get("labels", {})
    missing = [f"{key}={value}" for key, value in required_labels.items() if labels.get(key) != value]
    if missing:
        raise GuardError(f"fault target is missing required labels: {', '.join(missing)}")


def _destination_host(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"//{url}")
    try:
        host = parsed.hostname
    except ValueError as error:
        raise GuardError("destination has an invalid host") from error
    if not host:
        raise GuardError("destination has no host")
    return host.lower()


def service_namespace(url: str, home_ns: str) -> str:
    """Resolve a permitted in-cluster Service URL to its namespace."""
    host = _destination_host(url)
    if host == "localhost":
        raise GuardError(f"destination host {host} is not a Service")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise GuardError(f"destination host {host} is not a Service")
    labels = host.split(".")
    if any(not label or not label.replace("-", "").isalnum() for label in labels):
        raise GuardError(f"destination host {host} is not a Service")
    if len(labels) == 1:
        return home_ns
    if len(labels) == 2:
        return labels[1]
    if len(labels) == 3 and labels[2] == "svc":
        return labels[1]
    if len(labels) == 5 and labels[2:] == ["svc", "cluster", "local"]:
        return labels[1]
    raise GuardError(f"destination host {host} is not a Service")


def check_destinations(urls: dict[str, str], home_ns: str) -> None:
    """Require every configured destination to be a tenant or infra Service."""
    offending: list[str] = []
    for key, url in urls.items():
        if not url:
            continue
        try:
            host = _destination_host(url)
            namespace = service_namespace(url, home_ns)
            if namespace not in {TENANT_NS, INFRA_NS}:
                offending.append(f"{key}={host}")
        except GuardError:
            try:
                host = _destination_host(url)
            except GuardError:
                host = "invalid-host"
            offending.append(f"{key}={host}")
    if offending:
        raise GuardError(f"outbound destinations must be benchmark Services: {', '.join(offending)}")


def check_egress_probe(unpoliced_external_ok: bool, external_ok: bool, internal_ok: bool) -> None:
    """Require proof that egress policy, not the network, blocks the external target.

    The target must be reachable from an unpoliced namespace, unreachable from
    the policed one, and in-cluster Services must stay reachable.
    """
    if not unpoliced_external_ok:
        raise GuardError("egress probe target is unreachable without policy; enforcement cannot be shown")
    if external_ok or not internal_ok:
        raise GuardError("egress probe must deny external access and allow in-cluster access")
