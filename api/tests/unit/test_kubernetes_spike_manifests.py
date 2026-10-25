from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
LOCAL_K8S_DIR = REPO_ROOT / "k8s" / "local"
SCRIPT = REPO_ROOT / "scripts" / "kubernetes" / "local-kind.sh"

def _load_documents() -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for path in sorted(LOCAL_K8S_DIR.glob("*.yaml")):
        if path.name == "kind-config.yaml":
            continue
        with path.open() as handle:
            docs.extend(doc for doc in yaml.safe_load_all(handle) if doc)
    return docs


def _workloads() -> list[dict[str, Any]]:
    return [
        doc
        for doc in _load_documents()
        if doc.get("kind") in {"Deployment", "StatefulSet", "Job"}
    ]


def _pod_spec(workload: dict[str, Any]) -> dict[str, Any]:
    spec = workload["spec"]
    if workload["kind"] == "Job":
        return spec["template"]["spec"]
    return spec["template"]["spec"]


def _containers(workload: dict[str, Any]) -> list[dict[str, Any]]:
    pod_spec = _pod_spec(workload)
    return list(pod_spec.get("initContainers", [])) + list(pod_spec.get("containers", []))


def _doc(kind: str, name: str) -> dict[str, Any]:
    for doc in _load_documents():
        if doc.get("kind") == kind and doc.get("metadata", {}).get("name") == name:
            return doc
    raise AssertionError(f"{kind}/{name} not found")


def test_only_the_api_mounts_a_service_account_token() -> None:
    """The API runs the worker controller (api-rbac.yaml); nothing else may."""
    for workload in _workloads():
        spec = _pod_spec(workload)
        if workload["metadata"]["name"] == "bifrost-api":
            assert spec["serviceAccountName"] == "bifrost-api"
            assert spec["automountServiceAccountToken"] is True
        else:
            assert spec.get("automountServiceAccountToken") is False, (
                workload["kind"],
                workload["metadata"]["name"],
            )


def test_local_manifests_do_not_bind_host_ports_or_node_ports() -> None:
    assert "hostPort" not in (LOCAL_K8S_DIR / "kind-config.yaml").read_text()
    assert "extraPortMappings" not in (LOCAL_K8S_DIR / "kind-config.yaml").read_text()

    for doc in _load_documents():
        if doc.get("kind") == "Service":
            assert doc["spec"].get("type", "ClusterIP") != "NodePort", doc["metadata"]["name"]
            for port in doc["spec"].get("ports", []):
                assert "nodePort" not in port, doc["metadata"]["name"]

    for workload in _workloads():
        for container in _containers(workload):
            for port in container.get("ports", []):
                assert "hostPort" not in port, (
                    workload["metadata"]["name"],
                    container["name"],
                )


def test_every_local_container_has_cpu_and_memory_limits() -> None:
    for workload in _workloads():
        for container in _containers(workload):
            resources = container.get("resources") or {}
            requests = resources.get("requests") or {}
            limits = resources.get("limits") or {}
            assert requests.get("cpu"), (workload["metadata"]["name"], container["name"])
            assert requests.get("memory"), (workload["metadata"]["name"], container["name"])
            assert limits.get("cpu"), (workload["metadata"]["name"], container["name"])
            assert limits.get("memory"), (workload["metadata"]["name"], container["name"])


def test_static_executors_have_fixed_replicas_and_no_autoscaler() -> None:
    worker = _doc("Deployment", "bifrost-worker")
    assert worker["spec"]["replicas"] == 1  # Extra capacity comes from worker Jobs.
    worker_container = _containers(worker)[0]
    readiness = worker_container["readinessProbe"]["exec"]["command"]
    assert readiness == ["python", "-m", "src.worker.health", "--ready"]

    autoscaling_kinds = {
        "HorizontalPodAutoscaler",
        "VerticalPodAutoscaler",
        "ScaledObject",
    }
    assert all(doc.get("kind") not in autoscaling_kinds for doc in _load_documents())


def test_spike_environment_and_secret_free_status_contracts() -> None:
    config = _doc("ConfigMap", "bifrost-local-config")
    assert config["data"]["BIFROST_ENVIRONMENT"] == "testing"
    assert config["data"]["BIFROST_KUBERNETES_SPIKE"] == "1"

    secret = _doc("Secret", "bifrost-local-secrets")
    assert not any("RABBITMQ" in key for key in secret["stringData"])

    script = SCRIPT.read_text()
    assert "umask 077" in script
    assert 'IMAGE_NAME="${BIFROST_K8S_IMAGE:-bifrost-api:kind-${WORKTREE_HASH}}"' in script
    assert "docker image inspect --format '{{.Id}}'" in script
    assert "bifrost.local/runtime-revision" in script
    assert "apply_runtime_manifest" in script
    assert "annotate_runtime_revision" not in script
    assert "kubectl_local patch deployment/bifrost" not in script
    assert "trap 'collect_on_failure $?'" in script
    assert "collect || true" in script
    assert "keda" not in script.lower()
    assert "rabbitmq" not in script.lower()
    assert "dev@gobifrost.com / password" not in script


def test_runtime_renderer_sets_image_and_quoted_revision_on_all_deployments(tmp_path) -> None:
    image = "bifrost-api:test-render"
    revision = "1234567890123456"
    # Exercise the actual shell function without running the cluster entrypoint.
    script = SCRIPT.read_text()
    start = script.index("render_runtime_manifest() {")
    end = script.index("\n}\n", start) + 3
    renderer = script[start:end]
    output = tmp_path / "runtime.yaml"
    subprocess.run(
        [
            "bash", "-c",
            renderer + '\nIMAGE_NAME="$1"; render_runtime_manifest "$2" "$3" "$4"',
            "renderer-test", image, str(LOCAL_K8S_DIR / "bifrost.yaml"),
            str(output), revision,
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    deployments = {
        doc["metadata"]["name"]: doc
        for doc in yaml.safe_load_all(output.read_text())
        if doc and doc.get("kind") == "Deployment"
    }
    assert set(deployments) == {
        "bifrost-api",
        "bifrost-scheduler",
        "bifrost-worker",
    }

    for deployment in deployments.values():
        template = deployment["spec"]["template"]
        annotations = template["metadata"]["annotations"]
        assert annotations["bifrost.local/runtime-revision"] == revision
        assert isinstance(annotations["bifrost.local/runtime-revision"], str)
        assert template["spec"]["containers"][0]["image"] == image


def test_local_readme_documents_supported_entrypoints_and_worker_jobs() -> None:
    readme = (LOCAL_K8S_DIR / "README.md").read_text()
    assert "./test.sh kubernetes up" in readme
    assert "app.kubernetes.io/name=bifrost-pool-worker" in readme
    assert "keda" not in readme.lower()
