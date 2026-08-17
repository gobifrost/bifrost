from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services import sandbox_runner_provisioning as provisioning
from src.jobs.platform.registry import get_platform_job_definition
from src.jobs.platform.sandbox_runner_provision import SANDBOX_RUNNER_PROVISION_DEFINITION


class _Process:
    def __init__(self, output: bytes = b"", returncode: int = 0) -> None:
        self.output = output
        self.returncode = returncode
        self.input: bytes | None = None

    async def communicate(self, input: bytes | None = None):
        self.input = input
        return self.output, None


def test_sandbox_runner_provision_handler_is_registered() -> None:
    assert (
        get_platform_job_definition(SANDBOX_RUNNER_PROVISION_DEFINITION.job_type)
        is SANDBOX_RUNNER_PROVISION_DEFINITION
    )


@pytest.mark.asyncio
async def test_local_runner_needs_no_provider_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Redis:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _key: str) -> bytes:
            return b"ready"

        async def delete(self, _key: str) -> None:
            return None

    publish = AsyncMock()
    set_status = AsyncMock()
    monkeypatch.setattr("src.jobs.rabbitmq.publish_message", publish)
    monkeypatch.setattr("src.core.cache.redis_client.get_redis", _Redis)
    monkeypatch.setattr(provisioning, "_set_runtime_status", set_status)
    context = SimpleNamespace(report=AsyncMock())

    result = await provisioning._provision_local(context, {"local": None})

    assert result == {"uses_existing_worker": True}
    publish.assert_awaited_once()
    set_status.assert_awaited_once_with(provisioned=True, connected=True)


@pytest.mark.asyncio
async def test_runner_image_is_mirrored_with_short_lived_cloudflare_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials = _Process(
        b'{"username":"temporary-user","password":"temporary-password"}'
    )
    login = _Process()
    copy = _Process()
    create_process = AsyncMock(side_effect=[credentials, login, copy])
    monkeypatch.setattr(provisioning, "_CRANE", Path("/bin/true"))
    monkeypatch.setattr(provisioning, "_WRANGLER_NODE", Path("/bin/true"))
    monkeypatch.setattr(provisioning, "_WRANGLER", Path("/bin/true"))
    monkeypatch.setattr(
        provisioning,
        "configured_runner_image",
        lambda: "ghcr.io/gobifrost/bifrost-build:1.2.3",
    )
    monkeypatch.setattr(
        provisioning.asyncio,
        "create_subprocess_exec",
        create_process,
    )

    destination = await provisioning._mirror_runner_image_to_cloudflare(
        account_id="a" * 32,
        api_token="secret-cloudflare-token",
    )

    assert destination == (
        "registry.cloudflare.com/"
        + "a" * 32
        + "/bifrost-build:1.2.3"
    )
    credential_args = create_process.await_args_list[0].args
    assert "--push" in credential_args
    assert "--json" in credential_args
    assert "secret-cloudflare-token" not in credential_args
    assert login.input == b"temporary-password"
    copy_args = create_process.await_args_list[2].args
    assert copy_args[-2:] == (
        "ghcr.io/gobifrost/bifrost-build:1.2.3",
        destination,
    )


@pytest.mark.asyncio
async def test_cloudflare_deploy_uses_private_image_and_disables_ssh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def create_process(*args, **_kwargs):
        config_path = Path(args[args.index("--config") + 1])
        import json

        captured.update(json.loads(config_path.read_text(encoding="utf-8")))
        return _Process()

    monkeypatch.setattr(provisioning, "_WRANGLER_NODE", Path("/bin/true"))
    monkeypatch.setattr(provisioning, "_WRANGLER", Path("/bin/true"))
    monkeypatch.setattr(provisioning, "_WORKER_BUNDLE", Path("/bin/true"))
    monkeypatch.setattr(
        provisioning.asyncio,
        "create_subprocess_exec",
        create_process,
    )

    await provisioning._deploy_cloudflare_worker(
        account_id="a" * 32,
        api_token="secret-cloudflare-token",
        script_name="bifrost-build-test",
        workflow_name="bifrost-build-test-workflow",
        runner_image=(
            "registry.cloudflare.com/"
            + "a" * 32
            + "/bifrost-build:1.2.3"
        ),
    )

    container = captured["containers"][0]  # type: ignore[index]
    assert container["image"].startswith("registry.cloudflare.com/")
    assert container["ssh"] == {"enabled": False}
