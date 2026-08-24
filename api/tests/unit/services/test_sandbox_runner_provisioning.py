from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.services import sandbox_runner_provisioning as provisioning
from src.jobs.platform.base import PlatformJobFailure
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


class _Response:
    def __init__(self, body: dict) -> None:
        self.body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.body


class _AsyncClient:
    responses: list[_Response] = []
    requests: list[dict[str, Any]] = []

    def __init__(self, *_args, **_kwargs) -> None:
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, *args, **kwargs) -> _Response:
        self.requests.append({"args": args, "kwargs": kwargs})
        return self.responses.pop(0)


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
    assert copy_args[1:4] == ("copy", "--platform", "linux/amd64")
    assert copy_args[-2:] == (
        "ghcr.io/gobifrost/bifrost-build:1.2.3",
        destination,
    )


@pytest.mark.asyncio
async def test_runner_image_mirror_treats_exact_no_clobber_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = "registry.cloudflare.com/" + "a" * 32 + "/bifrost-build:1.2.3"
    credentials = _Process(
        b'{"username":"temporary-user","password":"temporary-password"}'
    )
    login = _Process()
    copy = _Process(
        (
            "Error: refusing to clobber existing tag "
            f"{destination}@sha256:"
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
        ).encode("utf-8"),
        returncode=1,
    )
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

    assert (
        await provisioning._mirror_runner_image_to_cloudflare(
            account_id="a" * 32,
            api_token="secret-cloudflare-token",
        )
        == destination
    )


@pytest.mark.asyncio
async def test_runner_image_mirror_wrong_no_clobber_tag_still_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong_destination = (
        "registry.cloudflare.com/" + "a" * 32 + "/bifrost-build:wrong"
    )
    credentials = _Process(
        b'{"username":"temporary-user","password":"temporary-password"}'
    )
    login = _Process()
    copy = _Process(
        (
            "Error: refusing to clobber existing tag "
            f"{wrong_destination}@sha256:"
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
        ).encode("utf-8"),
        returncode=1,
    )
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

    with pytest.raises(PlatformJobFailure) as failure:
        await provisioning._mirror_runner_image_to_cloudflare(
            account_id="a" * 32,
            api_token="secret-cloudflare-token",
        )

    assert failure.value.code == "cloudflare_registry_copy_failed"


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
    monkeypatch.setattr(provisioning, "_WORKER_SOURCE", Path("/bin/true"))
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
    assert captured["main"] == "/bin/true"
    assert container["image"] == (
        f"registry.cloudflare.com/{'a' * 32}/bifrost-build:1.2.3"
    )
    assert container["ssh"] == {"enabled": False}


@pytest.mark.asyncio
async def test_cloudflare_probe_recognizes_first_deploy_worker_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _AsyncClient.responses = [
        _Response(
            {
                "success": True,
                "result": {
                    "status": "errored",
                    "id": "probe-1",
                    "created_on": "2026-08-20T00:00:00Z",
                    "modified_on": "2026-08-20T00:00:02Z",
                },
            }
        ),
        _Response(
            {
                "success": True,
                "result": {
                    "status": "errored",
                    "step_count": 0,
                    "error": {
                        "message": "Worker not found.",
                        "name": "Error",
                    },
                },
            }
        )
    ]
    _AsyncClient.requests = []
    monkeypatch.setattr(provisioning.httpx, "AsyncClient", _AsyncClient)
    context = SimpleNamespace(report=AsyncMock())

    with pytest.raises(provisioning._CloudflareRolloutNotReady):
        await provisioning._wait_for_cloudflare_probe(
            context,
            account_id="account-id",
            api_token="token",
            workflow_name="workflow",
            probe_id="probe-1",
            deadline=provisioning.asyncio.get_running_loop().time() + 10,
        )
    assert _AsyncClient.requests[0]["kwargs"]["params"] == {"simple": "true"}
    assert "params" not in _AsyncClient.requests[1]["kwargs"]


@pytest.mark.asyncio
async def test_cloudflare_probe_recognizes_container_rollout_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _AsyncClient.responses = [
        _Response({"success": True, "result": {"status": "errored"}}),
        _Response(
            {
                "success": True,
                "result": {
                    "status": "errored",
                    "step_count": 1,
                    "error": {
                        "name": "Error",
                        "message": (
                            "NonRetryableError: SandboxError: Container is starting. "
                            "Please retry in a moment."
                        ),
                    },
                },
            }
        ),
    ]
    _AsyncClient.requests = []
    monkeypatch.setattr(provisioning.httpx, "AsyncClient", _AsyncClient)
    context = SimpleNamespace(report=AsyncMock())

    with pytest.raises(provisioning._CloudflareRolloutNotReady):
        await provisioning._wait_for_cloudflare_probe(
            context,
            account_id="account-id",
            api_token="token",
            workflow_name="workflow",
            probe_id="probe-1",
            deadline=provisioning.asyncio.get_running_loop().time() + 10,
        )


@pytest.mark.asyncio
async def test_cloudflare_first_deploy_readiness_retry_starts_fresh_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_probe = AsyncMock(side_effect=["probe-1", "probe-2"])
    wait_probe = AsyncMock(
        side_effect=[provisioning._CloudflareRolloutNotReady(), None]
    )
    sleep = AsyncMock()
    monkeypatch.setattr(
        provisioning,
        "_ROLLOUT_PROBE_BACKOFF_SECONDS",
        (0.01,),
    )
    monkeypatch.setattr(provisioning, "_start_cloudflare_probe", start_probe)
    monkeypatch.setattr(provisioning, "_wait_for_cloudflare_probe", wait_probe)
    monkeypatch.setattr(provisioning.asyncio, "sleep", sleep)
    context = SimpleNamespace(report=AsyncMock())

    probe_id = await provisioning._run_cloudflare_probe_with_readiness_retry(
        context,
        account_id="account-id",
        api_token="token",
        workflow_name="workflow",
    )

    assert probe_id == "probe-2"
    assert [call.args[2] for call in start_probe.await_args_list] == [
        "workflow",
        "workflow",
    ]
    assert wait_probe.await_count == 2
    sleep.assert_awaited_once_with(0.01)
    report_messages = [call.args[0] for call in context.report.await_args_list]
    assert any("container rollout" in message for message in report_messages)
    assert "Starting a fresh runner self-test" in report_messages


@pytest.mark.asyncio
async def test_cloudflare_first_deploy_readiness_retry_fails_closed_after_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provisioning, "_start_cloudflare_probe", AsyncMock())
    monkeypatch.setattr(
        provisioning,
        "_wait_for_cloudflare_probe",
        AsyncMock(side_effect=provisioning._CloudflareRolloutNotReady()),
    )
    monkeypatch.setattr(provisioning.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(
        provisioning,
        "_ROLLOUT_PROBE_BACKOFF_SECONDS",
        (),
    )
    context = SimpleNamespace(report=AsyncMock())

    with pytest.raises(PlatformJobFailure) as failure:
        await provisioning._run_cloudflare_probe_with_readiness_retry(
            context,
            account_id="account-id",
            api_token="token",
            workflow_name="workflow",
        )

    assert failure.value.code == "cloudflare_probe_rollout_not_ready"
