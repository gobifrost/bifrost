"""Application create responses are not sent before their row is durable."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models.contracts.applications import ApplicationCreate
from src.routers.applications import create_application


@pytest.mark.asyncio
async def test_create_application_commits_before_returning_response() -> None:
    events: list[str] = []
    application = MagicMock()
    response = MagicMock()

    ctx = MagicMock()
    ctx.org_id = uuid4()
    ctx.db = MagicMock()

    async def commit() -> None:
        events.append("commit")

    ctx.db.commit = AsyncMock(side_effect=commit)

    user = MagicMock()
    user.user_id = uuid4()
    user.email = "admin@example.com"
    user.is_platform_admin = True
    user.is_external = False

    async def to_public(*_args, **_kwargs):
        events.append("serialize")
        return response

    with (
        patch("src.routers.applications.ApplicationRepository") as repo_type,
        patch(
            "src.routers.applications.application_to_public",
            new=AsyncMock(side_effect=to_public),
        ),
    ):
        repo_type.return_value.create_application = AsyncMock(
            return_value=application
        )
        result = await create_application(
            ApplicationCreate(
                name="Commit Boundary",
                slug="commit-boundary",
                app_model="inline_v1",
            ),
            ctx,
            user,
        )

    assert result is response
    assert events == ["serialize", "commit"]
    ctx.db.commit.assert_awaited_once()
