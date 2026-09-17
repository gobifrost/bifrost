from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
ELASTIC_BUILDS_DIR = REPO_ROOT / "deploy" / "kubernetes" / "builds"
LOCAL_BUILD_JOBS = REPO_ROOT / "k8s" / "local" / "build-jobs.yaml"
LOCAL_SCRIPT = REPO_ROOT / "scripts" / "kubernetes" / "local-kind.sh"


EXPECTED_BUILD_CONFIG = {
    "BIFROST_PLATFORM_BUILD_BACKEND": "kubernetes",
    "BIFROST_KUBERNETES_BUILD_NAMESPACE": "bifrost",
    "BIFROST_KUBERNETES_BUILD_IMAGE": "ghcr.io/gobifrost/bifrost-api:REPLACE_WITH_COMPATIBLE_TAG",
    "BIFROST_KUBERNETES_BUILD_CONFIGMAP": "bifrost-config",
    "BIFROST_KUBERNETES_BUILD_SECRET": "bifrost-secrets",
    "BIFROST_KUBERNETES_BUILD_SERVICE_ACCOUNT": "bifrost-build-runner",
    "BIFROST_KUBERNETES_BUILD_JOB_TYPES": "application.deploy,application.sdk_update",
    "BIFROST_KUBERNETES_BUILD_MEMORY_REQUEST_MIB": "512",
    "BIFROST_KUBERNETES_BUILD_MEMORY_LIMIT_MIB": "2048",
    "BIFROST_KUBERNETES_BUILD_MAX_JOBS": "2",
    "BIFROST_KUBERNETES_BUILD_PENDING_TIMEOUT_SECONDS": "300",
    "BIFROST_KUBERNETES_BUILD_CPU_REQUEST": "250m",
    "BIFROST_KUBERNETES_BUILD_CPU_LIMIT": "1",
}


def _load_yaml(path: Path) -> list[dict[str, Any]]:
    with path.open() as handle:
        return [doc for doc in yaml.safe_load_all(handle) if doc]


def _docs_by_kind_name(paths: list[Path]) -> dict[tuple[str, str], dict[str, Any]]:
    docs: dict[tuple[str, str], dict[str, Any]] = {}
    for path in paths:
        for doc in _load_yaml(path):
            docs[(doc["kind"], doc["metadata"]["name"])] = doc
    return docs


def _container_env_from(patch: dict[str, Any], container_name: str) -> list[dict[str, Any]]:
    containers = patch["spec"]["template"]["spec"]["containers"]
    for container in containers:
        if container["name"] == container_name:
            return container["envFrom"]
    raise AssertionError(f"container {container_name} not found")


def test_elastic_builds_overlay_is_opt_in_and_targets_existing_api_scheduler() -> None:
    root_kustomization = yaml.safe_load((REPO_ROOT / "k8s" / "kustomization.yml").read_text())
    assert "elastic-builds" not in "\n".join(root_kustomization["resources"])

    overlay = yaml.safe_load((ELASTIC_BUILDS_DIR / "kustomization.yaml").read_text())
    assert overlay["resources"] == [
        "../../../k8s",
        "serviceaccounts.yaml",
        "rbac.yaml",
        "build-config.yaml",
    ]
    assert {"path": "api-env-patch.yaml"} in overlay["patches"]
    assert {"path": "scheduler-controller-patch.yaml"} in overlay["patches"]


def test_elastic_builds_rbac_is_limited_to_jobs_and_read_only_pods() -> None:
    docs = _docs_by_kind_name(
        [
            ELASTIC_BUILDS_DIR / "serviceaccounts.yaml",
            ELASTIC_BUILDS_DIR / "rbac.yaml",
        ]
    )

    controller_sa = docs[("ServiceAccount", "bifrost-build-controller")]
    runner_sa = docs[("ServiceAccount", "bifrost-build-runner")]
    assert controller_sa["automountServiceAccountToken"] is True
    assert runner_sa["automountServiceAccountToken"] is False

    role = docs[("Role", "bifrost-build-controller")]
    rules = role["rules"]
    assert rules == [
        {
            "apiGroups": ["batch"],
            "resources": ["jobs"],
            "verbs": ["get", "list", "create", "patch", "delete"],
        },
        {
            "apiGroups": [""],
            "resources": ["pods"],
            "verbs": ["get", "list"],
        },
    ]

    binding = docs[("RoleBinding", "bifrost-build-controller")]
    assert binding["subjects"] == [
        {
            "kind": "ServiceAccount",
            "name": "bifrost-build-controller",
            "namespace": "bifrost",
        }
    ]
    assert binding["roleRef"]["name"] == "bifrost-build-controller"


def test_elastic_builds_config_and_patches_are_explicit() -> None:
    config = _load_yaml(ELASTIC_BUILDS_DIR / "build-config.yaml")[0]
    assert config["data"] == EXPECTED_BUILD_CONFIG

    api_patch = _load_yaml(ELASTIC_BUILDS_DIR / "api-env-patch.yaml")[0]
    assert api_patch["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
    assert {"configMapRef": {"name": "bifrost-build-jobs-config"}} in _container_env_from(
        api_patch, "api"
    )

    scheduler_patch = _load_yaml(ELASTIC_BUILDS_DIR / "scheduler-controller-patch.yaml")[0]
    scheduler_spec = scheduler_patch["spec"]["template"]["spec"]
    assert scheduler_spec["serviceAccountName"] == "bifrost-build-controller"
    assert scheduler_spec["automountServiceAccountToken"] is True
    assert {"configMapRef": {"name": "bifrost-build-jobs-config"}} in _container_env_from(
        scheduler_patch, "scheduler"
    )


def test_local_build_jobs_manifest_uses_local_namespace_and_renderable_image() -> None:
    docs = _docs_by_kind_name([LOCAL_BUILD_JOBS])
    assert docs[("ServiceAccount", "bifrost-build-controller")]["metadata"]["namespace"] == (
        "bifrost-local"
    )
    assert (
        docs[("ServiceAccount", "bifrost-build-runner")]["automountServiceAccountToken"]
        is False
    )

    role = docs[("Role", "bifrost-build-controller")]
    assert role["rules"][0]["resources"] == ["jobs"]
    assert role["rules"][0]["verbs"] == ["get", "list", "create", "patch", "delete"]
    assert role["rules"][1]["resources"] == ["pods"]
    assert role["rules"][1]["verbs"] == ["get", "list"]

    config = docs[("ConfigMap", "bifrost-build-jobs-config")]
    assert config["data"] == {
        **EXPECTED_BUILD_CONFIG,
        "BIFROST_KUBERNETES_BUILD_NAMESPACE": "bifrost-local",
        "BIFROST_KUBERNETES_BUILD_IMAGE": "bifrost-api:kind-local",
        "BIFROST_KUBERNETES_BUILD_CONFIGMAP": "bifrost-local-config",
        "BIFROST_KUBERNETES_BUILD_SECRET": "bifrost-local-secrets",
    }


def test_local_kind_build_jobs_are_explicitly_opt_in_and_patch_only_scheduler_token() -> None:
    script = LOCAL_SCRIPT.read_text()
    assert "BIFROST_KIND_BUILD_JOBS    Set to 1" in script
    assert 'if [[ "${BIFROST_KIND_BUILD_JOBS:-}" != "1" ]]; then' in script
    assert 'apply_local_manifest "${ROOT_DIR}/k8s/local/build-jobs.yaml"' in script
    assert "kubectl_local set env deployment/bifrost-api" not in script
    assert "kubectl_local set env deployment/bifrost-scheduler" not in script
    assert "kubectl_local patch deployment/bifrost-scheduler" not in script
    assert "kubectl_local patch deployment/bifrost-worker" not in script


def test_local_renderer_replaces_build_job_configmap_image(tmp_path) -> None:
    image = "bifrost-api:test-build-jobs"
    script = LOCAL_SCRIPT.read_text()
    start = script.index("render_with_image() {")
    end = script.index("\n}\n", start) + 3
    renderer = script[start:end]
    output = tmp_path / "build-jobs.yaml"
    subprocess.run(
        [
            "bash",
            "-c",
            renderer + '\nIMAGE_NAME="$1"; render_with_image "$2" "$3"',
            "renderer-test",
            image,
            str(LOCAL_BUILD_JOBS),
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    config = _docs_by_kind_name([output])[("ConfigMap", "bifrost-build-jobs-config")]
    assert config["data"]["BIFROST_KUBERNETES_BUILD_IMAGE"] == image


def test_local_runtime_renderer_inlines_build_jobs_in_one_apply(tmp_path) -> None:
    image = "bifrost-api:test-build-jobs"
    revision = "buildjobs1234567"
    script = LOCAL_SCRIPT.read_text()
    start = script.index("render_runtime_manifest() {")
    end = script.index("\n}\n", start) + 3
    renderer = script[start:end]
    output = tmp_path / "runtime.yaml"
    subprocess.run(
        [
            "bash",
            "-c",
            (
                renderer
                + '\nIMAGE_NAME="$1"; BIFROST_KIND_BUILD_JOBS=1; '
                + 'render_runtime_manifest "$2" "$3" "$4"'
            ),
            "renderer-test",
            image,
            str(REPO_ROOT / "k8s" / "local" / "bifrost.yaml"),
            str(output),
            revision,
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
    api = deployments["bifrost-api"]["spec"]["template"]["spec"]
    scheduler = deployments["bifrost-scheduler"]["spec"]["template"]["spec"]
    worker = deployments["bifrost-worker"]["spec"]["template"]["spec"]

    api_env_from = api["containers"][0]["envFrom"]
    scheduler_env_from = scheduler["containers"][0]["envFrom"]
    worker_env_from = worker["containers"][0]["envFrom"]
    build_config = {"configMapRef": {"name": "bifrost-build-jobs-config"}}

    assert api["automountServiceAccountToken"] is False
    assert build_config in api_env_from
    assert scheduler["automountServiceAccountToken"] is True
    assert scheduler["serviceAccountName"] == "bifrost-build-controller"
    assert build_config in scheduler_env_from
    assert worker["automountServiceAccountToken"] is False
    assert build_config not in worker_env_from
