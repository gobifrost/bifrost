from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.routers.applications import delete_application


@pytest.mark.asyncio
async def test_delete_application_removes_all_retained_application_artifacts() -> None:
    app_id = uuid4()
    active_deployment_id = uuid4()
    application = SimpleNamespace(
        id=app_id,
        name="Retained Artifacts App",
        slug="retained-artifacts-app",
        active_deployment_id=active_deployment_id,
        organization_id=uuid4(),
    )

    ctx = MagicMock()
    ctx.db = MagicMock()
    ctx.org_id = uuid4()
    ctx.db.commit = AsyncMock()

    user = SimpleNamespace(
        user_id=uuid4(),
        is_platform_admin=True,
        is_external=False,
    )

    source_storage = MagicMock()
    source_storage.delete_application_artifacts = AsyncMock()
    builder = MagicMock()
    builder.delete_deployment = AsyncMock()

    with (
        patch("src.routers.applications.assert_entity_id_not_solution_managed", new=AsyncMock()),
        patch(
            "src.routers.applications.get_application_by_id_or_404",
            new=AsyncMock(return_value=application),
        ),
        patch("src.routers.applications.ApplicationRepository") as repo_type,
        patch(
            "src.routers.applications.ApplicationSourceArtifactStorage",
            return_value=source_storage,
        ),
        patch("src.services.solutions.app_build.SolutionAppBuilder", return_value=builder),
    ):
        repo_type.return_value.delete_application = AsyncMock(return_value=True)

        await delete_application(app_id, ctx, user)

    ctx.db.commit.assert_awaited_once()
    source_storage.delete_application_artifacts.assert_awaited_once_with(app_id)
    builder.delete_deployment.assert_awaited_once_with(app_id, active_deployment_id)
