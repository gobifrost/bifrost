from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

import yaml

from src.services.deployment_environment import (
    renamed_environment_variables,
    unread_environment_variables,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
ELASTIC_BUILDS_DIR = REPO_ROOT / "deploy" / "kubernetes" / "builds"
LOCAL_BUILD_JOBS = REPO_ROOT / "k8s" / "local" / "build-jobs.yaml"
LOCAL_SCRIPT = REPO_ROOT / "scripts" / "kubernetes" / "local-kind.sh"


# Jobs copy the API pod (namespace, image, envFrom); isolation is turned on
# in Settings -> Kubernetes. The overlay only names the tokenless runner
# account and the isolated-pod CPU and pending timeout.
EXPECTED_BUILD_CONFIG = {
    "BIFROST_KUBERNETES_JOB_SERVICE_ACCOUNT": "bifrost-build-runner",
    "BIFROST_KUBERNETES_JOB_PENDING_TIMEOUT_SECONDS": "300",
    "BIFROST_KUBERNETES_JOB_CPU_REQUEST": "250m",
    "BIFROST_KUBERNETES_JOB_CPU_LIMIT": "1",
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
    # The API keeps the base manifests' controller identity (k8s/api/rbac.yaml).
    assert "automountServiceAccountToken" not in api_patch["spec"]["template"]["spec"]
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


def test_local_build_jobs_manifest_matches_the_overlay() -> None:
    docs = _docs_by_kind_name([LOCAL_BUILD_JOBS])
    runner = docs[("ServiceAccount", "bifrost-build-runner")]
    assert runner["metadata"]["namespace"] == "bifrost-local"
    assert runner["automountServiceAccountToken"] is False
    # The API's own account (api-rbac.yaml) runs the build-Job controller.
    assert not any(name == "bifrost-build-controller" for _, name in docs)

    config = docs[("ConfigMap", "bifrost-build-jobs-config")]
    assert config["data"] == EXPECTED_BUILD_CONFIG


def test_local_kind_build_jobs_are_explicitly_opt_in_and_patch_no_tokens() -> None:
    script = LOCAL_SCRIPT.read_text()
    assert "BIFROST_KIND_BUILD_JOBS    Set to 1" in script
    assert 'if [[ "${BIFROST_KIND_BUILD_JOBS:-}" != "1" ]]; then' in script
    assert 'kubectl_local apply -f "${ROOT_DIR}/k8s/local/build-jobs.yaml"' in script
    assert "kubectl_local set env deployment/bifrost-api" not in script
    assert "kubectl_local set env deployment/bifrost-scheduler" not in script
    assert "kubectl_local patch deployment/bifrost-scheduler" not in script
    assert "kubectl_local patch deployment/bifrost-worker" not in script


def test_local_renderer_replaces_the_init_job_image(tmp_path) -> None:
    image = "bifrost-api:test-render"
    script = LOCAL_SCRIPT.read_text()
    start = script.index("render_with_image() {")
    end = script.index("\n}\n", start) + 3
    renderer = script[start:end]
    output = tmp_path / "init.yaml"
    subprocess.run(
        [
            "bash",
            "-c",
            renderer + '\nIMAGE_NAME="$1"; render_with_image "$2" "$3"',
            "renderer-test",
            image,
            str(REPO_ROOT / "k8s" / "local" / "init.yaml"),
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    [job] = _load_yaml(output)
    assert {c["image"] for c in job["spec"]["template"]["spec"]["containers"]} == {image}


def test_shipped_kubernetes_configuration_sets_only_variables_the_api_reads() -> None:
    """A fresh install must not boot into the "no longer used" notice, and
    nothing shipped may use a renamed variable's old name."""
    shipped: dict[str, set[str]] = {}
    for path in (
        REPO_ROOT / "k8s" / "configmap.yaml",
        REPO_ROOT / "k8s" / "secret.yaml",
        ELASTIC_BUILDS_DIR / "build-config.yaml",
        REPO_ROOT / "k8s" / "local" / "config.yaml",
        LOCAL_BUILD_JOBS,
    ):
        for doc in _load_yaml(path):
            if doc["kind"] in ("ConfigMap", "Secret"):
                shipped[f"{path.name}:{doc['metadata']['name']}"] = {
                    *(doc.get("data") or {}),
                    *(doc.get("stringData") or {}),
                }
    assert len(shipped) == 6  # local config.yaml holds a ConfigMap and a Secret

    for source, names in shipped.items():
        environ = dict.fromkeys(names, "set")
        assert unread_environment_variables(environ, broker_drained=True) == [], source
        assert renamed_environment_variables(environ) == [], source


def test_base_manifests_let_only_the_api_manage_worker_jobs() -> None:
    root_kustomization = yaml.safe_load((REPO_ROOT / "k8s" / "kustomization.yml").read_text())
    assert "api/rbac.yaml" in root_kustomization["resources"]
    docs = _docs_by_kind_name([REPO_ROOT / "k8s" / "api" / "rbac.yaml"])
    role = docs[("Role", "bifrost-worker-controller")]
    assert role["rules"] == [
        {
            "apiGroups": ["batch"],
            "resources": ["jobs"],
            "verbs": ["get", "list", "create", "patch", "delete"],
        },
        {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list", "patch"]},
    ]
    binding = docs[("RoleBinding", "bifrost-worker-controller")]
    assert binding["subjects"] == [
        {"kind": "ServiceAccount", "name": "bifrost-api", "namespace": "bifrost"}
    ]

    def pod(name: str) -> dict[str, Any]:
        [deployment] = _load_yaml(REPO_ROOT / "k8s" / name / "deployment.yaml")
        return deployment["spec"]["template"]["spec"]

    assert pod("api")["serviceAccountName"] == "bifrost-api"
    assert pod("api")["automountServiceAccountToken"] is True
    assert pod("scheduler")["automountServiceAccountToken"] is False
    assert pod("worker")["automountServiceAccountToken"] is False


def test_base_scheduler_runs_the_executor_with_the_workers_posture() -> None:
    [scheduler] = _load_yaml(REPO_ROOT / "k8s" / "scheduler" / "deployment.yaml")
    [worker] = _load_yaml(REPO_ROOT / "k8s" / "worker" / "deployment.yaml")
    scheduler_pod = scheduler["spec"]["template"]["spec"]
    worker_pod = worker["spec"]["template"]["spec"]
    scheduler_container = scheduler_pod["containers"][0]
    worker_container = worker_pod["containers"][0]

    assert scheduler_container["command"] == ["python", "-m", "src.scheduler.main"]
    assert scheduler_container["securityContext"] == worker_container["securityContext"]
    assert scheduler_container["resources"] == worker_container["resources"]
    assert scheduler_pod["volumes"] == worker_pod["volumes"]
    assert scheduler_pod["terminationGracePeriodSeconds"] == worker_pod["terminationGracePeriodSeconds"]
    # The scheduler's own probes stay: the entry point keeps its health files.
    assert "src.scheduler.health" in scheduler_container["livenessProbe"]["exec"]["command"]
    assert "src.scheduler.health" in scheduler_container["readinessProbe"]["exec"]["command"]


def test_local_runtime_renderer_gives_the_api_the_build_config(tmp_path) -> None:
    script = LOCAL_SCRIPT.read_text()
    start = script.index("render_runtime_manifest() {")
    end = script.index("\n}\n", start) + 3
    output = tmp_path / "runtime.yaml"
    subprocess.run(
        [
            "bash",
            "-c",
            (
                script[start:end]
                + '\nIMAGE_NAME="img"; BIFROST_KIND_BUILD_JOBS=1; '
                + 'render_runtime_manifest "$1" "$2" rev'
            ),
            "renderer-test",
            str(REPO_ROOT / "k8s" / "local" / "bifrost.yaml"),
            str(output),
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
    build_config = {"configMapRef": {"name": "bifrost-build-jobs-config"}}
    api = deployments["bifrost-api"]["spec"]["template"]["spec"]
    assert api["serviceAccountName"] == "bifrost-api"
    assert build_config in api["containers"][0]["envFrom"]
    for name in ("bifrost-scheduler", "bifrost-worker"):
        pod = deployments[name]["spec"]["template"]["spec"]
        assert pod["automountServiceAccountToken"] is False
        assert build_config not in pod["containers"][0]["envFrom"]
