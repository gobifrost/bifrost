"""Provisioning tests for the admin-managed sandbox runner."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.jobs.platform.base import PlatformJobContext
from src.services import sandbox_runner_provisioning as provisioning


def test_cloudflare_worker_is_an_es_module() -> None:
    source = (
        Path(provisioning.__file__).with_name("cloudflare_runner") / "worker.mjs"
    ).read_text(encoding="utf-8")

    assert "export default" in source
    assert 'NonRetryableError } from "cloudflare:workflows"' in source
    assert "reportTerminalWorkflowFailure" in source


def test_cloudflare_worker_reattaches_to_one_background_runner_process() -> None:
    source = (
        Path(provisioning.__file__).with_name("cloudflare_runner") / "worker.mjs"
    ).read_text(encoding="utf-8")

    assert "keepAlive: true" in source
    assert "sandbox.startProcess(" in source
    assert "sandbox.getProcess(RUNNER_PROCESS_ID)" in source
    assert "processId: RUNNER_PROCESS_ID" in source
    assert "autoCleanup: false" in source
    assert source.count("sandbox.exec(") == 1  # The short setup probe only.


def test_configured_runner_image_uses_release_version(monkeypatch):
    monkeypatch.setattr(
        provisioning,
        "get_settings",
        lambda: SimpleNamespace(
            builder_runner_image_repository="ghcr.io/gobifrost/bifrost-build",
            builder_runner_image_tag=None,
        ),
    )
    monkeypatch.setattr(provisioning, "get_version", lambda: "v2.4.1")

    assert (
        provisioning.configured_runner_image()
        == "ghcr.io/gobifrost/bifrost-build:2.4.1"
    )


@pytest.mark.asyncio
async def test_cloudflare_probe_uses_object_params_and_bounded_instance_id(
    monkeypatch,
):
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "success": True,
        "result": {"id": "cloudflare-workflow-instance"},
    }
    client = AsyncMock()
    client.post.return_value = response
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=client)
    manager.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(provisioning.httpx, "AsyncClient", lambda **_: manager)

    result = await provisioning._start_cloudflare_probe(
        "account-123",
        "secret-token",
        "bifrost-workflow",
    )

    assert result == "cloudflare-workflow-instance"
    request = client.post.await_args
    instance_id = request.kwargs["json"]["instance_id"]
    assert instance_id.startswith("bifrost-probe-")
    assert len(instance_id) == len("bifrost-probe-") + 32
    assert request.kwargs["json"]["params"] == {
        "mode": "probe",
        "probe_id": instance_id,
    }


@pytest.mark.asyncio
async def test_cloudflare_provision_marks_connected_only_after_probe(monkeypatch):
    context = SimpleNamespace(report=AsyncMock())
    verify = AsyncMock()
    deploy = AsyncMock()
    start = AsyncMock(return_value="probe-123")
    wait = AsyncMock()
    status = AsyncMock()
    monkeypatch.setattr(provisioning, "_verify_cloudflare_account", verify)
    monkeypatch.setattr(provisioning, "_deploy_cloudflare_worker", deploy)
    monkeypatch.setattr(provisioning, "_start_cloudflare_probe", start)
    monkeypatch.setattr(provisioning, "_wait_for_cloudflare_probe", wait)
    monkeypatch.setattr(provisioning, "_set_runtime_status", status)

    result = await provisioning._provision_cloudflare(
        cast(PlatformJobContext, context),
        {
            "cloudflare": {
                "account_id": "acct-123",
                "api_token": "secret-token",
                "script_name": "bifrost-runner",
                "workflow_name": "bifrost-workflow",
            }
        },
    )

    assert result == {"external_run_id": "probe-123"}
    assert status.await_args_list[0].kwargs == {
        "provisioned": True,
        "connected": False,
    }
    assert status.await_args_list[1].kwargs == {
        "provisioned": True,
        "connected": True,
    }
    wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_wrangler_config_uses_private_worker_and_public_versioned_image(
    tmp_path: Path,
    monkeypatch,
):
    wrangler = tmp_path / "wrangler"
    node = tmp_path / "node22"
    bundle = tmp_path / "worker.js"
    wrangler.write_text("stub", encoding="utf-8")
    node.write_text("stub", encoding="utf-8")
    bundle.write_text("export default {}", encoding="utf-8")
    monkeypatch.setattr(provisioning, "_WRANGLER", wrangler)
    monkeypatch.setattr(provisioning, "_WRANGLER_NODE", node)
    monkeypatch.setattr(provisioning, "_WORKER_BUNDLE", bundle)
    monkeypatch.setattr(
        provisioning,
        "configured_runner_image",
        lambda: "ghcr.io/gobifrost/bifrost-build:2.4.1",
    )
    captured: dict[str, object] = {}

    class Process:
        returncode = 0

        async def communicate(self):
            return b"deployed", None

    async def create_process(*args, **kwargs):
        config_path = Path(args[4])
        captured["args"] = args
        captured["config"] = config_path.read_text(encoding="utf-8")
        captured["env"] = kwargs["env"]
        return Process()

    monkeypatch.setattr(
        provisioning.asyncio,
        "create_subprocess_exec",
        create_process,
    )

    await provisioning._deploy_cloudflare_worker(
        account_id="acct-123",
        api_token="secret-token",
        script_name="bifrost-runner",
        workflow_name="bifrost-workflow",
    )

    config = cast(dict[str, Any], json.loads(str(captured["config"])))
    assert config["workers_dev"] is False
    assert config["observability"] == {"enabled": True}
    assert config["containers"] == [
        {
            "class_name": "Sandbox",
            "image": "ghcr.io/gobifrost/bifrost-build:2.4.1",
            "instance_type": "basic",
            "max_instances": 20,
        }
    ]
    assert config["workflows"][0]["name"] == "bifrost-workflow"
    assert "secret-token" not in str(captured["args"])
    assert "secret-token" not in str(captured["config"])
    args = cast(tuple[Any, ...], captured["args"])
    assert args[-2:] == ("--containers-rollout", "immediate")
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["CLOUDFLARE_API_TOKEN"] == "secret-token"
