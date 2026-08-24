from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.models.contracts.cli import CLIAICompleteRequest


@pytest.mark.asyncio
async def test_ai_complete_resolves_requested_profile_name(monkeypatch):
    from src.routers.cli import cli_ai_complete
    from src.services import llm

    db = AsyncMock()
    llm_client = AsyncMock()
    llm_client.provider_name = "openai"
    llm_client.model_name = "gpt-5"
    llm_client.complete.return_value = SimpleNamespace(
        content="Done",
        input_tokens=1,
        output_tokens=1,
        cache_read_tokens=0,
        cache_write_tokens=0,
        provider_cost=None,
        model="gpt-5",
    )
    get_llm_client = AsyncMock(return_value=llm_client)
    monkeypatch.setattr(llm, "get_llm_client", get_llm_client)

    response = await cli_ai_complete(
        CLIAICompleteRequest(
            messages=[{"role": "user", "content": "Hello"}],
            profile="Reasoning",
        ),
        SimpleNamespace(user_id=None),
        db,
    )

    assert response.content == "Done"
    get_llm_client.assert_awaited_once_with(db, profile_name="Reasoning")
