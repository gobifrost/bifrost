from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.orm.applications import Application
from src.repositories.applications import ApplicationRepository
from src.services.app_bundler import BundleMessage, BundleResult


@pytest.mark.asyncio
async def test_failed_bundle_never_promotes_preview(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = Application(
        id=uuid4(),
        name="Broken",
        slug=f"broken-{uuid4().hex[:8]}",
        repo_path=f"apps/broken-{uuid4().hex[:8]}",
        app_model="inline_v1",
    )
    db_session.add(app)
    await db_session.commit()

    async def failed_build(*_args, **_kwargs):
        return (
            BundleResult(
                success=False,
                errors=[BundleMessage(text="Could not resolve import")],
            ),
            [],
        )

    from src.services import app_bundler
    from src.services import app_storage

    promote = AsyncMock()
    monkeypatch.setattr(app_bundler, "build_with_migrate", failed_build)
    monkeypatch.setattr(app_storage.AppStorageService, "publish", promote)
    repo = ApplicationRepository(
        db_session,
        None,
        is_superuser=True,
    )

    with pytest.raises(ValueError, match="Could not resolve import"):
        await repo.publish(app.id, "dev@example.com")

    promote.assert_not_awaited()
    await db_session.refresh(app)
    assert app.published_at is None
