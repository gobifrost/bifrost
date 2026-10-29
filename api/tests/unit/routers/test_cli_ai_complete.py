from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.contracts.cli import CLIAICompleteRequest
from src.core.principal import UserPrincipal


@pytest.mark.asyncio
async def test_ai_complete_resolves_requested_profile_name(monkeypatch):
    from src.routers.cli import cli_ai_complete

    db = AsyncMock()
    complete = AsyncMock(return_value={
        "content": "Done",
        "input_tokens": 1,
        "output_tokens": 1,
        "model": "gpt-5",
    })
    monkeypatch.setattr("shared.sdk_ai.complete_sdk_ai", complete)
    user = UserPrincipal(user_id=uuid4(), email="ai@example.test", organization_id=None)

    response = await cli_ai_complete(
        CLIAICompleteRequest(
            messages=[{"role": "user", "content": "Hello"}],
            profile="Reasoning",
        ),
        user,
        db,
    )

    assert response.content == "Done"
    assert complete.await_args.kwargs["profile"] == "Reasoning"
