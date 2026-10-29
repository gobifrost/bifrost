"""Integration create responses are not sent before their row is durable."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models import IntegrationCreate
from src.routers.integrations import create_integration
from tests.helpers.authorization import admin_caller


@pytest.mark.asyncio
async def test_create_integration_commits_before_returning_response() -> None:
    events: list[str] = []
    integration = MagicMock()
    integration.name = "Commit Boundary"
    integration.logo_thumbnail_version = None
    integration.logo_content_type = None
    response = MagicMock()

    ctx = MagicMock()
    ctx.db = MagicMock()

    async def commit() -> None:
        events.append("commit")

    ctx.db.commit = AsyncMock(side_effect=commit)

    def serialize(_integration):
        events.append("serialize")
        return response

    with (
        patch("src.routers.integrations.IntegrationsRepository") as repo_type,
        patch(
            "src.routers.integrations._global_definition_writer",
            new=AsyncMock(return_value=admin_caller()),
        ),
        patch(
            "src.routers.integrations.IntegrationResponse.model_validate",
            side_effect=serialize,
        ),
    ):
        repo_type.return_value.get_integration_by_name = AsyncMock(return_value=None)
        repo_type.return_value.create_integration = AsyncMock(
            return_value=integration
        )
        result = await create_integration(
            IntegrationCreate(name="Commit Boundary"),
            ctx,
            MagicMock(),
        )

    assert result is response
    assert events == ["serialize", "commit"]
    ctx.db.commit.assert_awaited_once()
