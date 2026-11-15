"""Floci adapters all use the shared external-worker environment contract."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from shared.worker_control_auth import external_worker_control_key


CONTROL_TOKEN = "c" * 32
WORKER_ID = UUID("12345678-1234-5678-1234-567812345678")
RESOURCE_UID = "resource-123"


def _deploy_root() -> Path:
    repository_deploy = Path(__file__).resolve().parents[3] / "deploy"
    return repository_deploy if repository_deploy.is_dir() else Path("/deploy")


def _load_adapter(provider: str) -> Any:
    path = _deploy_root() / "capacity" / "providers" / "floci" / provider / "capacity.py"
    spec = importlib.util.spec_from_file_location(f"floci_{provider}_capacity", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _adapter(module: Any, *, env: dict[str, str]) -> Any:
    adapter = object.__new__(module.Adapter)
    adapter.control_token = CONTROL_TOKEN
    adapter.env = env
    adapter.image = "example/worker:latest"
    adapter.command = ["python", "-m", "src.worker.main"]
    if module.__name__.endswith("aws_capacity"):
        adapter.entrypoint = ["python"]
        adapter.memory_mib = 4096
        adapter.cpu_units = 2048
    elif module.__name__.endswith("azure_capacity"):
        adapter.cpu = 2.0
        adapter.memory_gb = 4.0
    else:
        adapter.args = ["-m", "src.worker.main"]
        adapter.cpu = "2000m"
        adapter.memory = "4096Mi"
    return adapter


def _environment(provider: str, container: dict[str, Any]) -> dict[str, str]:
    if provider == "aws":
        values = container["environment"]
    elif provider == "azure":
        values = container["properties"]["environmentVariables"]
    else:
        values = container["env"]
    return {item["name"]: item["value"] for item in values}


def _context(module: Any) -> Any:
    return module.Context(
        scope="capacity",
        worker_id=str(WORKER_ID),
        resource_uid=RESOURCE_UID,
        operation_id="operation-123",
    )


def test_floci_adapters_derive_one_scoped_worker_control_key() -> None:
    expected_key = external_worker_control_key(CONTROL_TOKEN, WORKER_ID, RESOURCE_UID)
    environments: list[dict[str, str]] = []

    for provider in ("aws", "azure", "gcp"):
        module = _load_adapter(provider)
        adapter = _adapter(module, env={"BIFROST_API_URL": "https://api.example.test"})
        environments.append(_environment(provider, adapter._container(_context(module))))

    assert [env["BIFROST_WORKER_CONTROL_SECRET"] for env in environments] == [
        expected_key,
        expected_key,
        expected_key,
    ]
    assert all(env["BIFROST_WORKER_INCARNATION_ID"] == str(WORKER_ID) for env in environments)
    assert all(env["BIFROST_WORKER_RESOURCE_UID"] == RESOURCE_UID for env in environments)
    assert all(env["BIFROST_WORKER_FORM"] == "external" for env in environments)


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
def test_floci_adapter_rejects_privileged_worker_environment(provider: str) -> None:
    module = _load_adapter(provider)
    adapter = _adapter(module, env={"BIFROST_DATABASE_URL": "postgresql://secret"})

    with pytest.raises(ValueError, match="cannot receive platform credentials"):
        adapter._container(_context(module))
