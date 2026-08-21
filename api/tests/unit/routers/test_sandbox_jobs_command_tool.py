import inspect
from types import SimpleNamespace

import pytest

from src.models.contracts.sandbox_runner import SandboxRunnerConfigPublic
from src.models.contracts.sandbox_runner import SandboxBuilderToolDefinition
from src.routers import sandbox_jobs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "expected_command_tool"),
    [("cloudflare", True), ("local", False)],
)
async def test_cloudflare_workspace_command_is_provider_gated(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    expected_command_tool: bool,
) -> None:
    class _FakeRunnerConfigService:
        def __init__(self, _db) -> None:
            pass

        async def get_config(self) -> SandboxRunnerConfigPublic:
            return SandboxRunnerConfigPublic(
                provider=provider,
                enabled=True,
                provisioned=True,
                connected=True,
            )

    monkeypatch.setattr(
        sandbox_jobs,
        "SandboxRunnerConfigService",
        _FakeRunnerConfigService,
    )

    definitions = [
        SandboxBuilderToolDefinition(
            name="list_files",
            description="List files",
            parameters={"type": "object", "properties": {}},
            execution="sandbox",
        )
    ]
    definitions = await sandbox_jobs._with_cloudflare_workspace_command(
        SimpleNamespace(),
        definitions,
    )
    assert (
        any(
            definition.name == "execute_command"
            for definition in definitions
        )
        is expected_command_tool
    )


def test_tool_start_applies_provider_tools_before_authorizing_name() -> None:
    source = inspect.getsource(sandbox_jobs.start_turn_tool)
    provider_tools = source.index("_with_cloudflare_workspace_command")
    tool_lookup = source.index("definition = next")

    assert provider_tools < tool_lookup
