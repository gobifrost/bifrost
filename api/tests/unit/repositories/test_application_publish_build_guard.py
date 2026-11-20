from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.orm.applications import Application
from src.jobs.platform.base import PlatformJobCancelled
from src.repositories.applications import (
    ApplicationPublishSnapshot,
    ApplicationRepository,
)
from src.services.app_bundler import BundleManifest, BundleResult


@pytest.mark.asyncio
async def test_changed_source_snapshot_never_promotes_old_bundle(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_id = uuid4()
    app = Application(
        id=app_id,
        name="Changed",
        slug=f"changed-{uuid4().hex[:8]}",
        repo_path="apps/changed",
        app_model="inline_v1",
    )
    db_session.add(app)
    await db_session.commit()

    snapshot = ApplicationPublishSnapshot(
        application_id=app_id,
        organization_id=None,
        repo_prefix="apps/changed/",
        dependencies={},
        source_files={"pages/index.tsx": b"export default () => null"},
        source_versions={"pages/index.tsx": "old-source"},
    )
    bundle = BundleResult(
        success=True,
        manifest=BundleManifest(
            entry="entry.js", css=None, outputs=["entry.js"],
            duration_ms=1, warnings=[], dependencies={},
        ),
    )
    repo = ApplicationRepository(db_session, None, is_superuser=True)
    publish = AsyncMock()
    monkeypatch.setattr("src.services.app_storage.AppStorageService.publish", publish)
    monkeypatch.setattr(
        "src.services.app_bundler.BundlerService.source_versions",
        AsyncMock(return_value={"pages/index.tsx": "new-source"}),
    )

    with pytest.raises(ValueError, match="changed during publish"):
        await repo.promote_publish_snapshot(
            snapshot,
            bundle,
            {"entry.js": b"export {}"},
            effect_authorization=lambda _db: _authorized(),
        )

    await db_session.refresh(app)
    assert app.published_at is None
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_dependencies_never_promote_old_bundle(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_id = uuid4()
    app = Application(
        id=app_id,
        name="Changed dependencies",
        slug=f"changed-deps-{uuid4().hex[:8]}",
        repo_path="apps/changed-deps",
        dependencies={"zod": "^4.0.0"},
        app_model="inline_v1",
    )
    db_session.add(app)
    await db_session.commit()

    snapshot = ApplicationPublishSnapshot(
        application_id=app_id,
        organization_id=None,
        repo_prefix="apps/changed-deps/",
        dependencies={"zod": "^3.0.0"},
        source_files={"pages/index.tsx": b"export default () => null"},
        source_versions={"pages/index.tsx": "same-source"},
    )
    bundle = BundleResult(
        success=True,
        manifest=BundleManifest(
            entry="entry.js", css=None, outputs=["entry.js"],
            duration_ms=1, warnings=[], dependencies=snapshot.dependencies,
        ),
    )
    repo = ApplicationRepository(db_session, None, is_superuser=True)
    publish = AsyncMock()
    monkeypatch.setattr("src.services.app_storage.AppStorageService.publish", publish)
    monkeypatch.setattr(
        "src.services.app_bundler.BundlerService.source_versions",
        AsyncMock(return_value={"pages/index.tsx": "same-source"}),
    )

    with pytest.raises(ValueError, match="changed during publish"):
        await repo.promote_publish_snapshot(
            snapshot,
            bundle,
            {"entry.js": b"export {}"},
                effect_authorization=lambda _db: _authorized(),
        )
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_repo_path_never_promotes_old_bundle(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_id = uuid4()
    app = Application(
        id=app_id,
        name="Moved source",
        slug=f"moved-source-{uuid4().hex[:8]}",
        repo_path="apps/current-source",
        app_model="inline_v1",
    )
    db_session.add(app)
    await db_session.commit()

    snapshot = _snapshot(app_id, "apps/previous-source/", {})
    repo = ApplicationRepository(db_session, None, is_superuser=True)
    publish = AsyncMock()
    monkeypatch.setattr("src.services.app_storage.AppStorageService.publish", publish)
    monkeypatch.setattr(
        "src.services.app_bundler.BundlerService.source_versions",
        AsyncMock(return_value=snapshot.source_versions),
    )

    with pytest.raises(ValueError, match="changed during publish"):
        await repo.promote_publish_snapshot(
            snapshot,
            _bundle({}),
            {"entry.js": b"export {}"},
                effect_authorization=lambda _db: _authorized(),
        )

    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_job_authority_never_promotes_live_manifest(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_id = uuid4()
    app = Application(
        id=app_id,
        name="Stale owner",
        slug=f"stale-owner-{uuid4().hex[:8]}",
        repo_path="apps/stale-owner",
        app_model="inline_v1",
    )
    db_session.add(app)
    await db_session.commit()

    snapshot = _snapshot(app_id, "apps/stale-owner/", {})
    repo = ApplicationRepository(db_session, None, is_superuser=True)
    write_preview = AsyncMock()
    publish = AsyncMock()
    monkeypatch.setattr(
        "src.services.app_bundler.BundlerService.write_compilation", write_preview
    )
    monkeypatch.setattr("src.services.app_storage.AppStorageService.publish", publish)
    monkeypatch.setattr(
        "src.services.app_bundler.BundlerService.source_versions",
        AsyncMock(return_value=snapshot.source_versions),
    )

    async def stale_owner(_db: object) -> bool:
        return False

    with pytest.raises(PlatformJobCancelled):
        await repo.promote_publish_snapshot(
            snapshot,
            _bundle({}),
            {"entry.js": b"export {}"},
            effect_authorization=stale_owner,
        )

    write_preview.assert_not_awaited()
    publish.assert_not_awaited()


def _snapshot(
    app_id, repo_prefix: str, dependencies: dict[str, str]
) -> ApplicationPublishSnapshot:
    return ApplicationPublishSnapshot(
        application_id=app_id,
        organization_id=None,
        repo_prefix=repo_prefix,
        dependencies=dependencies,
        source_files={"pages/index.tsx": b"export default () => null"},
        source_versions={"pages/index.tsx": "source-version"},
    )


def _bundle(dependencies: dict[str, str]) -> BundleResult:
    return BundleResult(
        success=True,
        manifest=BundleManifest(
            entry="entry.js", css=None, outputs=["entry.js"],
            duration_ms=1, warnings=[], dependencies=dependencies,
        ),
    )


async def _authorized() -> bool:
    return True
