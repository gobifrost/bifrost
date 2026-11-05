"""Every physical executor connects to the API remote-control host."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml


API_URL = "http://api:8000"


def _find_repo_file(path: str) -> Path:
    """Locate a narrow test-runner mount or the repository-host file.

    The runner mounts source, Kubernetes manifests, and individual Compose
    files instead of the repository root.  Keep this test usable both there
    and when invoked from a worktree.
    """
    candidates = [
        Path("/app") / path,
        Path(__file__).resolve().parents[3] / path,
    ]
    if path.startswith("api/"):
        candidates.insert(1, Path("/app") / path.removeprefix("api/"))
    if path.startswith("k8s/"):
        candidates.insert(1, Path("/") / path)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"{path} not found in deployment contract test mounts")


def _compose_service(path: str, service: str) -> dict[str, Any]:
    return yaml.safe_load(_find_repo_file(path).read_text())["services"][service]


def _deployment_container(path: str, name: str) -> dict[str, Any]:
    for document in yaml.safe_load_all(_find_repo_file(path).read_text()):
        if document and document.get("kind") == "Deployment" and document["metadata"]["name"] == name:
            return document["spec"]["template"]["spec"]["containers"][0]
    raise AssertionError(f"Deployment {name} was not found in {path}")


def test_compose_executors_keep_their_health_entrypoints_and_explicit_api_url() -> None:
    for service, command in (
        ("worker", ["python", "-m", "src.worker.main"]),
        ("scheduler", ["python", "-m", "src.scheduler.main"]),
    ):
        runtime = _compose_service("docker-compose.test.yml", service)
        assert runtime["command"] == command
        assert runtime["environment"]["BIFROST_API_URL"] == API_URL

    worker = _compose_service("docker-compose.yml", "worker")
    assert worker["command"] == "python -m src.worker.main"
    assert worker["environment"]["BIFROST_API_URL"] == API_URL

    for service, entrypoint in (("worker", "src.worker.main"), ("scheduler", "src.scheduler.main")):
        runtime = _compose_service("docker-compose.dev.yml", service)
        assert f"python -m {entrypoint}" in runtime["command"]
        assert runtime["environment"]["BIFROST_API_URL"] == API_URL


def test_kubernetes_executors_keep_their_health_entrypoints_and_api_service() -> None:
    config = yaml.safe_load(_find_repo_file("k8s/configmap.yaml").read_text())
    assert config["data"]["BIFROST_API_URL"] == API_URL

    for path, name, command in (
        ("k8s/worker/deployment.yaml", "bifrost-worker", ["python", "-m", "src.worker.main"]),
        ("k8s/scheduler/deployment.yaml", "bifrost-scheduler", ["python", "-m", "src.scheduler.main"]),
        ("k8s/local/bifrost.yaml", "bifrost-worker", ["python", "-m", "src.worker.main"]),
        ("k8s/local/bifrost.yaml", "bifrost-scheduler", ["python", "-m", "src.scheduler.main"]),
    ):
        assert _deployment_container(path, name)["command"] == command

    local_worker = _deployment_container("k8s/local/bifrost.yaml", "bifrost-worker")
    assert local_worker["readinessProbe"]["exec"]["command"] == [
        "python",
        "-m",
        "src.worker.health",
        "--ready",
    ]


def test_executor_entrypoints_start_remote_launchers_without_the_pool_runtime() -> None:
    for entrypoint in ("api/src/worker/main.py", "api/src/scheduler/main.py"):
        source = _find_repo_file(entrypoint).read_text()
        assert "src.worker.remote.launcher" in source
        assert "src.worker.pool_main" not in source
        assert "SERVICE_ACCOUNT_TOKEN_PATH.is_file()" in source


def test_remote_control_route_precedes_the_root_mcp_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The root-mounted MCP application must not swallow launcher sockets."""
    from starlette.applications import Starlette
    from starlette.routing import Mount

    from src import main as api_main

    monkeypatch.setattr(api_main, "_get_mcp_asgi_app", lambda: Starlette())
    application = api_main.create_app()
    remote_route = next(
        index
        for index, route in enumerate(application.routes)
        if route.path == "/api/worker-control"
    )
    root_mount = next(
        index
        for index, route in enumerate(application.routes)
        if isinstance(route, Mount) and route.path == ""
    )

    assert remote_route < root_mount


def test_api_shutdown_budget_preserves_the_executor_drain_window() -> None:
    container = _deployment_container("k8s/api/deployment.yaml", "bifrost-api")
    command = container["command"]
    assert command[command.index("--timeout-graceful-shutdown") + 1] == "300"

    deployment = next(
        document
        for document in yaml.safe_load_all(_find_repo_file("k8s/api/deployment.yaml").read_text())
        if document and document.get("kind") == "Deployment"
    )
    assert deployment["spec"]["template"]["spec"]["terminationGracePeriodSeconds"] >= 310

    local_container = _deployment_container("k8s/local/bifrost.yaml", "bifrost-api")
    local_command = local_container["command"]
    assert local_command[local_command.index("--timeout-graceful-shutdown") + 1] == "300"
    local_deployment = next(
        document
        for document in yaml.safe_load_all(_find_repo_file("k8s/local/bifrost.yaml").read_text())
        if document and document.get("kind") == "Deployment"
        and document["metadata"]["name"] == "bifrost-api"
    )
    assert local_deployment["spec"]["template"]["spec"]["terminationGracePeriodSeconds"] >= 310

    compose_api = _compose_service("docker-compose.yml", "api")
    assert "--timeout-graceful-shutdown 300" in compose_api["command"]
    assert compose_api["stop_grace_period"] == "310s"
